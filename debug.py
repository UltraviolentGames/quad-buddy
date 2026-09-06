"""Persistent edit / recipe logging for Quad Buddy.

Logs live in a fixed AppData folder so Cursor (or anything else) can read them
across Blender sessions:

  %APPDATA%/Blender Foundation/Blender/quad_buddy/
    edits.log      human-readable transcript
    edits.jsonl    one JSON object per event (machine analysis)
    recipes.jsonl  settings marked favorable or unfavorable by the user
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from math import degrees

import bpy

_DIR_NAME = "quad_buddy"
_session_started = False
_last_finished = None
_event_counter = 0


def data_dir():
    root = os.path.join(
        os.path.expandvars(r"%APPDATA%"),
        "Blender Foundation",
        "Blender",
        _DIR_NAME,
    )
    os.makedirs(root, exist_ok=True)
    return root


def log_path():
    return os.path.join(data_dir(), "edits.log")


def jsonl_path():
    return os.path.join(data_dir(), "edits.jsonl")


def recipes_path():
    return os.path.join(data_dir(), "recipes.jsonl")


def is_enabled(context=None):
    scene = None
    if context is not None:
        scene = getattr(context, "scene", None)
    if scene is None:
        scene = getattr(bpy.context, "scene", None)
    if scene is None:
        return True
    settings = getattr(scene, "quad_buddy", None)
    if settings is None:
        return True
    return bool(getattr(settings, "debug_edit_log", True))


def last_finished():
    return _last_finished


def _stamp():
    return datetime.now().isoformat(timespec="seconds")


def _ensure_header():
    global _session_started
    path = log_path()
    if _session_started and os.path.isfile(path):
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n" + "=" * 72 + "\n")
        handle.write("Quad Buddy edit log  %s\n" % _stamp())
        handle.write("Blender %s\n" % bpy.app.version_string)
        handle.write("jsonl=%s\n" % jsonl_path())
        handle.write("recipes=%s\n" % recipes_path())
        handle.write("=" * 72 + "\n")
    _session_started = True


def _json_safe(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return repr(value)


def scene_settings_snapshot(context=None):
    """Capture the overlay / mirror / default-fix knobs that affect results."""
    scene = None
    if context is not None:
        scene = getattr(context, "scene", None)
    if scene is None:
        scene = getattr(bpy.context, "scene", None)
    settings = getattr(scene, "quad_buddy", None) if scene is not None else None
    if settings is None:
        return {}
    return {
        "respect_mirror": bool(settings.respect_mirror),
        "require_editmode_display": bool(settings.require_editmode_display),
        "require_on_cage": bool(settings.require_on_cage),
        "require_merge": bool(settings.require_merge),
        "show_tris": bool(settings.show_tris),
        "show_ngons": bool(settings.show_ngons),
        "show_forgiven": bool(settings.show_forgiven),
        "fix_face_angle_deg": round(degrees(settings.fix_face_angle), 3),
        "fix_shape_angle_deg": round(degrees(settings.fix_shape_angle), 3),
        "fix_grow_to_neighbours": bool(settings.fix_grow_to_neighbours),
        "fix_include_ngons": bool(settings.fix_include_ngons),
        "fix_limited_dissolve_deg": round(
            degrees(settings.fix_limited_dissolve_angle), 3),
        "fix_topology_influence": float(settings.fix_topology_influence),
    }


def mirror_snapshot(obj, context=None):
    settings = None
    if context is not None:
        settings = getattr(getattr(context, "scene", None), "quad_buddy", None)
    if settings is None:
        return []
    from . import analysis
    return analysis.mirror_status(obj, settings)


def selection_summary(obj):
    mesh = obj.data
    try:
        if mesh.is_editmode:
            import bmesh
            bm = bmesh.from_edit_mesh(mesh)
            selected = [face.index for face in bm.faces if face.select]
            sides = {}
            for face in bm.faces:
                if not face.select:
                    continue
                sides[str(len(face.verts))] = sides.get(str(len(face.verts)), 0) + 1
            active = bm.faces.active.index if bm.faces.active is not None else None
            return {
                "selected_faces": selected[:128],
                "selected_count": len(selected),
                "selected_truncated": len(selected) > 128,
                "selected_sides": sides,
                "active_face": active,
                "face_count": len(bm.faces),
            }
        selected = [poly.index for poly in mesh.polygons if poly.select]
        return {
            "selected_faces": selected[:128],
            "selected_count": len(selected),
            "selected_truncated": len(selected) > 128,
            "selected_sides": {},
            "active_face": None,
            "face_count": len(mesh.polygons),
        }
    except (ValueError, ReferenceError, AttributeError) as exc:
        return {"error": repr(exc)}


def problem_snapshot(obj, context=None):
    settings = None
    if context is not None:
        settings = getattr(getattr(context, "scene", None), "quad_buddy", None)
    if settings is None:
        return None
    from . import analysis
    try:
        report = analysis.analyze_object(obj, settings, geometry=False)
    except (ValueError, ReferenceError, AttributeError) as exc:
        return {"error": repr(exc)}
    counts = report.counts
    return {
        "total": counts["total"],
        "quad": counts["quad"],
        "tri": counts["tri"],
        "forgiven": counts["forgiven"],
        "ngon": counts["ngon"],
        "nonmanifold": counts["nonmanifold"],
        "flagged": report.problem_count,
        "quad_ratio": round(report.quad_ratio, 4),
        "over_budget": report.over_budget,
    }


def _append_text(event):
    path = log_path()
    _ensure_header()
    lines = [
        "",
        "[%s] id=%s  %s  status=%s"
        % (event["timestamp"], event["id"], event["operator"], event["status"]),
        "  blend=%s" % event.get("blend"),
    ]
    if event.get("object"):
        lines.append(
            "  object=%s  mesh=%s"
            % (event["object"].get("name"), event["object"].get("mesh")))
    selection = event.get("selection") or {}
    if selection:
        if "error" in selection:
            lines.append("  selection_error=%s" % selection["error"])
        else:
            faces = selection.get("selected_faces") or []
            extra = ""
            if selection.get("selected_truncated"):
                extra = " (+%d more)" % (
                    selection["selected_count"] - len(faces))
            lines.append(
                "  faces=%s  selected=%s%s  active=%s  sides=%s"
                % (selection.get("face_count"), selection.get("selected_count"),
                   extra, selection.get("active_face"),
                   selection.get("selected_sides")))
            if faces:
                lines.append("  selected_indices=%s" % faces)
    if event.get("before_stats") is not None:
        lines.append("  before=%s" % event["before_stats"])
    if event.get("after_stats") is not None:
        lines.append("  after=%s" % event["after_stats"])
    if event.get("delta") is not None:
        lines.append("  delta=%s  mesh_changed=%s"
                     % (event["delta"], event.get("mesh_changed")))
    if event.get("problems_before") is not None:
        lines.append("  problems_before=%s" % event["problems_before"])
    if event.get("problems_after") is not None:
        lines.append("  problems_after=%s" % event["problems_after"])
    if event.get("fix_settings"):
        lines.append("  fix_settings=%s" % event["fix_settings"])
    if event.get("scene_settings"):
        lines.append("  scene_settings=%s" % event["scene_settings"])
    if event.get("mirror"):
        lines.append("  mirror=%s" % event["mirror"])
    if event.get("details"):
        for key, value in event["details"].items():
            lines.append("  %s=%s" % (key, value))
    if event.get("favorable") or event.get("desired"):
        lines.append("  favorable=True  rating=%s  note=%s"
                     % (event.get("rating"), event.get("note") or event.get("desired_note", "")))
    if event.get("unfavorable"):
        lines.append("  unfavorable=True  rating=%s  note=%s"
                     % (event.get("rating"), event.get("note") or event.get("desired_note", "")))
    if event.get("error"):
        lines.append("  error=%s" % event["error"])
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _append_jsonl(path, event):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(_json_safe(event), ensure_ascii=True) + "\n")


def log_edit(context, operator_id, status, *, obj=None, details=None,
             before=None, after=None, error=None, fix_settings=None,
             problems_before=None, problems_after=None):
    """Append one edit attempt to the durable logs."""
    global _event_counter, _last_finished

    if not is_enabled(context) and status != "desired":
        return None

    _event_counter += 1
    event = {
        "id": "qb-%s-%04d" % (
            datetime.now().strftime("%Y%m%d%H%M%S"), _event_counter),
        "timestamp": _stamp(),
        "operator": operator_id,
        "status": status,
        "blend": bpy.data.filepath or "<unsaved>",
        "blender": bpy.app.version_string,
        "details": details or {},
        "fix_settings": fix_settings or {},
        "scene_settings": scene_settings_snapshot(context),
        "error": error,
    }

    if obj is not None:
        event["object"] = {"name": obj.name, "mesh": obj.data.name}
        event["selection"] = selection_summary(obj)
        event["mirror"] = mirror_snapshot(obj, context)

    if before is not None:
        event["before_stats"] = {"tris": before[0], "ngons": before[1]}
    if after is not None:
        event["after_stats"] = {"tris": after[0], "ngons": after[1]}
    if before is not None and after is not None:
        event["delta"] = {
            "tris": after[0] - before[0],
            "ngons": after[1] - before[1],
        }
        event["mesh_changed"] = after != before

    if problems_before is not None:
        event["problems_before"] = problems_before
    if problems_after is not None:
        event["problems_after"] = problems_after

    try:
        _append_text(event)
        _append_jsonl(jsonl_path(), event)
    except OSError as exc:
        print("Quad Buddy debug log failed:", exc)
        return None

    if status == "finished":
        _last_finished = event
    return event


def mark_result(context, note="", rating="good"):
    """Promote the last finished edit into a labeled recipe (good or bad)."""
    global _last_finished
    source = _last_finished
    if source is None:
        return None, "No finished Quad Buddy edit to mark yet"

    favorable = rating in ("good", "great")
    recipe = {
        "id": "recipe-%s" % datetime.now().strftime("%Y%m%d%H%M%S"),
        "timestamp": _stamp(),
        "rating": rating,
        "favorable": favorable,
        "unfavorable": not favorable,
        "note": note or "",
        "source_event_id": source.get("id"),
        "operator": source.get("operator"),
        "blend": source.get("blend"),
        "object": source.get("object"),
        "fix_settings": source.get("fix_settings") or {},
        "scene_settings": source.get("scene_settings") or {},
        "before_stats": source.get("before_stats"),
        "after_stats": source.get("after_stats"),
        "delta": source.get("delta"),
        "problems_before": source.get("problems_before"),
        "problems_after": source.get("problems_after"),
        "details": source.get("details") or {},
        "mirror": source.get("mirror") or [],
    }

    marked = dict(source)
    marked["id"] = "qb-mark-%s" % datetime.now().strftime("%Y%m%d%H%M%S")
    marked["timestamp"] = _stamp()
    marked["status"] = "favorable" if favorable else "unfavorable"
    marked["desired"] = favorable
    marked["favorable"] = favorable
    marked["unfavorable"] = not favorable
    marked["desired_note"] = note or ""
    marked["note"] = note or ""
    marked["recipe_id"] = recipe["id"]
    marked["rating"] = rating

    try:
        _append_text(marked)
        _append_jsonl(jsonl_path(), marked)
        _append_jsonl(recipes_path(), recipe)
    except OSError as exc:
        return None, str(exc)

    _last_finished = marked
    return recipe, recipes_path()


def mark_desired(context, note="", rating="good"):
    """Backwards-compatible alias for mark_result."""
    return mark_result(context, note=note, rating=rating)


def clear_logs():
    global _session_started, _last_finished, _event_counter
    removed = []
    errors = []
    for path in (log_path(), jsonl_path(), recipes_path()):
        try:
            if os.path.isfile(path):
                os.remove(path)
                removed.append(path)
        except OSError as exc:
            errors.append("%s: %s" % (path, exc))
    _session_started = False
    _last_finished = None
    _event_counter = 0
    if errors:
        return False, "; ".join(errors)
    return True, ", ".join(removed) if removed else data_dir()


def load_jsonl(path):
    if not os.path.isfile(path):
        return []
    events = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def summarize_recipes():
    recipes = load_jsonl(recipes_path())
    by_operator = {}
    favorable = 0
    unfavorable = 0
    by_rating = {}
    for recipe in recipes:
        key = recipe.get("operator", "?")
        by_operator.setdefault(key, []).append(recipe)
        rating = recipe.get("rating", "?")
        by_rating[rating] = by_rating.get(rating, 0) + 1
        if recipe.get("unfavorable") or rating in ("bad", "reject", "worse"):
            unfavorable += 1
        elif rating in ("good", "great") or recipe.get("favorable", False):
            favorable += 1
        elif rating in ("bad", "reject", "worse"):
            unfavorable += 1
    return {
        "count": len(recipes),
        "favorable": favorable,
        "unfavorable": unfavorable,
        "by_rating": by_rating,
        "by_operator": {key: len(value) for key, value in by_operator.items()},
        "path": recipes_path(),
        "recipes": recipes,
    }
