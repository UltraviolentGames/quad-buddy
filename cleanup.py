"""Smarter triangle cleanup for Quad Buddy.

Blender's built-in Tris-to-Quads (Alt+J) only merges *adjacent triangle pairs*.
On AI / remeshed meshes (Tripo, etc.) most leftover tris sit alone in a sea of
quads, so Alt+J correctly does nothing — which looks like a broken button.

This module:
1. Classifies triangles (pairable vs isolated vs sandwichable)
2. Merges real pairs with aggressive thresholds + topology influence
3. Absorbs short tri–quad–tri sandwiches by dissolving the local patch and
   rebuilding it as quads
4. Optionally collapses near-degenerate triangle edges
5. Returns a diagnostic so the UI can explain what happened
"""

from __future__ import annotations

from math import pi, radians

import bmesh
import bpy


def classify_triangles(bm, face_indices=None):
    """Return pairing info for triangles in the mesh (or a subset)."""
    bm.faces.ensure_lookup_table()
    if face_indices is None:
        tris = [f for f in bm.faces if len(f.verts) == 3]
    else:
        index_set = set(face_indices)
        tris = [bm.faces[i] for i in index_set
                if 0 <= i < len(bm.faces) and len(bm.faces[i].verts) == 3]

    tri_set = set(tris)
    pair_edges = []
    pairable = set()
    for face in tris:
        for edge in face.edges:
            others = [f for f in edge.link_faces if f is not face and f in tri_set]
            if others:
                pairable.add(face)
                pairable.add(others[0])
                pair_edges.append((face, others[0], edge))

    # Unique undirected pairs
    seen = set()
    pairs = []
    for a, b, edge in pair_edges:
        key = tuple(sorted((a.index, b.index)))
        if key in seen:
            continue
        seen.add(key)
        pairs.append((a, b, edge))

    isolated = [f for f in tris if f not in pairable]

    # Sandwich: two isolated tris that both touch the same neighbouring quad
    sandwiches = []
    used = set()
    for face in isolated:
        if face in used:
            continue
        for edge in face.edges:
            for neigh in edge.link_faces:
                if neigh is face or len(neigh.verts) != 4:
                    continue
                # other triangles touching this quad
                partners = []
                for qedge in neigh.edges:
                    for other in qedge.link_faces:
                        if other is neigh or other is face:
                            continue
                        if len(other.verts) == 3 and other in tri_set and other not in used:
                            partners.append(other)
                for partner in partners:
                    sandwiches.append((face, neigh, partner))
                    used.add(face)
                    used.add(partner)
                    break
                if face in used:
                    break
            if face in used:
                break

    return {
        "tris": tris,
        "pairs": pairs,
        "pairable_count": len(pairable),
        "isolated": [f for f in isolated if f not in used],
        "sandwiches": sandwiches,
    }


def _update_edit_mesh(obj):
    bmesh.update_edit_mesh(obj.data, loop_triangles=True, destructive=True)


def _join_triangle_pairs_bmesh(bm, face_angle, shape_angle):
    """Use bmesh.ops.join_triangles on every triangle currently in the mesh."""
    faces = [f for f in bm.faces if len(f.verts) == 3]
    if len(faces) < 2:
        return 0
    before = len(bm.faces)
    bmesh.ops.join_triangles(
        bm,
        faces=faces,
        cmp_seam=False,
        cmp_sharp=False,
        cmp_uvs=False,
        cmp_vcols=False,
        cmp_materials=False,
        angle_face_threshold=face_angle,
        angle_shape_threshold=shape_angle,
    )
    return max(0, before - len(bm.faces))


def _face_vert_set(face):
    return {vert.index for vert in face.verts}


def _shared_edge(face_a, face_b):
    for edge in face_a.edges:
        if face_b in edge.link_faces:
            return edge
    return None


