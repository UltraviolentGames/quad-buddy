"""Zip Triangles — propagate two tris through a quad strip until they cancel.

Vertices never move or merge. The triangle "walks" by flipping the shared edge
of each tri+quad pentagon into the alternate diagonal, leaving a quad behind
and a triangle one step farther along the strip. When the two tris meet, their
shared edge is dissolved into one quad.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import acos, pi

import bmesh
from mathutils import Vector


# ---------------------------------------------------------------------------
# Result / debug helpers
# ---------------------------------------------------------------------------

@dataclass
class ZipDebug:
    enabled: bool = False
    lines: list = field(default_factory=list)

    def log(self, message):
        if self.enabled:
            self.lines.append(str(message))


@dataclass
class ZipResult:
    ok: bool
    message: str
    pairs_resolved: int = 0
    resulting_quads: list = field(default_factory=list)
    debug: ZipDebug = field(default_factory=ZipDebug)


# ---------------------------------------------------------------------------
# Edge / face helpers
# ---------------------------------------------------------------------------

def _edge_key(v0, v1):
    i0, i1 = v0.index, v1.index
    return (i0, i1) if i0 < i1 else (i1, i0)


def _shared_edge(face_a, face_b):
    for edge in face_a.edges:
        if face_b in edge.link_faces:
            return edge
    return None


def _other_face(edge, face):
    for linked in edge.link_faces:
        if linked is not face:
            return linked
    return None


def _is_manifold_edge(edge):
    return len(edge.link_faces) == 2


def _layer_float(edge, bm, names):
    """Read a float edge attribute from common Blender layer names."""
    for name in names:
        layer = bm.edges.layers.float.get(name)
        if layer is not None:
            try:
                return float(edge[layer])
            except (TypeError, KeyError, ValueError):
                pass
    # Legacy crease / bevel accessors on BMEdge
    for attr in ("crease", "bevel_weight"):
        if attr in names or attr.replace("_", "") in "".join(names):
            value = getattr(edge, attr, None)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    pass
    crease_layer = getattr(bm.edges.layers, "crease", None)
    if crease_layer is not None and "crease" in "".join(names):
        active = getattr(crease_layer, "active", None)
        if active is not None:
            try:
                return float(edge[active])
            except (TypeError, KeyError, ValueError):
                pass
    bevel_layer = getattr(bm.edges.layers, "bevel_weight", None)
    if bevel_layer is not None and "bevel" in "".join(names):
        active = getattr(bevel_layer, "active", None)
        if active is not None:
            try:
                return float(edge[active])
            except (TypeError, KeyError, ValueError):
                pass
    return 0.0


def _edge_blocked(bm, edge, respect_features=True):
    """Reject protected feature edges unless the add-on policy says otherwise."""
    if not respect_features:
        return False
    if edge.seam:
        return True
    if not edge.smooth:  # sharp
        return True
    if _layer_float(edge, bm, ("crease_edge", "crease")) > 1e-4:
        return True
    if _layer_float(edge, bm, ("bevel_weight_edge", "bevel_weight")) > 1e-4:
        return True
    return False


def _face_normal(face):
    try:
        return face.normal.copy()
    except Exception:
        return Vector((0.0, 0.0, 1.0))


def _ensure_tables(bm):
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()


def _ordered_quad_cycle(quad, shared):
    """Return (v0, v1, v2, v3) with shared = (v0, v1) along the face winding."""
    loops = list(quad.loops)
    for i, loop in enumerate(loops):
        edge = loop.edge
        if set(edge.verts) == set(shared.verts):
            v0 = loop.vert
            v1 = loop.link_loop_next.vert
            v2 = loop.link_loop_next.link_loop_next.vert
            v3 = loop.link_loop_next.link_loop_next.link_loop_next.vert
            return v0, v1, v2, v3
    return None


def _triangle_apex(tri, shared):
    shared_verts = set(shared.verts)
    for vert in tri.verts:
        if vert not in shared_verts:
            return vert
    return None


# ---------------------------------------------------------------------------
# Geometry quality
# ---------------------------------------------------------------------------

def _interior_angles(verts):
    angles = []
    count = len(verts)
    for i in range(count):
        prev = verts[(i - 1) % count].co
        curr = verts[i].co
        nxt = verts[(i + 1) % count].co
        a = (prev - curr).normalized()
        b = (nxt - curr).normalized()
        if a.length < 1e-12 or b.length < 1e-12:
            angles.append(pi)
            continue
        dot = max(-1.0, min(1.0, a.dot(b)))
        angles.append(acos(dot))
    return angles


def _quad_aspect(verts):
    lengths = [(verts[i].co - verts[(i + 1) % 4].co).length for i in range(4)]
    shortest = min(lengths)
    if shortest <= 1e-12:
        return float("inf")
    return max(lengths) / shortest


def _face_is_concave(verts, normal):
    """True when any 2D cross product against the face normal flips sign."""
    signs = []
    count = len(verts)
    for i in range(count):
        a = verts[i].co
        b = verts[(i + 1) % count].co
        c = verts[(i + 2) % count].co
        cross = (b - a).cross(c - b)
        signs.append(cross.dot(normal))
    positive = any(s > 1e-10 for s in signs)
    negative = any(s < -1e-10 for s in signs)
    return positive and negative


def _face_self_intersects(verts):
    """Cheap bow-tie / degenerate test for quads."""
    if len(verts) != 4:
        return False
    a, b, c, d = [v.co for v in verts]
    if (b - a).cross(d - a).length < 1e-12 and (b - a).cross(c - a).length < 1e-12:
        return True
    n1 = (b - a).cross(c - a)
    n2 = (c - a).cross(d - a)
    if n1.length > 1e-12 and n2.length > 1e-12 and n1.dot(n2) < 0:
        return True
    return False


def score_quad_candidate(verts, reference_normal=None, neighbor_flow=None):
    """Higher is better. Used when choosing among local re-tessellations."""
    if len(verts) != 4:
        return -1e9
    normal = (verts[1].co - verts[0].co).cross(verts[2].co - verts[0].co)
    if normal.length < 1e-12:
        return -1e9
    normal.normalize()

    score = 100.0
    aspect = _quad_aspect(verts)
    score -= min(40.0, max(0.0, (aspect - 1.0) * 8.0))

    angles = _interior_angles(verts)
    for angle in angles:
        score -= abs(angle - (pi / 2.0)) * 12.0

    if _face_is_concave(verts, normal):
        score -= 80.0
    if _face_self_intersects(verts):
        score -= 200.0

    if reference_normal is not None and reference_normal.length > 1e-12:
        score -= (1.0 - max(-1.0, min(1.0, normal.dot(reference_normal.normalized())))) * 25.0

    if neighbor_flow is not None:
        score += neighbor_flow * 5.0

    d1 = (verts[2].co - verts[0].co).length
    d2 = (verts[3].co - verts[1].co).length
    if min(d1, d2) > 1e-12:
        score -= abs(d1 - d2) / max(d1, d2) * 5.0

    return score


def score_quad_strip(path_faces, debug=None):
    """Score a whole candidate strip. Shorter + higher mean quad quality wins."""
    if not path_faces:
        return -1e9
    length = max(0, len(path_faces) - 2)
    score = 200.0 - length * 15.0

    quads = [f for f in path_faces[1:-1] if len(f.verts) == 4]
    if quads:
        qualities = []
        for face in quads:
            verts = list(face.verts)
            qualities.append(score_quad_candidate(verts, _face_normal(face)))
        score += sum(qualities) / len(qualities) * 0.15

    if debug:
        debug.log("strip score=%.2f length=%d faces=%s"
                  % (score, length, [f.index for f in path_faces]))
    return score


def validate_resulting_patch(verts, expect_sides, reference_normal=None):
    if len(verts) != expect_sides:
        return False, "unexpected vertex count"
    if len(set(verts)) != expect_sides:
        return False, "duplicate vertices"
    origin = verts[0].co
    area_vec = Vector((0, 0, 0))
    for i in range(1, expect_sides - 1):
        area_vec += (verts[i].co - origin).cross(verts[i + 1].co - origin)
    if area_vec.length < 1e-12:
        return False, "degenerate face"
    normal = area_vec.normalized()
    if expect_sides == 4:
        if _face_is_concave(verts, normal):
            return False, "concave quad"
        if _face_self_intersects(verts):
            return False, "self-intersecting quad"
        if _quad_aspect(verts) > 40.0:
            return False, "extremely thin quad"
    # Winding may be opposite the reference; callers flip after BMFace creation.
    if reference_normal is not None and reference_normal.length > 1e-12:
        if abs(normal.dot(reference_normal.normalized())) < 0.15:
            return False, "face normal nearly perpendicular to surface"
    return True, "ok"


# ---------------------------------------------------------------------------
# Pathfinding
# ---------------------------------------------------------------------------

def validate_quad_strip(bm, path, respect_features=True, debug=None):
    if path is None or len(path) < 2:
        return False, "path too short"
    start, end = path[0], path[-1]
    if len(start.verts) != 3 or len(end.verts) != 3:
        return False, "Both endpoints must be triangular faces."
    if start is end:
        return False, "triangle paired with itself"

    seen = set()
    for i, face in enumerate(path):
        if face in seen:
            return False, "The face strip revisits a face."
        seen.add(face)
        if i == 0 or i == len(path) - 1:
            if len(face.verts) != 3:
                return False, "Both endpoints must be triangular faces."
        else:
            if len(face.verts) != 4:
                return False, "The path contains a non-quad face."

        if i < len(path) - 1:
            edge = _shared_edge(face, path[i + 1])
            if edge is None:
                return False, "The selected triangles are not connected by a valid quad strip."
            if not _is_manifold_edge(edge):
                return False, "The path crosses a non-manifold edge."
            if edge.is_boundary:
                return False, "The path crosses a mesh boundary."
            if _edge_blocked(bm, edge, respect_features):
                return False, "The path crosses a seam/sharp/crease edge."

    if debug:
        debug.log("validated strip %s" % [f.index for f in path])
    return True, "ok"


def find_quad_strip(bm, start_tri, end_tri, max_distance, respect_features=True,
                    debug=None, allowed_faces=None):
    """BFS through quads for the shortest path from start_tri to end_tri.

    If allowed_faces is set, only intermediate quads from that set are used
    (endpoints may sit just outside). Callers can retry without the restriction.
    """
    if start_tri is end_tri:
        return None
    if len(start_tri.verts) != 3 or len(end_tri.verts) != 3:
        return None

    if _shared_edge(start_tri, end_tri) is not None:
        path = [start_tri, end_tri]
        ok, _reason = validate_quad_strip(bm, path, respect_features, debug)
        return path if ok else None

    frontier = deque([(start_tri, [start_tri], 0)])
    visited = {start_tri}

    while frontier:
        face, path, quads_crossed = frontier.popleft()

        for edge in face.edges:
            if not _is_manifold_edge(edge) or edge.is_boundary:
                continue
            if _edge_blocked(bm, edge, respect_features):
                continue
            other = _other_face(edge, face)
            if other is None or other in visited:
                continue

            if other is end_tri:
                candidate = path + [other]
                ok, reason = validate_quad_strip(bm, candidate, respect_features, debug)
                if ok:
                    if debug:
                        debug.log("found path %s" % [f.index for f in candidate])
                    return candidate
                if debug:
                    debug.log("reject path to target: %s" % reason)
                continue

            if len(other.verts) != 4:
                if debug:
                    debug.log("skip non-quad face %d (%d sides)" % (other.index, len(other.verts)))
                continue

            if allowed_faces is not None and other not in allowed_faces:
                continue

            next_quads = quads_crossed + 1
            if next_quads > max_distance:
                continue

            visited.add(other)
            frontier.append((other, path + [other], next_quads))

    return None


def find_nearest_triangle(bm, start_tri, max_distance, candidates=None,
                          respect_features=True, debug=None, allowed_faces=None):
    """Search for the nearest compatible triangle connected through quads."""
    best = None
    best_score = -1e18
    tris = candidates
    if tris is None:
        tris = [f for f in bm.faces if len(f.verts) == 3 and f is not start_tri]

    if debug:
        debug.log("search from tri %d among %d candidates (max_distance=%d corridor=%s)"
                  % (start_tri.index, len(tris), max_distance,
                     "yes" if allowed_faces else "no"))

    for other in tris:
        path = find_quad_strip(
            bm, start_tri, other, max_distance, respect_features, debug,
            allowed_faces=allowed_faces)
        if path is None:
            continue
        score = score_quad_strip(path, debug)
        if allowed_faces is not None:
            # Prefer paths that stay inside the user-selected corridor.
            score += 50.0
        if score > best_score:
            best_score = score
            best = (other, path, score)

    if best is None:
        return None, None
    if debug:
        debug.log("nearest partner %d path=%s score=%.2f"
                  % (best[0].index, [f.index for f in best[1]], best[2]))
    return best[0], best[1]


def _tris_touching_corridor(bm, corridor, exclude=None):
    """Triangles that share an edge with any face in the corridor set."""
    exclude = exclude or set()
    found = []
    seen = set()
    for face in corridor:
        if not face.is_valid:
            continue
        for edge in face.edges:
            for other in edge.link_faces:
                if other is face or other in exclude or other in seen:
                    continue
                if len(other.verts) == 3:
                    seen.add(other)
                    found.append(other)
    return found


def find_triangle_pair(bm, selected_tris, max_distance, respect_features=True,
                       debug=None, corridor=None):
    """Resolve selection into one (start, end, path), a list of pairs, or an error."""
    tris = [f for f in selected_tris if f.is_valid and len(f.verts) == 3]
    if debug:
        debug.log("triangles considered=%s" % [f.index for f in tris])
    if len(tris) == 0:
        return None, "Select one or more triangular faces."

    def _search_from(start, candidate_tris, allowed):
        return find_nearest_triangle(
            bm, start, max_distance, candidates=candidate_tris,
            respect_features=respect_features, debug=debug,
            allowed_faces=allowed)

    if len(tris) == 1:
        start = tris[0]
        # Prefer partners reachable through the selected quad corridor.
        if corridor:
            corridor_partners = _tris_touching_corridor(bm, corridor, exclude={start})
            if debug:
                debug.log("corridor partners=%s"
                          % [f.index for f in corridor_partners])
            other, path = _search_from(
                start, corridor_partners or None, allowed=corridor)
            if path is None:
                other, path = _search_from(start, None, allowed=corridor)
            if path is None:
                other, path = _search_from(start, None, allowed=None)
        else:
            other, path = _search_from(start, None, allowed=None)
        if path is None:
            return None, "No compatible triangle found within the search distance."
        return (start, other, path), "ok"

    if len(tris) == 2:
        path = None
        if corridor:
            path = find_quad_strip(
                bm, tris[0], tris[1], max_distance, respect_features, debug,
                allowed_faces=corridor)
        if path is None:
            path = find_quad_strip(
                bm, tris[0], tris[1], max_distance, respect_features, debug)
        if path is None:
            return None, "The selected triangles are not connected by a valid quad strip."
        return (tris[0], tris[1], path), "ok"

    pairs = []
    unused = set(tris)
    while len(unused) >= 2:
        best = None
        best_score = -1e18
        unused_list = list(unused)
        for i, a in enumerate(unused_list):
            for b in unused_list[i + 1:]:
                path = None
                if corridor:
                    path = find_quad_strip(
                        bm, a, b, max_distance, respect_features, debug,
                        allowed_faces=corridor)
                if path is None:
                    path = find_quad_strip(
                        bm, a, b, max_distance, respect_features, debug)
                if path is None:
                    continue
                path_set = set(path)
                if any(path_set.intersection(existing[2]) for existing in pairs):
                    if debug:
                        debug.log("reject overlapping path %s"
                                  % [f.index for f in path])
                    continue
                score = score_quad_strip(path, debug)
                if score > best_score:
                    best_score = score
                    best = (a, b, path)
        if best is None:
            break
        pairs.append(best)
        unused.discard(best[0])
        unused.discard(best[1])
        if debug:
            debug.log("paired %d <-> %d" % (best[0].index, best[1].index))

    if not pairs:
        return None, "No compatible triangle pairs found within the search distance."
    return pairs, "ok"


# ---------------------------------------------------------------------------
# Propagation
# ---------------------------------------------------------------------------

def _capture_loop_data(bm, face):
    """Snapshot UV / color loop data keyed by vertex index before face deletion."""
    data = {"uv": {}, "color": {}}
    if face is None or not face.is_valid:
        return data
    for layer in bm.loops.layers.uv.values():
        data["uv"][layer.name] = {
            loop.vert.index: loop[layer].uv.copy() for loop in face.loops
        }
    for layer in bm.loops.layers.color.values():
        try:
            data["color"][layer.name] = {
                loop.vert.index: loop[layer][:] for loop in face.loops
            }
        except Exception:
            pass
    return data


def _apply_loop_data(bm, face, *snapshots):
    """Copy captured loop data onto a new face, preferring the first matching vert."""
    if face is None or not face.is_valid:
        return
    for layer in bm.loops.layers.uv.values():
        for loop in face.loops:
            for snap in snapshots:
                uv_map = snap.get("uv", {}).get(layer.name)
                if uv_map and loop.vert.index in uv_map:
                    try:
                        loop[layer].uv = uv_map[loop.vert.index].copy()
                    except Exception:
                        pass
                    break
    for layer in bm.loops.layers.color.values():
        for loop in face.loops:
            for snap in snapshots:
                color_map = snap.get("color", {}).get(layer.name)
                if color_map and loop.vert.index in color_map:
                    try:
                        loop[layer] = color_map[loop.vert.index]
                    except Exception:
                        pass
                    break


def _propagation_candidates(tri, quad, shared):
    """Return possible (new_quad_verts, new_tri_verts) splits for one walk step.

    Includes opposite-edge advances *and* 90° corner turns so an L-shaped
    strip can keep the moving triangle on the exit edge toward the next face.
    """
    ordered = _ordered_quad_cycle(quad, shared)
    if ordered is None:
        return []
    v0, v1, v2, v3 = ordered
    apex = _triangle_apex(tri, shared)
    if apex is None:
        return []

    ref = (_face_normal(tri) + _face_normal(quad))
    if ref.length > 1e-12:
        ref.normalize()
    else:
        ref = _face_normal(quad)

    # Pentagon boundary: apex, v0, v3, v2, v1
    # Opposite landings (straight strip): diagonals v0-v2 / v1-v3
    # Side landings (corner turn): diagonals apex-v3 / apex-v2
    candidates = []
    options = (
        ((apex, v0, v2, v1), (v0, v2, v3), "opposite"),
        ((apex, v0, v3, v1), (v1, v2, v3), "opposite"),
        ((apex, v3, v2, v1), (apex, v0, v3), "turn"),
        ((apex, v0, v3, v2), (apex, v2, v1), "turn"),
    )
    for quad_verts, tri_verts, kind in options:
        if len(set(quad_verts)) != 4 or len(set(tri_verts)) != 3:
            continue
        ok_q, _ = validate_resulting_patch(list(quad_verts), 4, ref)
        ok_t, _ = validate_resulting_patch(list(tri_verts), 3, ref)
        if not (ok_q and ok_t):
            continue
        qscore = score_quad_candidate(list(quad_verts), ref)
        if kind == "opposite":
            qscore += 2.0  # slight preference for straight flow when either works
        candidates.append({
            "quad_verts": quad_verts,
            "tri_verts": tri_verts,
            "score": qscore,
            "ref": ref,
            "kind": kind,
            "src_tri": tri,
            "src_quad": quad,
        })
    return candidates


def _tri_verts_share_edge_with_face(tri_verts, face):
    """True when two of tri_verts form an existing edge of face."""
    if face is None or not face.is_valid:
        return False
    tri_set = set(tri_verts)
    for edge in face.edges:
        if set(edge.verts) <= tri_set:
            return True
    return False


def propagate_triangle_once(bm, tri, next_quad, toward_face=None, debug=None):
    """Walk tri one step through next_quad. Returns (new_tri, message).

    If toward_face is set, prefer a re-tessellation whose new triangle lands on
    an edge shared with toward_face (straight or corner).
    """
    shared = _shared_edge(tri, next_quad)
    if shared is None or not _is_manifold_edge(shared):
        if debug:
            debug.log("propagate fail: no manifold shared edge")
        return None, "The triangle and next quad do not share a manifold edge."

    candidates = _propagation_candidates(tri, next_quad, shared)
    if not candidates:
        return None, "Zipping would create an invalid or degenerate quad."

    if toward_face is not None and toward_face.is_valid:
        landing = [c for c in candidates
                   if _tri_verts_share_edge_with_face(c["tri_verts"], toward_face)]
        if landing:
            candidates = landing
        elif debug:
            debug.log("no landing candidate onto face %d (%d sides)"
                      % (toward_face.index, len(toward_face.verts)))

    candidates.sort(key=lambda item: item["score"], reverse=True)
    best = candidates[0]
    if debug:
        debug.log("propagate tri %d through quad %d score=%.2f kind=%s"
                  % (tri.index, next_quad.index, best["score"], best.get("kind")))

    src_tri = best["src_tri"]
    src_quad = best["src_quad"]
    mat_tri = src_tri.material_index
    mat_quad = src_quad.material_index
    smooth_tri = src_tri.smooth
    smooth_quad = src_quad.smooth
    snap_tri = _capture_loop_data(bm, src_tri)
    snap_quad = _capture_loop_data(bm, src_quad)

    bmesh.ops.delete(bm, geom=[src_tri, src_quad], context='FACES_ONLY')

    try:
        new_quad = bm.faces.new(best["quad_verts"])
        new_tri = bm.faces.new(best["tri_verts"])
    except (ValueError, RuntimeError) as exc:
        return None, "Failed to create replacement faces: %s" % exc

    new_quad.material_index = mat_quad
    new_quad.smooth = smooth_quad
    new_tri.material_index = mat_tri
    new_tri.smooth = smooth_tri
    new_quad.normal_update()
    new_tri.normal_update()

    ref = best["ref"]
    if new_quad.normal.dot(ref) < 0:
        new_quad.normal_flip()
    if new_tri.normal.dot(ref) < 0:
        new_tri.normal_flip()

    _apply_loop_data(bm, new_quad, snap_quad, snap_tri)
    _apply_loop_data(bm, new_tri, snap_tri, snap_quad)

    ok_q, reason_q = validate_resulting_patch(list(new_quad.verts), 4, ref)
    ok_t, reason_t = validate_resulting_patch(list(new_tri.verts), 3, ref)
    if not ok_q:
        return None, "Zipping would create an invalid or degenerate quad. (%s)" % reason_q
    if not ok_t:
        return None, "Propagation produced an invalid triangle. (%s)" % reason_t

    if toward_face is not None and toward_face.is_valid:
        if _shared_edge(new_tri, toward_face) is None:
            return None, "Lost strip adjacency during propagation."

    _ensure_tables(bm)
    return new_tri, "ok"


def resolve_adjacent_triangles(bm, tri_a, tri_b, debug=None):
    """Dissolve the shared edge between two adjacent tris into one quad."""
    shared = _shared_edge(tri_a, tri_b)
    if shared is None:
        return None, "Triangles are not adjacent."
    if not _is_manifold_edge(shared):
        return None, "The path crosses a non-manifold edge."

    verts = []
    seen = set()
    for face in (tri_a, tri_b):
        for vert in face.verts:
            if vert not in seen:
                verts.append(vert)
                seen.add(vert)
    if len(verts) != 4:
        return None, "Zipping would create an invalid or degenerate quad."

    apex_a = _triangle_apex(tri_a, shared)
    apex_b = _triangle_apex(tri_b, shared)
    v0, v1 = shared.verts
    candidates = [
        (apex_a, v0, apex_b, v1),
        (apex_a, v1, apex_b, v0),
    ]
    ref = _face_normal(tri_a) + _face_normal(tri_b)
    if ref.length > 1e-12:
        ref.normalize()

    best = None
    best_score = -1e18
    for cand in candidates:
        ok, _reason = validate_resulting_patch(list(cand), 4, ref)
        if not ok:
            continue
        score = score_quad_candidate(list(cand), ref)
        if score > best_score:
            best_score = score
            best = cand

    if best is None:
        return None, "Zipping would create an invalid or degenerate quad."

    mat = tri_a.material_index
    smooth = tri_a.smooth
    snap_a = _capture_loop_data(bm, tri_a)
    snap_b = _capture_loop_data(bm, tri_b)

    bmesh.ops.delete(bm, geom=[tri_a, tri_b], context='FACES_ONLY')
    try:
        new_quad = bm.faces.new(best)
    except (ValueError, RuntimeError) as exc:
        return None, "Failed to create final quad: %s" % exc

    new_quad.material_index = mat
    new_quad.smooth = smooth
    new_quad.normal_update()
    if new_quad.normal.dot(ref) < 0:
        new_quad.normal_flip()
    _apply_loop_data(bm, new_quad, snap_a, snap_b)
    if debug:
        debug.log("resolved adjacent tris into quad %d score=%.2f"
                  % (new_quad.index, best_score))

    ok, reason = validate_resulting_patch(list(new_quad.verts), 4, ref)
    if not ok:
        return None, "Zipping would create an invalid or degenerate quad. (%s)" % reason

    _ensure_tables(bm)
    return new_quad, "ok"


def zip_triangle_pair(bm, start_tri, end_tri, path, respect_features=True, debug=None):
    """Propagate start toward end along path, then dissolve."""
    ok, reason = validate_quad_strip(bm, path, respect_features, debug)
    if not ok:
        return None, reason

    if len(path) == 2:
        return resolve_adjacent_triangles(bm, start_tri, end_tri, debug)

    current = start_tri
    for i, face in enumerate(path[1:-1]):
        if not face.is_valid:
            return None, "Strip became invalid during propagation."
        if not current.is_valid:
            return None, "Moving triangle became invalid during propagation."
        shared = _shared_edge(current, face)
        if shared is None:
            return None, "Lost strip adjacency during propagation."
        toward = path[i + 2]  # next quad or the end triangle
        current, msg = propagate_triangle_once(
            bm, current, face, toward_face=toward, debug=debug)
        if current is None:
            return None, msg

    if not current.is_valid or not end_tri.is_valid:
        return None, "Triangles became invalid before final merge."

    if _shared_edge(current, end_tri) is None:
        return None, "The selected triangles are not connected by a valid quad strip."

    return resolve_adjacent_triangles(bm, current, end_tri, debug)


def _normalize_pairs(pair_data):
    if isinstance(pair_data, tuple) and len(pair_data) == 3 and hasattr(pair_data[0], "verts"):
        return [pair_data]
    return list(pair_data)


def _zip_selected_impl(bm, selected_faces, max_distance, respect_features, debug):
    selected_tris = [f for f in selected_faces if f.is_valid and len(f.verts) == 3]
    selected_quads = [f for f in selected_faces if f.is_valid and len(f.verts) == 4]
    corridor = set(selected_quads) if selected_quads else None
    debug.log("selected tris=%s quads=%s"
              % ([f.index for f in selected_tris], [f.index for f in selected_quads]))

    pair_data, message = find_triangle_pair(
        bm, selected_tris, max_distance, respect_features, debug,
        corridor=corridor)
    if pair_data is None:
        return ZipResult(False, message, debug=debug)

    pairs = _normalize_pairs(pair_data)
    resolved = []
    for start, end, path in pairs:
        debug.log("zipping %d <-> %d via %s"
                  % (start.index, end.index, [f.index for f in path]))
        if not start.is_valid or not end.is_valid:
            continue
        live_path = []
        ok_path = True
        for face in path:
            if not face.is_valid:
                ok_path = False
                break
            live_path.append(face)
        if not ok_path:
            continue
        quad, msg = zip_triangle_pair(
            bm, start, end, live_path, respect_features, debug)
        if quad is None:
            return ZipResult(False, msg, pairs_resolved=len(resolved), debug=debug)
        resolved.append(quad)

    if not resolved:
        return ZipResult(
            False,
            message if message != "ok" else "No pairs resolved.",
            debug=debug,
        )

    debug.log("final validation ok pairs=%d" % len(resolved))
    return ZipResult(
        True,
        "Zipped %d triangle pair%s."
        % (len(resolved), "" if len(resolved) == 1 else "s"),
        pairs_resolved=len(resolved),
        resulting_quads=resolved,
        debug=debug,
    )


def zip_selected_triangles(bm, selected_faces, max_distance=16,
                           respect_features=True, debug_enabled=False,
                           validate_first=True):
    """Main entry: zip from a selection of faces on an edit-mode BMesh.

    When validate_first is True, run the full plan on a BMesh copy first so a
    mid-strip failure never leaves the real mesh half-modified.
    """
    debug = ZipDebug(enabled=debug_enabled)
    _ensure_tables(bm)
    selected_tris = [f for f in selected_faces if f.is_valid and len(f.verts) == 3]
    if not selected_tris:
        return ZipResult(False, "Select one or more triangular faces.", debug=debug)

    selected_indices = [f.index for f in selected_tris]

    if validate_first:
        bm_copy = bm.copy()
        try:
            _ensure_tables(bm_copy)
            probe_faces = [bm_copy.faces[i] for i in selected_indices
                           if 0 <= i < len(bm_copy.faces)]
            probe_debug = ZipDebug(enabled=debug_enabled)
            probe = _zip_selected_impl(
                bm_copy, probe_faces, max_distance, respect_features, probe_debug)
            debug.lines.extend(probe_debug.lines)
            if not probe.ok:
                debug.log("dry-run rejected: %s" % probe.message)
                return ZipResult(False, probe.message, debug=debug)
            debug.log("dry-run passed (%d pairs)" % probe.pairs_resolved)
        finally:
            bm_copy.free()
        # Copy/free must not disturb the live mesh; refresh tables just in case.
        _ensure_tables(bm)
        selected_tris = [bm.faces[i] for i in selected_indices
                         if 0 <= i < len(bm.faces) and len(bm.faces[i].verts) == 3]

    return _zip_selected_impl(
        bm, selected_tris, max_distance, respect_features, debug)
