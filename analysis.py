"""Topology analysis for Quad Buddy.

Everything here is read-only: it inspects a mesh and reports what is wrong with
it, plus the geometry the overlay needs in order to draw those problems.
"""

import bmesh

try:
    from mathutils.geometry import tessellate_polygon
except ImportError:
    tessellate_polygon = None


FILL_KEYS = ('tri', 'ngon', 'forgiven')
LINE_KEYS = ('tri', 'ngon', 'forgiven', 'nonmanifold', 'boundary')
POINT_KEYS = ('pole_high', 'pole_low')

COUNT_KEYS = (
    'total', 'quad', 'tri', 'ngon', 'forgiven', 'degenerate',
    'nonmanifold', 'boundary', 'pole_high', 'pole_low',
)


class MirrorPlane:
    """A single mirror plane contributed by one axis of one Mirror modifier."""

    __slots__ = ('axis', 'threshold', 'matrix', 'modifier')

    def __init__(self, axis, threshold, matrix, modifier):
        self.axis = axis
        self.threshold = threshold
        self.matrix = matrix
        self.modifier = modifier


class TopologyReport:
    __slots__ = ('fills', 'outlines', 'points', 'problem_faces', 'counts', 'over_budget')

    def __init__(self):
        self.fills = {key: [] for key in FILL_KEYS}
        self.outlines = {key: [] for key in LINE_KEYS}
        self.points = {key: [] for key in POINT_KEYS}
        self.problem_faces = []
        self.counts = {key: 0 for key in COUNT_KEYS}
        self.over_budget = False

    @property
    def problem_count(self):
        return self.counts['tri'] - self.counts['forgiven'] + self.counts['ngon']

    @property
    def quad_ratio(self):
        total = self.counts['total']
        return (self.counts['quad'] / total) if total else 0.0

    def is_empty(self):
        if any(self.fills[key] for key in FILL_KEYS):
            return False
        if any(self.outlines[key] for key in LINE_KEYS):
            return False
        if any(self.points[key] for key in POINT_KEYS):
            return False
        return True


# ---------------------------------------------------------------------------
# Mirror handling
# ---------------------------------------------------------------------------

def mirror_status(obj, settings):
    """Per-modifier summary used by the UI to explain why mirroring is trusted."""
    rows = []
    for mod in obj.modifiers:
        if mod.type != 'MIRROR':
            continue
        axes = ''.join(name for name, on in zip('XYZ', mod.use_axis) if on)
        reasons = []
        if not axes:
            reasons.append("no axis enabled")
        if settings.require_editmode_display and not mod.show_in_editmode:
            reasons.append("Display in Edit Mode is off")
        if settings.require_on_cage and not mod.show_on_cage:
            reasons.append("On Cage is off")
        if settings.require_merge and not mod.use_mirror_merge:
            reasons.append("Merge is off")
        rows.append({
            'name': mod.name,
            'axes': axes or '-',
            'trusted': not reasons,
            'reasons': reasons,
        })
    return rows


def gather_mirror_planes(obj, settings):
    planes = []
    if not settings.respect_mirror:
        return planes
    for mod in obj.modifiers:
        if mod.type != 'MIRROR':
            continue
        if settings.require_editmode_display and not mod.show_in_editmode:
            continue
        if settings.require_on_cage and not mod.show_on_cage:
            continue
        if settings.require_merge and not mod.use_mirror_merge:
            continue
        threshold = max(float(mod.merge_threshold), 1e-6)
        matrix = None
        if mod.mirror_object is not None:
            matrix = mod.mirror_object.matrix_world.inverted_safe() @ obj.matrix_world
        for axis in range(3):
            if mod.use_axis[axis]:
                planes.append(MirrorPlane(axis, threshold, matrix, mod.name))
    return planes


def tri_mirror_forgiven(face, planes):
    """True when exactly one edge of the triangle sits on a mirror plane.

    That edge is the seam, so the mirrored copy of the triangle shares it and
    the pair forms a symmetric quad straddling the plane.
    """
    for plane in planes:
        axis = plane.axis
        threshold = plane.threshold
        matrix = plane.matrix
        on_plane = 0
        for vert in face.verts:
            co = vert.co if matrix is None else matrix @ vert.co
            if abs(co[axis]) <= threshold:
                on_plane += 1
        if on_plane == 2:
            return True
    return False


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def get_bmesh(obj):
    """Return (bmesh, owned). Caller frees the bmesh when owned is True."""
    mesh = obj.data
    if mesh.is_editmode:
        return bmesh.from_edit_mesh(mesh), False
    bm = bmesh.new()
    bm.from_mesh(mesh)
    return bm, True


def analyze_object(obj, settings, geometry=True):
    bm, owned = get_bmesh(obj)
    try:
        return analyze_bmesh(obj, bm, settings, geometry=geometry)
    finally:
        if owned:
            bm.free()


