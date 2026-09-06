"""Temporary debug logging for Quad Buddy mesh edits.

Writes every attempted fix/cleanup to a text file so you can see what ran,
what the mesh looked like before/after, and whether anything actually changed.
"""

import os
from datetime import datetime

import bpy

_LOG_NAME = "quad_buddy_edits.log"
_session_started = False


def log_path():
    return os.path.join(bpy.app.tempdir, _LOG_NAME)


def _ensure_header(path):
    global _session_started
    if _session_started and os.path.isfile(path):
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n" + "=" * 72 + "\n")
        handle.write("Quad Buddy edit log  %s\n" % datetime.now().isoformat(timespec="seconds"))
        handle.write("Blender %s  tempdir=%s\n" % (bpy.app.version_string, bpy.app.tempdir))
        handle.write("=" * 72 + "\n")
    _session_started = True


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


def _selection_summary(obj):
    """Compact face-selection snapshot for the log."""
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
                sides[len(face.verts)] = sides.get(len(face.verts), 0) + 1
            active = bm.faces.active.index if bm.faces.active is not None else None
            return {
                "selected_faces": selected[:64],
                "selected_count": len(selected),
                "selected_truncated": len(selected) > 64,
                "selected_sides": sides,
                "active_face": active,
                "face_count": len(bm.faces),
            }
        selected = [poly.index for poly in mesh.polygons if poly.select]
        return {
            "selected_faces": selected[:64],
            "selected_count": len(selected),
            "selected_truncated": len(selected) > 64,
            "selected_sides": {},
            "active_face": None,
            "face_count": len(mesh.polygons),
        }
    except (ValueError, ReferenceError, AttributeError) as exc:
        return {"error": repr(exc)}


def log_edit(context, operator_id, status, *, obj=None, details=None,
             before=None, after=None, error=None):
    """Append one edit attempt to the debug log.

    status should be something like 'attempt', 'finished', or 'cancelled'.
    """
    if not is_enabled(context):
        return log_path()

    path = log_path()
    try:
        _ensure_header(path)
        stamp = datetime.now().isoformat(timespec="seconds")
        blend = bpy.data.filepath or "<unsaved>"
        lines = [
            "",
            "[%s] %s  status=%s" % (stamp, operator_id, status),
            "  blend=%s" % blend,
        ]
        if obj is not None:
            lines.append("  object=%s  mesh=%s" % (obj.name, obj.data.name))
            summary = _selection_summary(obj)
            if "error" in summary:
                lines.append("  selection_error=%s" % summary["error"])
            else:
                faces = summary["selected_faces"]
                extra = " (+%d more)" % (
                    summary["selected_count"] - len(faces)
                ) if summary.get("selected_truncated") else ""
                lines.append(
                    "  faces=%d  selected=%d%s  active=%s  sides=%s"
                    % (summary["face_count"], summary["selected_count"], extra,
                       summary["active_face"], summary["selected_sides"]))
                if faces:
                    lines.append("  selected_indices=%s" % faces)
        if before is not None:
            lines.append("  before_tris=%s  before_ngons=%s" % (before[0], before[1]))
        if after is not None:
            lines.append("  after_tris=%s  after_ngons=%s" % (after[0], after[1]))
            if before is not None:
                lines.append(
                    "  delta_tris=%+d  delta_ngons=%+d"
                    % (after[0] - before[0], after[1] - before[1]))
                changed = after != before
                lines.append("  mesh_changed=%s" % changed)
        if details:
            for key, value in details.items():
                lines.append("  %s=%s" % (key, value))
        if error is not None:
            lines.append("  error=%s" % error)

        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        print("Quad Buddy debug log failed:", exc)
    return path


def clear_log():
    path = log_path()
    global _session_started
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError as exc:
        return False, str(exc)
    _session_started = False
    return True, path


def read_tail(max_lines=80):
    path = log_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        return lines[-max_lines:]
    except OSError:
        return []