def _rebuild_sandwich(bm, tri_a, quad, tri_b):
    """Replace a tri-quad-tri strip with two quads when the tris sit on opposite sides."""
    if not (tri_a.is_valid and quad.is_valid and tri_b.is_valid):
        return False
    if len(quad.verts) != 4:
        return False

    edge_a = _shared_edge(tri_a, quad)
    edge_b = _shared_edge(tri_b, quad)
    if edge_a is None or edge_b is None:
        return False

    # Opposite edges of a quad share no vertices.
    verts_a = set(edge_a.verts)
    verts_b = set(edge_b.verts)
    opposite = verts_a.isdisjoint(verts_b)

    apex_a = [vert for vert in tri_a.verts if vert not in verts_a]
    apex_b = [vert for vert in tri_b.verts if vert not in verts_b]
    if len(apex_a) != 1 or len(apex_b) != 1:
        return False
    apex_a = apex_a[0]
    apex_b = apex_b[0]

    qa, qb = edge_a.verts
    qc, qd = edge_b.verts

    if opposite:
        # Two quads: (apex_a, qa, qc, qb) depends on winding — build from edge ends.
        # Match quad loop order so the new faces don't flip.
        loop = list(quad.verts)
        # Find indices of the two shared edges along the loop
        def edge_pos(v0, v1):
            for i, vert in enumerate(loop):
                nxt = loop[(i + 1) % 4]
                if {vert, nxt} == {v0, v1}:
                    return i
            return None

        i0 = edge_pos(qa, qb)
        i1 = edge_pos(qc, qd)
        if i0 is None or i1 is None:
            return False

        # Order shared-edge verts along the quad winding
        a0, a1 = loop[i0], loop[(i0 + 1) % 4]
        b0, b1 = loop[i1], loop[(i1 + 1) % 4]

        # Quads: apex_a-a0-b1-a1  and  b0-apex_b-b1? 
        # For opposite edges, the quad is a0-a1-…-b0-b1 around.
        # Adjacent verts after a1 should lead to one end of the other edge.
        # Safer construction used by strip remesh:
        #   quad1 = apex_a, a0, b1, a1   if a0 connected toward b1
        # Check connectivity via existing quad edges.
        def connected(v0, v1):
            return bm.edges.get((v0, v1)) is not None

        # Try the two natural strip windings.
        candidates = [
            [(apex_a, a0, b1, a1), (a0, apex_b, b0, b1)],
            [(apex_a, a0, b0, a1), (a1, b1, apex_b, b0)],
            [(apex_a, a1, b0, a0), (a0, b1, apex_b, b0)],
            [(apex_a, a0, b1, a1), (b1, b0, apex_b, a0)],
        ]
    else:
        # Adjacent shared edges — join into one n-gon then triangulate/join later.
        candidates = []

    # Delete original faces first, then try to create quads.
    bmesh.ops.delete(bm, geom=[tri_a, quad, tri_b], context='FACES_ONLY')

    created = False
    if opposite:
        for faces in candidates:
            new_faces = []
            ok = True
            for verts in faces:
                try:
                    new_faces.append(bm.faces.new(verts))
                except (ValueError, RuntimeError):
                    ok = False
                    break
            if ok:
                created = True
                for face in new_faces:
                    face.normal_update()
                break
            for face in new_faces:
                if face.is_valid:
                    bm.faces.remove(face)

    if not created:
        # Fallback: boundary fill from remaining verts of the hole.
        # Collect the outer loop verts: apex_a + edge_a + path + edge_b + apex_b
        try:
            verts = [apex_a, qa, qc, apex_b, qd, qb]
            # unique preserve order
            ordered = []
            seen = set()
            for vert in verts:
                if vert.index not in seen:
                    ordered.append(vert)
                    seen.add(vert.index)
            if len(ordered) >= 3:
                face = bm.faces.new(ordered)
                bmesh.ops.triangulate(
                    bm, faces=[face], quad_method='BEAUTY', ngon_method='BEAUTY')
                tris = [f for f in bm.faces if len(f.verts) == 3]
                if len(tris) >= 2:
                    bmesh.ops.join_triangles(
                        bm,
                        faces=tris,
                        angle_face_threshold=pi,
                        angle_shape_threshold=pi,
                    )
                created = True
        except (ValueError, RuntimeError):
            created = False

    return created


