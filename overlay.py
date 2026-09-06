"""Viewport overlay for Quad Buddy.

Analysis results are cached per object and only rebuilt when the mesh, the
object transform, or a setting actually changes.
"""

import time

import bmesh
import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from . import analysis

# Rebuilds are capped to this interval so dragging vertices stays smooth. The
# overlay trails the mesh by at most this long and then catches up.
BUILD_INTERVAL = 0.12

_handle = None
_cache = {}
_stats = {}
_last_build = {}
_last_stats = {}
_epoch = 0
_refresh_pending = False

_fill_shader = None
_line_shader = None
_line_is_polyline = False


def invalidate():
    """Force every cached object to be re-analysed on the next redraw."""
    global _epoch
    _epoch += 1


def reset():
    _cache.clear()
    _stats.clear()
    _last_build.clear()
    _last_stats.clear()
    invalidate()


def _schedule_catch_up():
    """Ask for one more redraw so a throttled rebuild is not left stale."""
    global _refresh_pending
    if _refresh_pending:
        return
    _refresh_pending = True

    def _tick():
        global _refresh_pending
        _refresh_pending = False
        from .props import tag_redraw_view3d
        tag_redraw_view3d()
        return None

    try:
        bpy.app.timers.register(_tick, first_interval=BUILD_INTERVAL)
    except (ValueError, RuntimeError):
        _refresh_pending = False


def _store_stats(obj_name, signature, report):
    stats = {
        'counts': dict(report.counts),
        'over_budget': report.over_budget,
        'problem_faces': report.problem_count,
        'quad_ratio': report.quad_ratio,
    }
    _stats[obj_name] = (signature, stats)
    return stats


def ensure_stats(obj, settings):
    """Cached counts for the UI. Cheap on repeat calls, recomputed on change."""
    signature = _signature(obj, settings)
    cached = _stats.get(obj.name)
    if cached is not None:
        if cached[0] == signature:
            return cached[1]
        if time.perf_counter() - _last_stats.get(obj.name, 0.0) < BUILD_INTERVAL:
            return cached[1]
    try:
        report = analysis.analyze_object(obj, settings, geometry=False)
    except (ValueError, ReferenceError, AttributeError):
        return cached[1] if cached is not None else None
    _last_stats[obj.name] = time.perf_counter()
    return _store_stats(obj.name, signature, report)


def on_depsgraph_update(depsgraph):
    for update in depsgraph.updates:
        if update.is_updated_geometry or update.is_updated_transform:
            invalidate()
            return


# ---------------------------------------------------------------------------
# Shaders
# ---------------------------------------------------------------------------

def _shaders():
    """Resolve the builtin shaders once. Returns None when no GPU is available."""
    global _fill_shader, _line_shader, _line_is_polyline
    if _fill_shader is None:
        try:
            _fill_shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        except (ValueError, KeyError, SystemError):
            return None
    if _line_shader is None:
        try:
            _line_shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
            _line_is_polyline = True
        except (ValueError, KeyError, SystemError):
            _line_shader = _fill_shader
            _line_is_polyline = False
    return _fill_shader, _line_shader, _line_is_polyline


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _signature(obj, settings):
    mesh = obj.data
    if mesh.is_editmode:
        try:
            bm = bmesh.from_edit_mesh(mesh)
            counts = (len(bm.verts), len(bm.edges), len(bm.faces))
        except (ValueError, ReferenceError):
            counts = (0, 0, 0)
    else:
        counts = (len(mesh.vertices), len(mesh.edges), len(mesh.polygons))
    matrix = tuple(round(value, 6) for row in obj.matrix_world for value in row)
    return (_epoch, counts, matrix, settings.max_faces)


def _build_entry(obj, settings, signature):
    report = analysis.analyze_object(obj, settings)
    _store_stats(obj.name, signature, report)
    shaders = _shaders()
    if shaders is None:
        return {'fills': {}, 'lines': {}, 'points': {}}
    fill_shader, line_shader, _ = shaders

    fills = {}
    for key, points in report.fills.items():
        if points:
            fills[key] = batch_for_shader(fill_shader, 'TRIS', {"pos": points})

    lines = {}
    for key, points in report.outlines.items():
        if points:
            lines[key] = batch_for_shader(line_shader, 'LINES', {"pos": points})

    points_batches = {}
    for key, points in report.points.items():
        if points:
            points_batches[key] = batch_for_shader(fill_shader, 'POINTS', {"pos": points})

    return {
        'fills': fills,
        'lines': lines,
        'points': points_batches,
    }


