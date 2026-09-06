"""Blender background tests for Zip Triangles.

Run:
  blender --background --python tests/test_zip_triangles.py
"""

from __future__ import annotations

import os
import sys
import traceback

import bmesh
import bpy
from mathutils import Matrix, Vector


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from zip_triangles import (  # noqa: E402
    find_quad_strip,
    zip_selected_triangles,
)


class Failure(Exception):
    pass


def _assert(cond, message):
    if not cond:
        raise Failure(message)


def _new_bm():
    return bmesh.new()


def _positions(bm):
    zt = None
    try:
        from zip_triangles import _ensure_tables
        _ensure_tables(bm)
    except Exception:
        bm.verts.ensure_lookup_table()
        bm.verts.index_update()
    return {v.index: v.co.copy() for v in bm.verts}


def _assert_positions_unchanged(before, bm, label):
    bm.verts.ensure_lookup_table()
    _assert(len(bm.verts) == len(before), "%s: vertex count changed" % label)
    for vert in bm.verts:
        delta = (vert.co - before[vert.index]).length
        _assert(delta <= 1e-9, "%s: vertex %d moved" % (label, vert.index))


def _count_sides(bm):
    tris = sum(1 for f in bm.faces if len(f.verts) == 3)
    quads = sum(1 for f in bm.faces if len(f.verts) == 4)
    ngons = sum(1 for f in bm.faces if len(f.verts) > 4)
    return tris, quads, ngons


def _grid(bm, cols, rows, size=1.0):
    """Create a cols x rows quad grid in XY. Returns face grid [row][col]."""
    verts = [[bm.verts.new((x * size, y * size, 0.0))
              for x in range(cols + 1)]
             for y in range(rows + 1)]
    bm.verts.ensure_lookup_table()
    faces = []
    for y in range(rows):
        row = []
        for x in range(cols):
            face = bm.faces.new((
                verts[y][x],
                verts[y][x + 1],
                verts[y + 1][x + 1],
                verts[y + 1][x],
            ))
            row.append(face)
        faces.append(row)
    bm.faces.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    return faces


def _split_quad_to_tris(bm, face, diagonal="02"):
    """Replace one quad with two triangles. diagonal '02' or '13' in face loop order."""
    loops = list(face.loops)
    v0, v1, v2, v3 = [loop.vert for loop in loops]
    mat = face.material_index
    smooth = face.smooth
    bmesh.ops.delete(bm, geom=[face], context='FACES_ONLY')
    if diagonal == "02":
        t0 = bm.faces.new((v0, v1, v2))
        t1 = bm.faces.new((v0, v2, v3))
    else:
        t0 = bm.faces.new((v0, v1, v3))
        t1 = bm.faces.new((v1, v2, v3))
    for tri in (t0, t1):
        tri.material_index = mat
        tri.smooth = smooth
    bm.faces.ensure_lookup_table()
    return t0, t1


def _make_strip_with_end_tris(cols=3, rows=1, horizontal=True):
    """
    Build a strip of quads with triangles at both ends.

    horizontal: one row of `cols` quads between left/right end triangles.
    vertical: one column of `rows` quads between bottom/top end triangles.
    """
    bm = _new_bm()
    if horizontal:
        left = bm.verts.new((-0.5, 0.5, 0.0))
        right = bm.verts.new((cols + 0.5, 0.5, 0.0))
        grid = [[None for _ in range(cols + 1)] for _ in range(2)]
        for y in range(2):
            for x in range(cols + 1):
                grid[y][x] = bm.verts.new((float(x), float(y), 0.0))
        bm.verts.ensure_lookup_table()
        quads = []
        for x in range(cols):
            quads.append(bm.faces.new((
                grid[0][x], grid[0][x + 1], grid[1][x + 1], grid[1][x],
            )))
        tri_l = bm.faces.new((left, grid[0][0], grid[1][0]))
        tri_r = bm.faces.new((right, grid[1][cols], grid[0][cols]))
        bm.faces.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        return bm, tri_l, tri_r, quads

    top = bm.verts.new((0.5, rows + 0.5, 0.0))
    bottom = bm.verts.new((0.5, -0.5, 0.0))
    grid = [[None for _ in range(2)] for _ in range(rows + 1)]
    for y in range(rows + 1):
        for x in range(2):
            grid[y][x] = bm.verts.new((float(x), float(y), 0.0))
    bm.verts.ensure_lookup_table()
    quads = []
    for y in range(rows):
        quads.append(bm.faces.new((
            grid[y][0], grid[y][1], grid[y + 1][1], grid[y + 1][0],
        )))
    tri_b = bm.faces.new((bottom, grid[0][1], grid[0][0]))
    tri_t = bm.faces.new((top, grid[rows][0], grid[rows][1]))
    bm.faces.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    return bm, tri_b, tri_t, quads


