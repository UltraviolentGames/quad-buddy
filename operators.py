"""Operators for Quad Buddy: selection, navigation and guided fixes."""

from math import degrees, radians
import os

import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator

from . import analysis, cleanup, debug, overlay

_cursor = {}


def _settings(context):
    return context.scene.quad_buddy


def _mesh_objects(context):
    active = context.view_layer.objects.active
    objects = [obj for obj in context.selected_objects if obj.type == 'MESH']
    if active is not None and active.type == 'MESH' and active not in objects:
        objects.append(active)
    return objects


def _face_stats(obj):
    bm, owned = analysis.get_bmesh(obj)
    try:
        tris = 0
        ngons = 0
        for face in bm.faces:
            sides = len(face.verts)
            if sides == 3:
                tris += 1
            elif sides > 4:
                ngons += 1
        return tris, ngons
    finally:
        if owned:
            bm.free()


def _select_faces(context, obj, indices, extend=False):
    context.tool_settings.mesh_select_mode = (False, False, True)
    if not extend:
        bpy.ops.mesh.select_all(action='DESELECT')

    mesh = obj.data
    bm = bmesh.from_edit_mesh(mesh)
    bm.faces.ensure_lookup_table()
    total = len(bm.faces)
    hit = 0
    for index in indices:
        if 0 <= index < total:
            bm.faces[index].select = True
            hit += 1
    bm.select_flush(True)
    bmesh.update_edit_mesh(mesh)
    return hit


def _grow_triangle_selection(obj):
    """Add triangles that touch an already selected triangle."""
    mesh = obj.data
    bm = bmesh.from_edit_mesh(mesh)
    seeds = [face for face in bm.faces if face.select and len(face.verts) == 3]
    if not seeds:
        return 0
    added = 0
    for face in seeds:
        for edge in face.edges:
            for neighbour in edge.link_faces:
                if neighbour is face or neighbour.select:
                    continue
                if len(neighbour.verts) == 3:
                    neighbour.select = True
                    added += 1
    if added:
        bm.select_flush(True)
        bmesh.update_edit_mesh(mesh)
    return added


def _ensure_edit_mode(context, obj):
    if context.mode == 'EDIT_MESH' and context.view_layer.objects.active is obj:
        return True
    if obj is None or obj.type != 'MESH':
        return False
    context.view_layer.objects.active = obj
    obj.select_set(True)
    if context.mode != 'EDIT_MESH':
        bpy.ops.object.mode_set(mode='EDIT')
    return context.mode == 'EDIT_MESH'


def _frame_selection(context):
    area = context.area
    if area is None or area.type != 'VIEW_3D':
        return
    region = next((reg for reg in area.regions if reg.type == 'WINDOW'), None)
    if region is None:
        return
    try:
        with context.temp_override(area=area, region=region):
            bpy.ops.view3d.view_selected()
    except (RuntimeError, TypeError):
        pass


def _fix_settings_payload(action, face_angle, shape_angle, grow=None,
                          include_ngons=None, limited_dissolve=None,
                          topology_influence=None, extra=None):
    payload = {
        "action": action,
        "face_angle_deg": round(degrees(face_angle), 3),
        "shape_angle_deg": round(degrees(shape_angle), 3),
        "face_angle_rad": face_angle,
        "shape_angle_rad": shape_angle,
    }
    if grow is not None:
        payload["grow_to_neighbours"] = bool(grow)
    if include_ngons is not None:
        payload["include_ngons"] = bool(include_ngons)
    if limited_dissolve is not None:
        payload["limited_dissolve_deg"] = round(degrees(limited_dissolve), 3)
        payload["limited_dissolve_rad"] = limited_dissolve
    if topology_influence is not None:
        payload["topology_influence"] = float(topology_influence)
    if extra:
        payload.update(extra)
    return payload