def _entry_for(obj, settings):
    signature = _signature(obj, settings)
    cached = _cache.get(obj.name)
    if cached is not None:
        if cached[0] == signature:
            return cached[1]
        if time.perf_counter() - _last_build.get(obj.name, 0.0) < BUILD_INTERVAL:
            _schedule_catch_up()
            return cached[1]
    try:
        entry = _build_entry(obj, settings, signature)
    except (ValueError, ReferenceError, AttributeError):
        return cached[1] if cached is not None else None
    _cache[obj.name] = (signature, entry)
    _last_build[obj.name] = time.perf_counter()
    return entry


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def _objects_to_draw(context, settings):
    scope = settings.scope
    view_layer = context.view_layer
    active = view_layer.objects.active

    if scope == 'ACTIVE':
        candidates = [active] if active is not None else []
    elif scope == 'SELECTED':
        candidates = list(context.selected_objects)
        if active is not None and active not in candidates:
            candidates.append(active)
    else:
        candidates = list(context.visible_objects)

    result = []
    for obj in candidates:
        if obj is None or obj.type != 'MESH':
            continue
        if not obj.visible_get():
            continue
        if not settings.show_in_object_mode and not obj.data.is_editmode:
            continue
        if obj not in result:
            result.append(obj)
    return result


def _outline_color(color):
    alpha = min(1.0, color[3] * 2.4 + 0.25)
    return (color[0], color[1], color[2], alpha)


def _draw():
    context = bpy.context
    scene = context.scene
    if scene is None:
        return
    settings = getattr(scene, 'quad_buddy', None)
    if settings is None or not settings.enabled:
        return
    if context.space_data is None or context.space_data.type != 'VIEW_3D':
        return

    objects = _objects_to_draw(context, settings)
    if not objects:
        return

    shaders = _shaders()
    if shaders is None:
        return
    fill_shader, line_shader, is_polyline = shaders

    fill_colors = {
        'tri': tuple(settings.color_tri),
        'ngon': tuple(settings.color_ngon),
        'forgiven': tuple(settings.color_forgiven),
    }
    line_colors = {
        'tri': _outline_color(settings.color_tri),
        'ngon': _outline_color(settings.color_ngon),
        'forgiven': _outline_color(settings.color_forgiven),
        'nonmanifold': tuple(settings.color_nonmanifold),
        'boundary': tuple(settings.color_boundary),
    }
    point_colors = {
        'pole_high': tuple(settings.color_pole_high),
        'pole_low': tuple(settings.color_pole_low),
    }

    entries = []
    for obj in objects:
        entry = _entry_for(obj, settings)
        if entry is not None:
            entries.append(entry)
    if not entries:
        return

    gpu.state.blend_set('ALPHA')
    gpu.state.face_culling_set('NONE')
    gpu.state.depth_test_set('NONE' if settings.xray else 'LESS_EQUAL')
    gpu.state.depth_mask_set(False)

    try:
        if settings.draw_fills:
            for entry in entries:
                for key, batch in entry['fills'].items():
                    fill_shader.bind()
                    fill_shader.uniform_float("color", fill_colors[key])
                    batch.draw(fill_shader)

        if settings.draw_outlines:
            region = context.region
            for entry in entries:
                for key, batch in entry['lines'].items():
                    line_shader.bind()
                    if is_polyline:
                        line_shader.uniform_float(
                            "viewportSize", (region.width, region.height))
                        line_shader.uniform_float("lineWidth", settings.outline_width)
                    else:
                        gpu.state.line_width_set(settings.outline_width)
                    line_shader.uniform_float("color", line_colors[key])
                    batch.draw(line_shader)

        if settings.show_poles:
            gpu.state.point_size_set(settings.point_size)
            for entry in entries:
                for key, batch in entry['points'].items():
                    fill_shader.bind()
                    fill_shader.uniform_float("color", point_colors[key])
                    batch.draw(fill_shader)
    finally:
        gpu.state.point_size_set(1.0)
        gpu.state.line_width_set(1.0)
        gpu.state.depth_mask_set(True)
        gpu.state.depth_test_set('NONE')
        gpu.state.blend_set('NONE')


# ---------------------------------------------------------------------------
# Handler lifetime
# ---------------------------------------------------------------------------

def enable():
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')


def disable():
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, 'WINDOW')
        _handle = None
    reset()