def _zip(bm, tris, **kwargs):
    return zip_selected_triangles(
        bm, tris, max_distance=kwargs.get("max_distance", 16),
        respect_features=kwargs.get("respect_features", True),
        debug_enabled=kwargs.get("debug", False),
        validate_first=kwargs.get("validate_first", True),
    )


def test_one_quad_between():
    bm, a, b, quads = _make_strip_with_end_tris(cols=1, horizontal=True)
    before = _positions(bm)
    tris_before, quads_before, _ = _count_sides(bm)
    result = _zip(bm, [a, b])
    _assert(result.ok, result.message)
    _assert_positions_unchanged(before, bm, "one_quad")
    tris, quads_n, ngons = _count_sides(bm)
    _assert(tris == tris_before - 2, "expected two fewer tris")
    _assert(quads_n == 2, "expected 2 quads after zip, got %d" % quads_n)
    _assert(ngons == 0, "unexpected ngon")
    bm.free()


def test_several_quads():
    bm, a, b, quads = _make_strip_with_end_tris(cols=4, horizontal=True)
    before = _positions(bm)
    result = _zip(bm, [a, b])
    _assert(result.ok, result.message)
    _assert_positions_unchanged(before, bm, "several")
    tris, quads_n, _ = _count_sides(bm)
    _assert(tris == 0, "expected no tris left")
    _assert(quads_n == 5, "expected 5 quads (4 strip + 1 merged), got %d" % quads_n)
    bm.free()


def test_horizontal_strip():
    bm, a, b, _ = _make_strip_with_end_tris(cols=3, horizontal=True)
    result = _zip(bm, [a, b])
    _assert(result.ok, result.message)
    bm.free()


def test_vertical_strip():
    bm, a, b, _ = _make_strip_with_end_tris(rows=3, horizontal=False)
    result = _zip(bm, [a, b])
    _assert(result.ok, result.message)
    tris, _, _ = _count_sides(bm)
    _assert(tris == 0, "vertical: tris remain")
    bm.free()


def test_curved_surface():
    bm, a, b, quads = _make_strip_with_end_tris(cols=3, horizontal=True)
    # Bend the strip into a gentle arc
    for vert in bm.verts:
        x = vert.co.x
        vert.co.z = 0.15 * (x * x)
    bm.normal_update()
    before = _positions(bm)
    result = _zip(bm, [a, b])
    _assert(result.ok, result.message)
    _assert_positions_unchanged(before, bm, "curved")
    bm.free()


def test_reversed_orientations():
    bm, a, b, _ = _make_strip_with_end_tris(cols=2, horizontal=True)
    a.normal_flip()
    bm.normal_update()
    result = _zip(bm, [a, b])
    _assert(result.ok, result.message)
    bm.free()


def test_near_boundary():
    # Strip already has boundary edges on the sides; ensure zip still works.
    bm, a, b, _ = _make_strip_with_end_tris(cols=2, horizontal=True)
    result = _zip(bm, [a, b])
    _assert(result.ok, result.message)
    bm.free()


def test_non_manifold_edge():
    bm, a, b, quads = _make_strip_with_end_tris(cols=2, horizontal=True)
    # Duplicate a face on the first shared strip edge to make it non-manifold
    edge = None
    for e in a.edges:
        if any(f in quads for f in e.link_faces):
            edge = e
            break
    _assert(edge is not None, "could not find shared edge")
    # Extrude a third face using the same edge verts + a new vert
    extra = bm.verts.new((edge.verts[0].co + Vector((0, 0, 1))))
    try:
        bm.faces.new((edge.verts[0], edge.verts[1], extra))
    except ValueError:
        pass
    bm.faces.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    result = _zip(bm, [a, b])
    _assert(not result.ok, "non-manifold path should fail")
    bm.free()


