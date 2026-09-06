"""User settings for Quad Buddy."""

import bpy
from bpy.props import BoolProperty, FloatProperty, FloatVectorProperty, IntProperty
from bpy.types import PropertyGroup


def tag_redraw_view3d():
    wm = bpy.context.window_manager
    if wm is None:
        return
    for window in wm.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _refresh(self, context):
    from . import overlay
    overlay.invalidate()
    tag_redraw_view3d()


class QuadBuddySettings(PropertyGroup):

    enabled: BoolProperty(
        name="Show Overlay",
        description="Draw the topology overlay in the 3D viewport",
        default=True,
        update=_refresh,
    )

    scope: bpy.props.EnumProperty(
        name="Scope",
        description="Which objects the overlay analyses",
        items=[
            ('ACTIVE', "Active", "Only the active object"),
            ('SELECTED', "Selected", "Active plus every selected mesh object"),
            ('VISIBLE', "Visible", "Every visible mesh object"),
        ],
        default='SELECTED',
        update=_refresh,
    )

    show_in_object_mode: BoolProperty(
        name="Object Mode",
        description="Keep the overlay visible outside of Edit Mode",
        default=True,
        update=_refresh,
    )

    # --- what to flag -----------------------------------------------------

    show_tris: BoolProperty(
        name="Triangles",
        description="Highlight triangles",
        default=True,
        update=_refresh,
    )

    show_ngons: BoolProperty(
        name="N-gons",
        description="Highlight faces with more than four sides",
        default=True,
        update=_refresh,
    )

    show_forgiven: BoolProperty(
        name="Mirror-Paired Tris",
        description=(
            "Show triangles that pair with their mirrored twin into a symmetric quad. "
            "These are counted separately and never flagged red"
        ),
        default=True,
        update=_refresh,
    )

    show_nonmanifold: BoolProperty(
        name="Non-Manifold Edges",
        description="Highlight edges shared by more than two faces, and loose wire edges",
        default=True,
        update=_refresh,
    )

    show_boundary: BoolProperty(
        name="Open Boundaries",
        description="Highlight edges with only one face. Mirror seams show up here",
        default=False,
        update=_refresh,
    )

    show_poles: BoolProperty(
        name="Poles",
        description="Highlight interior vertices that are not 4-valence",
        default=False,
        update=_refresh,
    )

    # --- mirror handling --------------------------------------------------

    respect_mirror: BoolProperty(
        name="Mirror Aware",
        description=(
            "Do not flag a triangle whose mirrored twin completes it into a quad. "
            "Only applies while the Mirror modifier meets the conditions below"
        ),
        default=True,
        update=_refresh,
    )

    require_editmode_display: BoolProperty(
        name="Require Edit Mode Display",
        description=(
            "Only trust a Mirror modifier when its Display in Edit Mode toggle is on, "
            "so the mirrored half is actually visible while you model"
        ),
        default=True,
        update=_refresh,
    )

    require_on_cage: BoolProperty(
        name="Require On Cage",
        description="Stricter: also require the Mirror modifier's On Cage toggle",
        default=False,
        update=_refresh,
    )

    require_merge: BoolProperty(
        name="Require Merge",
        description=(
            "Only trust a Mirror modifier that merges at the seam. Without merging the "
            "two halves stay separate and the pair never welds into a quad"
        ),
        default=True,
        update=_refresh,
    )

    # --- appearance -------------------------------------------------------

    color_tri: FloatVectorProperty(
        name="Triangle",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 0.12, 0.12, 0.42),
        update=_refresh,
    )

    color_ngon: FloatVectorProperty(
        name="N-gon",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 0.10, 0.52, 0.42),
        update=_refresh,
    )

    color_forgiven: FloatVectorProperty(
        name="Mirror-Paired",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(0.16, 0.90, 0.48, 0.26),
        update=_refresh,
    )

    color_nonmanifold: FloatVectorProperty(
        name="Non-Manifold",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 0.85, 0.10, 1.0),
        update=_refresh,
    )

    color_boundary: FloatVectorProperty(
        name="Boundary",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(0.30, 0.80, 1.0, 0.9),
        update=_refresh,
    )

    color_pole_high: FloatVectorProperty(
        name="Pole 5+",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 0.70, 0.10, 1.0),
        update=_refresh,
    )

    color_pole_low: FloatVectorProperty(
        name="Pole 3",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(0.35, 0.65, 1.0, 1.0),
        update=_refresh,
    )

    draw_fills: BoolProperty(
        name="Fills",
        description="Draw shaded face fills",
        default=True,
        update=_refresh,
    )

    draw_outlines: BoolProperty(
        name="Outlines",
        description="Draw face outlines",
        default=True,
        update=_refresh,
    )

    outline_width: FloatProperty(
        name="Outline Width",
        default=1.8,
        min=1.0,
        max=8.0,
        update=_refresh,
    )

    point_size: FloatProperty(
        name="Pole Size",
        default=7.0,
        min=1.0,
        max=24.0,
        update=_refresh,
    )

    xray: BoolProperty(
        name="See Through",
        description="Draw the overlay through the mesh instead of depth testing it",
        default=False,
        update=_refresh,
    )

    depth_offset: FloatProperty(
        name="Depth Offset",
        description=(
            "Push the overlay towards the camera as a fraction of object size. "
            "Raise it if the highlight flickers against the surface"
        ),
        default=0.0015,
        min=0.0,
        max=0.05,
        precision=4,
        step=1,
        update=_refresh,
    )

    max_faces: IntProperty(
        name="Face Budget",
        description="Skip analysis on meshes above this face count to keep the viewport responsive",
        default=100000,
        min=1000,
        soft_max=2000000,
        update=_refresh,
    )

    # --- navigation -------------------------------------------------------

    frame_on_jump: BoolProperty(
        name="Frame On Jump",
        description="Zoom the view to each problem face when stepping through them",
        default=True,
    )