def _dissolve_sandwich(bm, tri_a, quad, tri_b, face_angle, shape_angle):
    before_tris = sum(1 for f in bm.faces if f.is_valid and len(f.verts) == 3)
    ok = _rebuild_sandwich(bm, tri_a, quad, tri_b)
    if not ok:
        return False
    after_tris = sum(1 for f in bm.faces if f.is_valid and len(f.verts) == 3)
    return after_tris < before_tris


def _collapse_degenerate_tris(bm, relative=0.05):
    """Collapse a triangle edge that is tiny relative to the other two."""
    collapsed = 0
    # Snapshot because collapsing mutates the mesh
    candidates = [f for f in bm.faces if len(f.verts) == 3]
    for face in candidates:
        if not face.is_valid:
            continue
        edges = list(face.edges)
        if len(edges) != 3:
            continue
        lengths = [(edge.calc_length(), edge) for edge in edges]
        lengths.sort(key=lambda item: item[0])
        shortest, edge = lengths[0]
        longest = lengths[2][0]
        if longest <= 1e-12:
            continue
        if shortest / longest > relative:
            continue
        try:
            bmesh.ops.collapse(bm, edge_set=[edge], uvs=True)
            collapsed += 1
        except (ValueError, RuntimeError):
            continue
    return collapsed


def _ops_tris_to_quads(face_angle, shape_angle, topology_influence):
    kwargs = {
        "face_threshold": face_angle,
        "shape_threshold": shape_angle,
        "uvs": False,
        "vcols": False,
        "seam": False,
        "sharp": False,
        "materials": False,
        "deselect_joined": False,
    }
    # Blender 5+
    try:
        bpy.ops.mesh.tris_convert_to_quads(
            topology_influence=topology_influence, **kwargs)
    except TypeError:
        bpy.ops.mesh.tris_convert_to_quads(**kwargs)