def analyze_bmesh(obj, bm, settings, geometry=True):
    report = TopologyReport()
    counts = report.counts
    counts['total'] = len(bm.faces)

    if counts['total'] > settings.max_faces:
        report.over_budget = True
        return report

    bm.faces.ensure_lookup_table()
    bm.faces.index_update()

    matrix = obj.matrix_world
    normal_matrix = matrix.to_3x3().inverted_safe().transposed()
    offset = settings.depth_offset * object_scale(obj)
    planes = gather_mirror_planes(obj, settings)
    problems = report.problem_faces

    for face in bm.faces:
        sides = len(face.verts)

        if sides == 4:
            counts['quad'] += 1
            continue
        if sides < 3:
            counts['degenerate'] += 1
            continue

        if sides == 3:
            counts['tri'] += 1
            if planes and tri_mirror_forgiven(face, planes):
                counts['forgiven'] += 1
                key = 'forgiven'
                visible = settings.show_forgiven
            else:
                key = 'tri'
                visible = settings.show_tris
                problems.append(face.index)
        else:
            counts['ngon'] += 1
            key = 'ngon'
            visible = settings.show_ngons
            problems.append(face.index)

        if visible and geometry:
            _emit_face(report, key, face, matrix, normal_matrix, offset)

    _scan_edges(report, bm, settings, matrix, normal_matrix, offset, geometry)

    if settings.show_poles:
        _scan_poles(report, bm, matrix, geometry)

    return report


def _emit_face(report, key, face, matrix, normal_matrix, offset):
    normal = normal_matrix @ face.normal
    normal.normalize()
    push = normal * offset
    points = [(matrix @ vert.co) + push for vert in face.verts]
    count = len(points)

    fills = report.fills[key]
    if count == 3:
        fills.extend(points)
    elif count == 4:
        fills.extend((points[0], points[1], points[2], points[0], points[2], points[3]))
    else:
        for a, b, c in _tessellate(points):
            fills.extend((points[a], points[b], points[c]))

    outline = report.outlines[key]
    for index in range(count):
        outline.append(points[index])
        outline.append(points[(index + 1) % count])


def _tessellate(points):
    if tessellate_polygon is not None:
        try:
            triangles = tessellate_polygon((points,))
            if triangles:
                return triangles
        except (ValueError, TypeError):
            pass
    return [(0, i, i + 1) for i in range(1, len(points) - 1)]


def _scan_edges(report, bm, settings, matrix, normal_matrix, offset, geometry=True):
    counts = report.counts
    for edge in bm.edges:
        linked = len(edge.link_faces)
        if linked == 2:
            continue

        if linked == 1:
            counts['boundary'] += 1
            key = 'boundary'
            visible = settings.show_boundary
        else:
            counts['nonmanifold'] += 1
            key = 'nonmanifold'
            visible = settings.show_nonmanifold

        if not visible or not geometry:
            continue

        push = None
        if edge.link_faces:
            normal = normal_matrix @ edge.link_faces[0].normal
            normal.normalize()
            push = normal * offset

        outline = report.outlines[key]
        for vert in edge.verts:
            point = matrix @ vert.co
            if push is not None:
                point = point + push
            outline.append(point)


def _scan_poles(report, bm, matrix, geometry=True):
    counts = report.counts
    high = report.points['pole_high']
    low = report.points['pole_low']
    for vert in bm.verts:
        if not vert.link_faces:
            continue
        edges = vert.link_edges
        if any(edge.is_boundary for edge in edges):
            continue
        valence = len(edges)
        if valence >= 5:
            counts['pole_high'] += 1
            if geometry:
                high.append(matrix @ vert.co)
        elif valence == 3:
            counts['pole_low'] += 1
            if geometry:
                low.append(matrix @ vert.co)


def object_scale(obj):
    dimensions = obj.dimensions
    largest = max(dimensions.x, dimensions.y, dimensions.z)
    return largest if largest > 1e-9 else 1.0


def collect_problem_faces(obj, settings):
    """Indices of faces the overlay would flag red, ignoring display toggles."""
    bm, owned = get_bmesh(obj)
    try:
        bm.faces.ensure_lookup_table()
        bm.faces.index_update()
        planes = gather_mirror_planes(obj, settings)
        tris = []
        ngons = []
        for face in bm.faces:
            sides = len(face.verts)
            if sides == 3:
                if planes and tri_mirror_forgiven(face, planes):
                    continue
                tris.append(face.index)
            elif sides > 4:
                ngons.append(face.index)
        return tris, ngons
    finally:
        if owned:
            bm.free()


# ---------------------------------------------------------------------------
# Advice
# ---------------------------------------------------------------------------

def _planarity(face):
    """Out-of-plane deviation relative to average edge length."""
    sides = len(face.verts)
    if sides < 4:
        return 0.0
    perimeter = face.calc_perimeter()
    if perimeter <= 1e-9:
        return 0.0
    normal = face.normal
    center = face.calc_center_median()
    deviation = max(abs((vert.co - center).dot(normal)) for vert in face.verts)
    return deviation / (perimeter / sides)