def _load_scene_fix_defaults(operator, context):
    settings = _settings(context)
    if hasattr(operator, "face_angle"):
        operator.face_angle = settings.fix_face_angle
    if hasattr(operator, "shape_angle"):
        operator.shape_angle = settings.fix_shape_angle
    if hasattr(operator, "grow_to_neighbours"):
        operator.grow_to_neighbours = settings.fix_grow_to_neighbours
    if hasattr(operator, "include_ngons"):
        operator.include_ngons = settings.fix_include_ngons
    if hasattr(operator, "topology_influence"):
        operator.topology_influence = settings.fix_topology_influence


def _apply_operator_fix_props_to_scene(context, face_angle, shape_angle,
                                      grow=None, include_ngons=None,
                                      limited_dissolve=None,
                                      topology_influence=None):
    settings = _settings(context)
    settings.fix_face_angle = face_angle
    settings.fix_shape_angle = shape_angle
    if grow is not None:
        settings.fix_grow_to_neighbours = grow
    if include_ngons is not None:
        settings.fix_include_ngons = include_ngons
    if limited_dissolve is not None:
        settings.fix_limited_dissolve_angle = limited_dissolve
    if topology_influence is not None:
        settings.fix_topology_influence = topology_influence


class QUADBUDDY_OT_toggle_overlay(Operator):
    """Turn the Quad Buddy overlay on or off"""
    bl_idname = "quadbuddy.toggle_overlay"
    bl_label = "Toggle Quad Buddy Overlay"
    bl_options = {'REGISTER'}

    def execute(self, context):
        settings = _settings(context)
        settings.enabled = not settings.enabled
        self.report({'INFO'}, "Quad Buddy overlay %s"
                    % ("on" if settings.enabled else "off"))
        return {'FINISHED'}


class QUADBUDDY_OT_refresh(Operator):
    """Re-analyse the mesh right now"""
    bl_idname = "quadbuddy.refresh"
    bl_label = "Refresh Analysis"
    bl_options = {'REGISTER'}

    def execute(self, context):
        overlay.reset()
        from .props import tag_redraw_view3d
        tag_redraw_view3d()
        return {'FINISHED'}


class QUADBUDDY_OT_select_problems(Operator):
    """Select the flagged geometry in Edit Mode"""
    bl_idname = "quadbuddy.select_problems"
    bl_label = "Select Problems"
    bl_options = {'REGISTER', 'UNDO'}

    kind: EnumProperty(
        name="Kind",
        items=[
            ('ALL', "Triangles and N-gons", "Everything flagged red"),
            ('TRIS', "Triangles", "Triangles that are not excused by a mirror seam"),
            ('NGONS', "N-gons", "Faces with more than four sides"),
            ('NONMANIFOLD', "Non-Manifold", "Edges shared by more than two faces"),
        ],
        default='ALL',
    )

    extend: BoolProperty(name="Extend", default=False)

    @classmethod
    def poll(cls, context):
        active = context.view_layer.objects.active
        return active is not None and active.type == 'MESH'

    def execute(self, context):
        obj = context.view_layer.objects.active
        if not _ensure_edit_mode(context, obj):
            self.report({'ERROR'}, "Could not enter Edit Mode")
            return {'CANCELLED'}

        if self.kind == 'NONMANIFOLD':
            context.tool_settings.mesh_select_mode = (False, True, False)
            if not self.extend:
                bpy.ops.mesh.select_all(action='DESELECT')
            bpy.ops.mesh.select_non_manifold(
                extend=self.extend,
                use_wire=True,
                use_boundary=False,
                use_multi_face=True,
                use_non_contiguous=False,
                use_verts=False,
            )
            self.report({'INFO'}, "Selected non-manifold edges")
            return {'FINISHED'}

        settings = _settings(context)
        tris, ngons = analysis.collect_problem_faces(obj, settings)

        if self.kind == 'TRIS':
            indices = tris
            label = "triangle"
        elif self.kind == 'NGONS':
            indices = ngons
            label = "n-gon"
        else:
            indices = sorted(tris + ngons)
            label = "problem face"

        count = _select_faces(context, obj, indices, self.extend)
        if count:
            self.report({'INFO'}, "Selected %d %s%s"
                        % (count, label, "" if count == 1 else "s"))
        else:
            self.report({'INFO'}, "No %ss found" % label)
        return {'FINISHED'}