def run_cleanup(obj, *, face_angle=None, shape_angle=None,
                topology_influence=1.2, collapse_degenerate=True,
                solve_sandwiches=True, target_indices=None):
    """Run multi-pass cleanup on the active edit-mode mesh object.

    Returns a dict of diagnostics for logging / UI.
    """
    if face_angle is None:
        face_angle = pi  # 180°
    if shape_angle is None:
        shape_angle = pi

    mesh = obj.data
    bm = bmesh.from_edit_mesh(mesh)
    bm.faces.ensure_lookup_table()

    info = classify_triangles(bm, target_indices)
    diag = {
        "initial_tris": len(info["tris"]),
        "pairable": info["pairable_count"],
        "pair_count": len(info["pairs"]),
        "isolated": len(info["isolated"]),
        "sandwiches": len(info["sandwiches"]),
        "joined_bmesh": 0,
        "joined_ops": 0,
        "sandwiches_solved": 0,
        "collapsed": 0,
        "passes": [],
    }

    # --- Pass 1: dissolve sandwiches (tri-quad-tri) -----------------------
    if solve_sandwiches and info["sandwiches"]:
        solved = 0
        # Re-fetch after each mutate
        for _ in range(len(info["sandwiches"])):
            bm.faces.ensure_lookup_table()
            current = classify_triangles(bm, target_indices)
            if not current["sandwiches"]:
                break
            tri_a, quad, tri_b = current["sandwiches"][0]
            if _dissolve_sandwich(bm, tri_a, quad, tri_b, face_angle, shape_angle):
                solved += 1
        diag["sandwiches_solved"] = solved
        diag["passes"].append("sandwiches=%d" % solved)
        _update_edit_mesh(obj)
        bm = bmesh.from_edit_mesh(mesh)

    # --- Pass 2: bmesh join_triangles (aggressive) ------------------------
    bm.faces.ensure_lookup_table()
    joined = _join_triangle_pairs_bmesh(bm, face_angle, shape_angle)
    diag["joined_bmesh"] = joined
    diag["passes"].append("bmesh_join=%d" % joined)
    _update_edit_mesh(obj)

    # --- Pass 3: built-in Alt+J with topology influence -------------------
    before = sum(1 for p in mesh.polygons if len(p.vertices) == 3)
    # Select all remaining tris (and keep going on whole mesh selection of tris)
    bm = bmesh.from_edit_mesh(mesh)
    bm.faces.ensure_lookup_table()
    bpy.ops.mesh.select_all(action='DESELECT')
    for face in bm.faces:
        face.select = len(face.verts) == 3
    bm.select_flush(True)
    _update_edit_mesh(obj)

    _ops_tris_to_quads(face_angle, shape_angle, topology_influence)
    after = sum(1 for p in mesh.polygons if len(p.vertices) == 3)
    diag["joined_ops"] = max(0, before - after)
    diag["passes"].append("ops_join=%d" % diag["joined_ops"])

    # --- Pass 4: collapse degenerate tris ---------------------------------
    if collapse_degenerate:
        bm = bmesh.from_edit_mesh(mesh)
        collapsed = _collapse_degenerate_tris(bm)
        diag["collapsed"] = collapsed
        diag["passes"].append("collapse=%d" % collapsed)
        _update_edit_mesh(obj)

    # --- Final classification ---------------------------------------------
    bm = bmesh.from_edit_mesh(mesh)
    bm.faces.ensure_lookup_table()
    final = classify_triangles(bm, None)
    diag["final_tris"] = len(final["tris"])
    diag["final_pairable"] = final["pairable_count"]
    diag["final_isolated"] = len(final["isolated"])
    diag["final_sandwiches"] = len(final["sandwiches"])
    diag["removed_tris"] = diag["initial_tris"] - diag["final_tris"]
    diag["mesh_changed"] = diag["removed_tris"] > 0 or diag["collapsed"] > 0

    if not diag["mesh_changed"]:
        if diag["initial_tris"] == 0:
            diag["reason"] = "no_triangles"
        elif diag["pairable"] == 0 and diag["sandwiches"] == 0:
            diag["reason"] = "isolated_only"
        elif diag["sandwiches"] and diag["sandwiches_solved"] == 0 and diag["pairable"] == 0:
            diag["reason"] = "sandwich_unsolved"
        else:
            diag["reason"] = "thresholds_or_topology_blocked"
    else:
        diag["reason"] = "ok"

    # Leave remaining problem tris selected for the user
    bpy.ops.mesh.select_all(action='DESELECT')
    bm = bmesh.from_edit_mesh(mesh)
    for face in bm.faces:
        face.select = len(face.verts) == 3
    bm.select_flush(True)
    _update_edit_mesh(obj)

    return diag


def explain(diag):
    """Short human summary for reports / UI."""
    if diag.get("reason") == "no_triangles":
        return "No triangles left to clean."
    if diag.get("reason") == "isolated_only":
        return (
            "Found %d isolated triangle(s) sitting in a quad field. "
            "Alt+J cannot merge those — they need a path cut to another odd face "
            "or a manual loop reroute. Remaining tris are selected."
            % diag.get("final_isolated", diag.get("isolated", 0))
        )
    if diag.get("reason") == "sandwich_unsolved":
        return (
            "Detected %d tri-quad-tri sandwich(es) but could not rebuild them safely. "
            "Remaining tris are selected."
            % diag.get("sandwiches", 0)
        )
    if diag.get("reason") == "thresholds_or_topology_blocked":
        return (
            "Had %d pairable triangle(s) but none merged. Try higher face/shape "
            "angles (up to 180°) or check seams/sharps."
            % diag.get("pairable", 0)
        )
    return (
        "Removed %d triangle(s). %d still remain (%d isolated)."
        % (diag.get("removed_tris", 0), diag.get("final_tris", 0),
           diag.get("final_isolated", 0))
    )