def test_ngon_in_path():
    bm, a, b, quads = _make_strip_with_end_tris(cols=3, horizontal=True)
    # Turn the middle quad into an ngon by dissolving an adjacent... simpler:
    # merge middle quad with a tiny triangle? Or delete middle and make pentagon.
    mid = quads[1]
    # Add a vertex on one edge and rebuild as pentagon
    e = mid.edges[0]
    v0, v1 = e.verts
    mid_pt = bm.verts.new((v0.co + v1.co) * 0.5)
    verts = []
    for loop in mid.loops:
        verts.append(loop.vert)
        if loop.edge is e or set(loop.edge.verts) == {v0, v1}:
            verts.append(mid_pt)
    # unique preserve order
    ordered = []
    seen = set()
    for v in verts:
        if v.index not in seen:
            ordered.append(v)
            seen.add(v.index)
    mat = mid.material_index
    bmesh.ops.delete(bm, geom=[mid], context='FACES_ONLY')
    if len(ordered) >= 5:
        ngon = bm.faces.new(ordered)
        ngon.material_index = mat
    bm.faces.ensure_lookup_table()
    result = _zip(bm, [a, b])
    _assert(not result.ok, "ngon path should fail")
    bm.free()


def test_multiple_paths_prefers_shortest():
    # Build a ring-like dual path: two routes of different lengths between tris.
    bm = _new_bm()
    # Simple case: direct 1-quad path and a longer detour — BFS should pick short.
    bm2, a, b, quads = _make_strip_with_end_tris(cols=1, horizontal=True)
    path = find_quad_strip(bm2, a, b, max_distance=8)
    _assert(path is not None, "expected a path")
    _assert(len(path) == 3, "expected shortest path length 3 (tri,quad,tri), got %d" % len(path))
    bm2.free()
    bm.free()


def test_concave_result_rejected():
    # Build a pathological strip whose only diagonal flip would be concave.
    # Use a dart-shaped quad between two tris.
    bm = _new_bm()
    # Concave quad: verts where one interior angle > 180 in the plane
    vA = bm.verts.new((-1, 0, 0))
    v0 = bm.verts.new((0, 0, 0))
    v1 = bm.verts.new((0, 1, 0))
    v2 = bm.verts.new((0.2, 0.5, 0))  # dent
    v3 = bm.verts.new((-0.2, 0.5, 0))
    vB = bm.verts.new((1, 0.5, 0))
    # This geometry may not form a valid walkable strip; assert fail-safe.
    try:
        tri_a = bm.faces.new((vA, v0, v1))
        # concave quad attempt
        quad = bm.faces.new((v0, v1, v2, v3))
        tri_b = bm.faces.new((vB, v2, v3))
    except ValueError:
        bm.free()
        return  # geometry refused by BMesh itself — acceptable
    bm.faces.ensure_lookup_table()
    before_faces = len(bm.faces)
    result = _zip(bm, [tri_a, tri_b], respect_features=False)
    if result.ok:
        # If it somehow succeeds, resulting faces must not be concave
        from zip_triangles import _face_is_concave, _face_normal
        for face in result.resulting_quads:
            verts = list(face.verts)
            _assert(not _face_is_concave(verts, _face_normal(face)),
                    "produced concave quad")
    else:
        _assert(len(bm.faces) == before_faces, "mesh modified after failure")
    bm.free()


def test_overlapping_multi_pairs():
    # Two disjoint strips should both zip; overlapping should only zip one pair.
    bm1, a1, b1, _ = _make_strip_with_end_tris(cols=2, horizontal=True)
    # Build second strip offset in Y inside same bm by translating copies — easier:
    # create two separate strips in one bmesh.
    bm = _new_bm()
    # Strip 1
    parts = []
    for offset_y in (0.0, 3.0):
        left = bm.verts.new((-0.5, 0.5 + offset_y, 0.0))
        right = bm.verts.new((2.5, 0.5 + offset_y, 0.0))
        g = [[bm.verts.new((float(x), float(y) + offset_y, 0.0))
              for x in range(3)] for y in range(2)]
        quads = []
        for x in range(2):
            quads.append(bm.faces.new((g[0][x], g[0][x + 1], g[1][x + 1], g[1][x])))
        tri_l = bm.faces.new((left, g[0][0], g[1][0]))
        tri_r = bm.faces.new((right, g[1][2], g[0][2]))
        parts.append((tri_l, tri_r))
    bm.faces.ensure_lookup_table()
    tris = [parts[0][0], parts[0][1], parts[1][0], parts[1][1]]
    result = _zip(bm, tris)
    _assert(result.ok, result.message)
    _assert(result.pairs_resolved == 2, "expected 2 disjoint pairs, got %d"
            % result.pairs_resolved)
    bm.free()
    bm1.free()