class QUADBUDDY_OT_step_problem(Operator):
    """Jump to the next flagged face and make it active"""
    bl_idname = "quadbuddy.step_problem"
    bl_label = "Step Through Problems"
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(
        name="Direction",
        items=[
            ('NEXT', "Next", "Move to the next problem face"),
            ('PREV', "Previous", "Move to the previous problem face"),
        ],
        default='NEXT',
    )

    @classmethod
    def poll(cls, context):
        active = context.view_layer.objects.active
        return active is not None and active.type == 'MESH'

    def execute(self, context):
        obj = context.view_layer.objects.active
        if not _ensure_edit_mode(context, obj):
            self.report({'ERROR'}, "Could not enter Edit Mode")
            return {'CANCELLED'}

        settings = _settings(context)
        tris, ngons = analysis.collect_problem_faces(obj, settings)
        indices = sorted(tris + ngons)
        if not indices:
            self.report({'INFO'}, "No problem faces left on this mesh")
            return {'CANCELLED'}

        position = _cursor.get(obj.name, -1)
        step = 1 if self.direction == 'NEXT' else -1
        position = (position + step) % len(indices)
        _cursor[obj.name] = position
        target = indices[position]

        _select_faces(context, obj, (target,), extend=False)

        mesh = obj.data
        bm = bmesh.from_edit_mesh(mesh)
        bm.faces.ensure_lookup_table()
        if 0 <= target < len(bm.faces):
            bm.faces.active = bm.faces[target]
            bmesh.update_edit_mesh(mesh)

        if settings.frame_on_jump:
            _frame_selection(context)

        self.report({'INFO'}, "Problem %d of %d" % (position + 1, len(indices)))
        return {'FINISHED'}