def _aspect(face):
    lengths = [edge.calc_length() for edge in face.edges]
    shortest = min(lengths)
    if shortest <= 1e-9:
        return float('inf')
    return max(lengths) / shortest


def _max_valence(face):
    return max(len(vert.link_edges) for vert in face.verts)


def describe_face(obj, face, settings):
    """Return (headline, detail lines, suggested fix action or None)."""
    sides = len(face.verts)

    if face.calc_area() <= 1e-12:
        return (
            "Zero-area face",
            ["This face collapses to nothing, so it has no usable normal.",
             "Merge By Distance or delete it."],
            'DISSOLVE_DEGENERATE',
        )

    if sides == 3:
        return _describe_tri(obj, face, settings)
    if sides > 4:
        return _describe_ngon(face)
    return _describe_quad(face)


def _describe_tri(obj, face, settings):
    planes = gather_mirror_planes(obj, settings)
    if planes and tri_mirror_forgiven(face, planes):
        return (
            "Mirror-paired triangle",
            ["One edge sits on the mirror plane, so the mirrored twin completes a "
             "symmetric quad across the seam.",
             "Leave it. Dissolve the seam edge after applying the Mirror if you want "
             "a single quad in the final mesh."],
            None,
        )

    if any(mod.type == 'MIRROR' for mod in obj.modifiers) and not planes:
        return (
            "Triangle, mirror not trusted",
            ["This object has a Mirror modifier but it does not meet the conditions "
             "in the Mirror panel, so no seam forgiveness was applied.",
             "Turn on Display in Edit Mode (and Merge) to let Quad Buddy pair seam "
             "triangles."],
            None,
        )

    neighbours = [f for edge in face.edges for f in edge.link_faces if f is not face]
    tri_neighbours = [f for f in neighbours if len(f.verts) == 3]
    open_edges = sum(1 for edge in face.edges if edge.is_boundary)

    if tri_neighbours:
        return (
            "Paired triangle",
            ["It shares an edge with %d other triangle%s."
             % (len(tri_neighbours), "" if len(tri_neighbours) == 1 else "s"),
             "Tris to Quads merges the pair into a quad with no shape change."],
            'TRIS_TO_QUADS',
        )

    if open_edges:
        return (
            "Boundary triangle",
            ["It sits on an open edge, so nothing on the far side can absorb it.",
             "Fine on a flat cap. Otherwise extend or reroute the loop, or bridge the "
             "opening first."],
            None,
        )

    valence = _max_valence(face)
    if valence >= 5:
        return (
            "Triangle on a pole",
            ["A corner of this triangle carries %d edges." % valence,
             "Poles pinch when the surface deforms. Move it onto a flat, low-motion "
             "area, or reroute the loop so the pole becomes a clean quad fan."],
            None,
        )

    return (
        "Lone triangle in a quad field",
        ["Nothing next to it can absorb the triangle, so a loop must terminate here.",
         "Reroute the surrounding loop, add a supporting loop, or split the "
         "neighbouring quad into a three-quad fan."],
        None,
    )


def _describe_ngon(face):
    sides = len(face.verts)
    flatness = _planarity(face)

    if sides == 5:
        return (
            "Pentagon",
            ["A five-sided face is a pole in disguise.",
             "Split it into a quad plus a triangle, or reroute the loops so the extra "
             "edge terminates somewhere flat."],
            'REQUAD_NGONS',
        )

    if flatness > 0.08:
        return (
            "Non-planar n-gon (%d sides)" % sides,
            ["Its vertices are %.0f%% of an edge length out of plane, so shading and "
             "deformation are unpredictable." % (flatness * 100.0),
             "Re-quad it, or at least triangulate so the shading is defined."],
            'REQUAD_NGONS',
        )

    return (
        "Flat n-gon (%d sides)" % sides,
        ["Planar, so it renders cleanly and is acceptable on a static cap.",
         "Re-quad it if the mesh deforms, subdivides, or is heading into a game "
         "engine."],
        'REQUAD_NGONS',
    )


def _describe_quad(face):
    flatness = _planarity(face)
    aspect = _aspect(face)

    if flatness > 0.15:
        return (
            "Warped quad",
            ["The corners are %.0f%% of an edge length out of plane." % (flatness * 100.0),
             "It will crease when smooth shaded or subdivided. Flatten it or split it "
             "along the shorter diagonal."],
            None,
        )

    if aspect > 8.0:
        return (
            "Stretched quad",
            ["Longest edge is %.1fx the shortest." % aspect,
             "Thin quads distort textures and deform poorly. Even out the loop spacing."],
            None,
        )

    return ("Clean quad", ["Nothing to fix here."], None)