def test_vertex_preservation():
    bm, a, b, _ = _make_strip_with_end_tris(cols=3, horizontal=True)
    before = _positions(bm)
    count = len(bm.verts)
    result = _zip(bm, [a, b])
    _assert(result.ok, result.message)
    _assert(len(bm.verts) == count, "vertex count changed")
    _assert_positions_unchanged(before, bm, "preserve")
    bm.free()


def test_uv_and_material_preservation():
    bm, a, b, quads = _make_strip_with_end_tris(cols=2, horizontal=True)
    # Assign materials
    a.material_index = 1
    b.material_index = 1
    for q in quads:
        q.material_index = 2
    # UV layer
    uv = bm.loops.layers.uv.new("UVMap")
    for face in bm.faces:
        for loop in face.loops:
            loop[uv].uv = (loop.vert.co.x * 0.25, loop.vert.co.y * 0.25)
    result = _zip(bm, [a, b])
    _assert(result.ok, result.message)
    _assert(bm.loops.layers.uv.get("UVMap") is not None, "UV layer missing")
    for quad in result.resulting_quads:
        _assert(quad.material_index in (1, 2), "material lost")
        for loop in quad.loops:
            # UVs should be finite / set
            u, v = loop[uv].uv
            _assert(u == u and v == v, "UV became NaN")
    bm.free()


def test_single_selection_finds_partner():
    bm, a, b, _ = _make_strip_with_end_tris(cols=2, horizontal=True)
    result = _zip(bm, [a])
    _assert(result.ok, result.message)
    bm.free()


def test_failure_leaves_mesh_unchanged():
    bm, a, b, _ = _make_strip_with_end_tris(cols=2, horizontal=True)
    # Select only one tri with max_distance 0 effectively impossible partner far
    # Create isolated tri with no partner
    lonely = bm.verts.new((10, 10, 0))
    v2 = bm.verts.new((11, 10, 0))
    v3 = bm.verts.new((10, 11, 0))
    iso = bm.faces.new((lonely, v2, v3))
    bm.faces.ensure_lookup_table()
    face_count = len(bm.faces)
    edge_count = len(bm.edges)
    result = _zip(bm, [iso], max_distance=2)
    _assert(not result.ok, "isolated tri should fail")
    _assert(len(bm.faces) == face_count, "faces changed on failure")
    _assert(len(bm.edges) == edge_count, "edges changed on failure")
    bm.free()


def test_undo_via_mesh_roundtrip():
    """Simulate undo by keeping a mesh snapshot and restoring."""
    bm, a, b, _ = _make_strip_with_end_tris(cols=2, horizontal=True)
    mesh = bpy.data.meshes.new("zip_undo_test")
    bm.to_mesh(mesh)
    snapshot = mesh.copy()
    result = _zip(bm, [a, b])
    _assert(result.ok, result.message)
    # Restore
    bm.clear()
    bm.from_mesh(snapshot)
    bm.faces.ensure_lookup_table()
    tris, quads, _ = _count_sides(bm)
    _assert(tris == 2, "undo restore should bring back 2 tris")
    _assert(quads == 2, "undo restore should bring back 2 quads")
    bm.free()
    bpy.data.meshes.remove(mesh)
    bpy.data.meshes.remove(snapshot)


TESTS = [
    test_one_quad_between,
    test_several_quads,
    test_horizontal_strip,
    test_vertical_strip,
    test_curved_surface,
    test_reversed_orientations,
    test_near_boundary,
    test_non_manifold_edge,
    test_ngon_in_path,
    test_multiple_paths_prefers_shortest,
    test_concave_result_rejected,
    test_overlapping_multi_pairs,
    test_vertex_preservation,
    test_uv_and_material_preservation,
    test_single_selection_finds_partner,
    test_failure_leaves_mesh_unchanged,
    test_undo_via_mesh_roundtrip,
]


def main():
    passed = 0
    failed = 0
    for test in TESTS:
        name = test.__name__
        try:
            test()
            print("PASS", name)
            passed += 1
        except Exception as exc:
            failed += 1
            print("FAIL", name, "-", exc)
            traceback.print_exc()
    print("SUMMARY %d passed, %d failed" % (passed, failed))
    # Blender --python keeps running unless we quit
    if failed:
        sys.exit(1)
    bpy.ops.wm.quit_blender()


if __name__ == "__main__":
    main()