class QUADBUDDY_OT_fix(Operator):
    """Run a topology fix on the current selection"""
    bl_idname = "quadbuddy.fix"
    bl_label = "Fix Selection"
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(
        name="Action",
        items=[
            ('TRIS_TO_QUADS', "Tris to Quads",
             "Merge neighbouring triangle pairs back into quads"),
            ('REQUAD_NGONS', "Re-Quad N-gons",
             "Triangulate the selection and immediately rebuild quads from it"),
            ('LIMITED_DISSOLVE', "Limited Dissolve",
             "Dissolve edges between nearly coplanar faces"),
            ('DISSOLVE_DEGENERATE', "Dissolve Degenerate",
             "Collapse zero-length edges and zero-area faces"),
            ('TRIANGULATE', "Triangulate",
             "Turn the selection into clean triangles"),
        ],
        default='TRIS_TO_QUADS',
    )

    face_angle: FloatProperty(
        name="Max Face Angle",
        description="Largest angle between two triangles that may be joined",
        default=radians(180.0),
        min=0.0,
        max=radians(180.0),
        subtype='ANGLE',
    )

    shape_angle: FloatProperty(
        name="Max Shape Angle",
        description="How far from a rectangle the resulting quad may be",
        default=radians(180.0),
        min=0.0,
        max=radians(180.0),
        subtype='ANGLE',
    )

    grow_to_neighbours: BoolProperty(
        name="Include Adjacent Triangles",
        description=(
            "Pull neighbouring triangles into the selection first, so a single "
            "picked triangle can still be joined with its partner"
        ),
        default=True,
    )

    topology_influence: FloatProperty(
        name="Topology Influence",
        description="Prefer quads that continue existing grid flow (Blender 5+)",
        default=1.2,
        min=0.0,
        max=2.0,
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        _load_scene_fix_defaults(self, context)
        obj = context.view_layer.objects.active
        before = _face_stats(obj)
        problems_before = debug.problem_snapshot(obj, context)
        dissolve = _settings(context).fix_limited_dissolve_angle
        fix_settings = _fix_settings_payload(
            self.action, self.face_angle, self.shape_angle,
            grow=self.grow_to_neighbours,
            limited_dissolve=dissolve if self.action == 'LIMITED_DISSOLVE' else None,
            topology_influence=self.topology_influence,
        )
        details = {"source": "fix"}
        debug.log_edit(
            context, self.bl_idname, "attempt",
            obj=obj, before=before, details=details,
            fix_settings=fix_settings, problems_before=problems_before)

        try:
            if self.action == 'TRIS_TO_QUADS':
                if self.grow_to_neighbours:
                    details["grown_tris"] = _grow_triangle_selection(obj)
                try:
                    bpy.ops.mesh.tris_convert_to_quads(
                        face_threshold=self.face_angle,
                        shape_threshold=self.shape_angle,
                        topology_influence=self.topology_influence,
                    )
                except TypeError:
                    bpy.ops.mesh.tris_convert_to_quads(
                        face_threshold=self.face_angle,
                        shape_threshold=self.shape_angle,
                    )
            elif self.action == 'REQUAD_NGONS':
                bpy.ops.mesh.quads_convert_to_tris(
                    quad_method='BEAUTY', ngon_method='BEAUTY')
                try:
                    bpy.ops.mesh.tris_convert_to_quads(
                        face_threshold=self.face_angle,
                        shape_threshold=self.shape_angle,
                        topology_influence=self.topology_influence,
                    )
                except TypeError:
                    bpy.ops.mesh.tris_convert_to_quads(
                        face_threshold=self.face_angle,
                        shape_threshold=self.shape_angle,
                    )
            elif self.action == 'LIMITED_DISSOLVE':
                bpy.ops.mesh.dissolve_limited(
                    angle_limit=dissolve, use_dissolve_boundaries=False)
            elif self.action == 'DISSOLVE_DEGENERATE':
                bpy.ops.mesh.dissolve_degenerate()
            else:
                bpy.ops.mesh.quads_convert_to_tris(
                    quad_method='BEAUTY', ngon_method='BEAUTY')
        except Exception as exc:
            debug.log_edit(
                context, self.bl_idname, "error",
                obj=obj, before=before, details=details,
                fix_settings=fix_settings, problems_before=problems_before,
                error=repr(exc))
            raise

        after = _face_stats(obj)
        problems_after = debug.problem_snapshot(obj, context)
        overlay.invalidate()
        _apply_operator_fix_props_to_scene(
            context, self.face_angle, self.shape_angle,
            grow=self.grow_to_neighbours, limited_dissolve=dissolve,
            topology_influence=self.topology_influence)
        debug.log_edit(
            context, self.bl_idname, "finished",
            obj=obj, before=before, after=after, details=details,
            fix_settings=fix_settings,
            problems_before=problems_before, problems_after=problems_after)

        self.report({'INFO'}, "Triangles %d to %d, n-gons %d to %d"
                    % (before[0], after[0], before[1], after[1]))
        return {'FINISHED'}


class QUADBUDDY_OT_quick_cleanup(Operator):
    """Select every flagged triangle and try to merge it back into a quad"""
    bl_idname = "quadbuddy.quick_cleanup"
    bl_label = "Quick Cleanup"
    bl_options = {'REGISTER', 'UNDO'}

    face_angle: FloatProperty(
        name="Max Face Angle",
        default=radians(180.0),
        min=0.0,
        max=radians(180.0),
        subtype='ANGLE',
    )

    shape_angle: FloatProperty(
        name="Max Shape Angle",
        default=radians(180.0),
        min=0.0,
        max=radians(180.0),
        subtype='ANGLE',
    )

    include_ngons: BoolProperty(
        name="Include N-gons",
        description="Also triangulate n-gons first so they can be rebuilt as quads",
        default=False,
    )

    grow_to_neighbours: BoolProperty(
        name="Include Adjacent Triangles",
        description=(
            "Pull in touching triangles, including mirror-paired ones, so a flagged "
            "triangle can pair up with the neighbour that completes its quad"
        ),
        default=True,
    )

    topology_influence: FloatProperty(
        name="Topology Influence",
        description="Prefer quads that continue existing grid flow (Blender 5+). Try 1.0–1.3",
        default=1.2,
        min=0.0,
        max=2.0,
    )

    @classmethod
    def poll(cls, context):
        active = context.view_layer.objects.active
        return active is not None and active.type == 'MESH'

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        _load_scene_fix_defaults(self, context)
        obj = context.view_layer.objects.active
        if not _ensure_edit_mode(context, obj):
            debug.log_edit(
                context, self.bl_idname, "cancelled",
                obj=obj, details={"reason": "could not enter edit mode"},
                fix_settings=_fix_settings_payload(
                    "QUICK_CLEANUP", self.face_angle, self.shape_angle,
                    grow=self.grow_to_neighbours,
                    include_ngons=self.include_ngons,
                    topology_influence=self.topology_influence))
            self.report({'ERROR'}, "Could not enter Edit Mode")
            return {'CANCELLED'}

        settings = _settings(context)
        tris, ngons = analysis.collect_problem_faces(obj, settings)
        indices = sorted(tris + ngons) if self.include_ngons else tris
        fix_settings = _fix_settings_payload(
            "QUICK_CLEANUP", self.face_angle, self.shape_angle,
            grow=self.grow_to_neighbours, include_ngons=self.include_ngons,
            topology_influence=self.topology_influence)

        if not indices and not self.include_ngons:
            # Still run classifier on all tris — maybe mirror-forgiven ones can pair
            pass

        before = _face_stats(obj)
        problems_before = debug.problem_snapshot(obj, context)
        details = {
            "source": "quick_cleanup",
            "problem_tris": len(tris),
            "problem_ngons": len(ngons),
            "target_indices": indices[:128],
            "target_count": len(indices),
        }
        debug.log_edit(
            context, self.bl_idname, "attempt",
            obj=obj, before=before, details=details,
            fix_settings=fix_settings, problems_before=problems_before)

        try:
            if self.include_ngons and ngons:
                _select_faces(context, obj, ngons, extend=False)
                bpy.ops.mesh.quads_convert_to_tris(
                    quad_method='BEAUTY', ngon_method='BEAUTY')

            diag = cleanup.run_cleanup(
                obj,
                face_angle=self.face_angle,
                shape_angle=self.shape_angle,
                topology_influence=self.topology_influence,
                collapse_degenerate=True,
                solve_sandwiches=True,
                target_indices=None,
            )
            details["cleanup"] = diag
            details["explanation"] = cleanup.explain(diag)
        except Exception as exc:
            debug.log_edit(
                context, self.bl_idname, "error",
                obj=obj, before=before, details=details,
                fix_settings=fix_settings, problems_before=problems_before,
                error=repr(exc))
            raise

        after = _face_stats(obj)
        problems_after = debug.problem_snapshot(obj, context)
        overlay.invalidate()
        _apply_operator_fix_props_to_scene(
            context, self.face_angle, self.shape_angle,
            grow=self.grow_to_neighbours, include_ngons=self.include_ngons,
            topology_influence=self.topology_influence)

        debug.log_edit(
            context, self.bl_idname, "finished",
            obj=obj, before=before, after=after, details=details,
            fix_settings=fix_settings,
            problems_before=problems_before, problems_after=problems_after)

        message = cleanup.explain(details["cleanup"])
        level = 'INFO' if details["cleanup"].get("mesh_changed") else 'WARNING'
        self.report({level}, message)
        return {'FINISHED'}


class QUADBUDDY_OT_report(Operator):
    """Write a topology summary to the Info log"""
    bl_idname = "quadbuddy.report"
    bl_label = "Log Topology Report"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        active = context.view_layer.objects.active
        return active is not None and active.type == 'MESH'

    def execute(self, context):
        settings = _settings(context)
        for obj in _mesh_objects(context):
            report = analysis.analyze_object(obj, settings)
            counts = report.counts
            if report.over_budget:
                self.report({'WARNING'}, "%s: %d faces is over the budget, skipped"
                            % (obj.name, counts['total']))
                continue
            self.report(
                {'INFO'},
                "%s: %d faces, %.1f%% quads, %d tris (%d mirror-paired), "
                "%d n-gons, %d non-manifold edges"
                % (obj.name, counts['total'], report.quad_ratio * 100.0,
                   counts['tri'], counts['forgiven'], counts['ngon'],
                   counts['nonmanifold']))
        return {'FINISHED'}


class QUADBUDDY_OT_open_edit_log(Operator):
    """Open the human-readable Quad Buddy edit log"""
    bl_idname = "quadbuddy.open_edit_log"
    bl_label = "Open Edit Log"
    bl_options = {'REGISTER'}

    which: EnumProperty(
        name="File",
        items=[
            ('LOG', "edits.log", "Human-readable transcript"),
            ('JSONL', "edits.jsonl", "Machine-readable events"),
            ('RECIPES', "recipes.jsonl", "Desired-result recipes"),
        ],
        default='LOG',
    )

    def execute(self, context):
        path = {
            'LOG': debug.log_path(),
            'JSONL': debug.jsonl_path(),
            'RECIPES': debug.recipes_path(),
        }[self.which]
        if not os.path.isfile(path):
            if self.which == 'LOG':
                debug.log_edit(
                    context, self.bl_idname, "note",
                    details={"message": "log file created on open"})
            else:
                os.makedirs(debug.data_dir(), exist_ok=True)
                open(path, "a", encoding="utf-8").close()

        text = None
        abs_path = bpy.path.abspath(path)
        for existing in bpy.data.texts:
            if bpy.path.abspath(existing.filepath) == abs_path:
                text = existing
                break
        if text is None:
            text = bpy.data.texts.load(path)
        text.name = {
            'LOG': "QuadBuddy_EditLog",
            'JSONL': "QuadBuddy_EditsJSONL",
            'RECIPES': "QuadBuddy_Recipes",
        }[self.which]

        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'TEXT_EDITOR':
                    for space in area.spaces:
                        if space.type == 'TEXT_EDITOR':
                            space.text = text
                    area.tag_redraw()
                    self.report({'INFO'}, "Opened %s" % path)
                    return {'FINISHED'}

        self.report({'INFO'}, "Loaded %s" % path)
        return {'FINISHED'}


class QUADBUDDY_OT_clear_edit_log(Operator):
    """Delete Quad Buddy edit and recipe logs"""
    bl_idname = "quadbuddy.clear_edit_log"
    bl_label = "Clear Edit Logs"
    bl_options = {'REGISTER'}

    def execute(self, context):
        ok, info = debug.clear_logs()
        if ok:
            self.report({'INFO'}, "Cleared logs in %s" % debug.data_dir())
        else:
            self.report({'ERROR'}, "Could not clear logs: %s" % info)
        return {'FINISHED' if ok else 'CANCELLED'}


class QUADBUDDY_OT_mark_desired(Operator):
    """Mark the last finished Quad Buddy edit as a desired result / recipe"""
    bl_idname = "quadbuddy.mark_desired"
    bl_label = "Mark Desired Result"
    bl_options = {'REGISTER'}

    note: StringProperty(
        name="Note",
        description="Why this result is the desired one",
        default="",
        maxlen=256,
    )

    rating: EnumProperty(
        name="Rating",
        items=[
            ('good', "Good", "Acceptable / desired result"),
            ('great', "Great", "Best-so-far result"),
            ('reject', "Reject", "Log that these settings were wrong"),
        ],
        default='good',
    )

    def invoke(self, context, event):
        self.note = _settings(context).last_desired_note
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        layout = self.layout
        last = debug.last_finished()
        if last is None:
            layout.label(text="No finished edit yet", icon='ERROR')
            return
        layout.label(text="Last: %s" % last.get("operator", "?"), icon='CHECKMARK')
        fix = last.get("fix_settings") or {}
        layout.label(text="action=%s  face=%.1f  shape=%.1f"
                     % (fix.get("action"), fix.get("face_angle_deg", 0),
                        fix.get("shape_angle_deg", 0)))
        layout.prop(self, "rating")
        layout.prop(self, "note")

    def execute(self, context):
        if debug.last_finished() is None:
            self.report({'ERROR'}, "Run a fix first, then mark it")
            return {'CANCELLED'}
        _settings(context).last_desired_note = self.note
        recipe, path = debug.mark_desired(context, note=self.note, rating=self.rating)
        if recipe is None:
            self.report({'ERROR'}, path)
            return {'CANCELLED'}
        self.report(
            {'INFO'},
            "Saved %s recipe %s" % (self.rating, recipe["id"]))
        return {'FINISHED'}


class QUADBUDDY_OT_apply_recipe_settings(Operator):
    """Copy the last desired recipe's fix settings into the scene knobs"""
    bl_idname = "quadbuddy.apply_recipe_settings"
    bl_label = "Load Last Desired Settings"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        recipes = debug.load_jsonl(debug.recipes_path())
        desired = [r for r in recipes if r.get("rating") in ('good', 'great')]
        if not desired:
            self.report({'ERROR'}, "No desired recipes logged yet")
            return {'CANCELLED'}
        recipe = desired[-1]
        fix = recipe.get("fix_settings") or {}
        settings = _settings(context)
        if "face_angle_rad" in fix:
            settings.fix_face_angle = fix["face_angle_rad"]
        elif "face_angle_deg" in fix:
            settings.fix_face_angle = radians(fix["face_angle_deg"])
        if "shape_angle_rad" in fix:
            settings.fix_shape_angle = fix["shape_angle_rad"]
        elif "shape_angle_deg" in fix:
            settings.fix_shape_angle = radians(fix["shape_angle_deg"])
        if "grow_to_neighbours" in fix:
            settings.fix_grow_to_neighbours = bool(fix["grow_to_neighbours"])
        if "include_ngons" in fix:
            settings.fix_include_ngons = bool(fix["include_ngons"])
        if "limited_dissolve_rad" in fix:
            settings.fix_limited_dissolve_angle = fix["limited_dissolve_rad"]
        if "topology_influence" in fix:
            settings.fix_topology_influence = float(fix["topology_influence"])
        scene = recipe.get("scene_settings") or {}
        for key in (
            "respect_mirror", "require_editmode_display", "require_on_cage",
            "require_merge",
        ):
            if key in scene:
                setattr(settings, key, bool(scene[key]))
        if "fix_topology_influence" in scene:
            settings.fix_topology_influence = float(scene["fix_topology_influence"])
        self.report({'INFO'}, "Loaded recipe %s" % recipe.get("id"))
        return {'FINISHED'}


classes = (
    QUADBUDDY_OT_toggle_overlay,
    QUADBUDDY_OT_refresh,
    QUADBUDDY_OT_select_problems,
    QUADBUDDY_OT_step_problem,
    QUADBUDDY_OT_fix,
    QUADBUDDY_OT_quick_cleanup,
    QUADBUDDY_OT_report,
    QUADBUDDY_OT_open_edit_log,
    QUADBUDDY_OT_clear_edit_log,
    QUADBUDDY_OT_mark_desired,
    QUADBUDDY_OT_apply_recipe_settings,
)
