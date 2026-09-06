"""Sidebar panels for Quad Buddy."""

import bmesh
from bpy.types import Panel

from . import analysis, overlay

FIX_LABELS = {
    'TRIS_TO_QUADS': "Tris to Quads",
    'REQUAD_NGONS': "Re-Quad N-gons",
    'LIMITED_DISSOLVE': "Limited Dissolve",
    'DISSOLVE_DEGENERATE': "Dissolve Degenerate",
    'TRIANGULATE': "Triangulate",
}


def _wrap(layout, text, region_width):
    limit = max(24, min(90, int(region_width / 7.2)))
    words = text.split()
    line = ""
    for word in words:
        candidate = word if not line else line + " " + word
        if len(candidate) > limit:
            layout.label(text=line)
            line = word
        else:
            line = candidate
    if line:
        layout.label(text=line)


def _active_mesh(context):
    obj = context.view_layer.objects.active
    if obj is not None and obj.type == 'MESH':
        return obj
    return None


class BasePanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Quad Buddy"


class QUADBUDDY_PT_main(BasePanel, Panel):
    bl_idname = "QUADBUDDY_PT_main"
    bl_label = "Quad Buddy"

    def draw_header(self, context):
        self.layout.prop(context.scene.quad_buddy, "enabled", text="")

    def draw(self, context):
        settings = context.scene.quad_buddy
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(settings, "scope")

        obj = _active_mesh(context)
        if obj is None:
            layout.label(text="Select a mesh object", icon='INFO')
            return

        stats = overlay.ensure_stats(obj, settings)
        box = layout.box()
        box.use_property_split = False

        if stats is None:
            box.label(text="Analysis unavailable", icon='ERROR')
            return

        counts = stats['counts']
        if stats['over_budget']:
            box.label(text="{:,} faces exceeds the budget".format(counts['total']),
                      icon='ERROR')
            box.label(text="Raise Face Budget or decimate first")
            return

        flagged_tris = counts['tri'] - counts['forgiven']
        header = box.row()
        header.label(text=obj.name, icon='MESH_DATA')
        header.label(text="%.1f%% quads" % (stats['quad_ratio'] * 100.0))

        grid = box.grid_flow(row_major=True, columns=2, even_columns=True,
                             align=True)
        grid.label(text="Triangles", icon='MESH_DATA')
        grid.label(text=str(flagged_tris))
        if counts['forgiven']:
            grid.label(text="Mirror-paired", icon='MOD_MIRROR')
            grid.label(text=str(counts['forgiven']))
        grid.label(text="N-gons", icon='MESH_PLANE')
        grid.label(text=str(counts['ngon']))
        if counts['nonmanifold']:
            grid.label(text="Non-manifold", icon='ERROR')
            grid.label(text=str(counts['nonmanifold']))
        if settings.show_poles and (counts['pole_high'] or counts['pole_low']):
            grid.label(text="Poles 5+ / 3", icon='VERTEXSEL')
            grid.label(text="%d / %d" % (counts['pole_high'], counts['pole_low']))

        problems = flagged_tris + counts['ngon']
        if problems == 0:
            box.label(text="All quads. Nothing flagged.", icon='CHECKMARK')

        layout.separator()

        col = layout.column(align=True)
        col.use_property_split = False
        row = col.row(align=True)
        row.operator("quadbuddy.step_problem", text="Previous",
                     icon='TRIA_LEFT').direction = 'PREV'
        row.operator("quadbuddy.step_problem", text="Next",
                     icon='TRIA_RIGHT').direction = 'NEXT'
        col.prop(settings, "frame_on_jump")

        col = layout.column(align=True)
        col.use_property_split = False
        col.operator("quadbuddy.select_problems", text="Select All Problems",
                     icon='RESTRICT_SELECT_OFF').kind = 'ALL'
        row = col.row(align=True)
        row.operator("quadbuddy.select_problems", text="Tris").kind = 'TRIS'
        row.operator("quadbuddy.select_problems", text="N-gons").kind = 'NGONS'
        row.operator("quadbuddy.select_problems",
                     text="Non-Manifold").kind = 'NONMANIFOLD'

        layout.separator()
        row = layout.row(align=True)
        row.use_property_split = False
        row.operator("quadbuddy.refresh", icon='FILE_REFRESH', text="Refresh")
        row.operator("quadbuddy.report", icon='TEXT', text="Log Report")


class QUADBUDDY_PT_inspector(BasePanel, Panel):
    bl_parent_id = "QUADBUDDY_PT_main"
    bl_label = "Inspector"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.quad_buddy
        obj = _active_mesh(context)

        if obj is None or context.mode != 'EDIT_MESH':
            layout.label(text="Enter Edit Mode and pick a face", icon='INFO')
            return

        try:
            bm = bmesh.from_edit_mesh(obj.data)
            face = bm.faces.active
        except (ValueError, ReferenceError):
            face = None

        if face is None or not face.is_valid:
            layout.label(text="No active face", icon='INFO')
            layout.label(text="Click a face, or press Next above")
            return

        headline, lines, action = analysis.describe_face(obj, face, settings)

        box = layout.box()
        sides = len(face.verts)
        icon = 'CHECKMARK' if sides == 4 and headline == "Clean quad" else 'ERROR'
        if headline.startswith("Mirror-paired"):
            icon = 'MOD_MIRROR'
        box.label(text=headline, icon=icon)

        width = context.region.width
        detail = box.column(align=True)
        detail.scale_y = 0.8
        for line in lines:
            _wrap(detail, line, width)

        if action is not None:
            box.separator()
            operator = box.operator(
                "quadbuddy.fix",
                text="Apply %s" % FIX_LABELS.get(action, action),
                icon='PLAY')
            operator.action = action


class QUADBUDDY_PT_fixes(BasePanel, Panel):
    bl_parent_id = "QUADBUDDY_PT_main"
    bl_label = "Fixes"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.quad_buddy

        col = layout.column(align=True)
        col.operator("quadbuddy.quick_cleanup", icon='SHADERFX')

        zip_box = layout.box()
        zip_box.label(text="Zip Triangles", icon='MOD_TRIANGULATE')
        zip_col = zip_box.column(align=True)
        zip_col.enabled = context.mode == 'EDIT_MESH'
        zip_col.operator("quadbuddy.zip_triangles", icon='AUTOMERGE_ON')
        zip_box.use_property_split = True
        zip_box.use_property_decorate = False
        zip_props = zip_box.column(align=True)
        zip_props.prop(settings, "zip_max_distance")
        zip_toggle = zip_box.column(align=True)
        zip_toggle.use_property_split = False
        zip_toggle.prop(settings, "zip_respect_features")
        zip_toggle.prop(settings, "zip_debug")
        zip_tip = zip_box.column(align=True)
        zip_tip.scale_y = 0.8
        _wrap(zip_tip,
              "Walks two triangles through a quad strip by flipping diagonals, "
              "then dissolves them into one quad. Vertices never move or merge.",
              context.region.width)

        box = layout.box()
        box.label(text="Fix Settings", icon='PREFERENCES')
        box.use_property_split = True
        box.use_property_decorate = False
        col = box.column(align=True)
        col.prop(settings, "fix_face_angle")
        col.prop(settings, "fix_shape_angle")
        col.prop(settings, "fix_topology_influence")
        col.prop(settings, "fix_limited_dissolve_angle")
        col = box.column(align=True)
        col.use_property_split = False
        col.prop(settings, "fix_grow_to_neighbours")
        col.prop(settings, "fix_include_ngons")
        tip = box.column(align=True)
        tip.scale_y = 0.8
        _wrap(tip,
              "Cleanup now classifies tris. Isolated triangles in a quad field "
              "cannot be fixed by Alt+J alone — use Zip Triangles, or read the "
              "status bar explanation.",
              context.region.width)

        col = layout.column(align=True)
        col.enabled = context.mode == 'EDIT_MESH'
        col.label(text="On current selection:")
        for action in ('TRIS_TO_QUADS', 'REQUAD_NGONS', 'LIMITED_DISSOLVE',
                       'DISSOLVE_DEGENERATE', 'TRIANGULATE'):
            col.operator("quadbuddy.fix",
                         text=FIX_LABELS[action]).action = action

        if context.mode != 'EDIT_MESH':
            layout.label(text="Enter Edit Mode to use these", icon='INFO')

        layout.separator()
        keys = layout.column(align=True)
        keys.scale_y = 0.8
        keys.label(text="Shortcuts", icon='INFO')
        keys.label(text="Shift Alt Q    toggle overlay")
        keys.label(text="Shift Alt N    next problem")
        keys.label(text="Shift Alt B    previous problem")
        keys.label(text="Shift Alt J    tris to quads")

        layout.separator()
        debug_box = layout.box()
        debug_box.label(text="Debug / Recipes", icon='CONSOLE')
        debug_box.prop(settings, "debug_edit_log")
        row = debug_box.row(align=True)
        row.operator("quadbuddy.mark_desired", text="Mark Good", icon='FUND')
        row.operator("quadbuddy.mark_unfavorable", text="Mark Bad", icon='CANCEL')
        debug_box.operator("quadbuddy.apply_recipe_settings", icon='IMPORT')
        row = debug_box.row(align=True)
        row.operator("quadbuddy.open_edit_log", text="Log").which = 'LOG'
        row.operator("quadbuddy.open_edit_log", text="JSONL").which = 'JSONL'
        row.operator("quadbuddy.open_edit_log", text="Recipes").which = 'RECIPES'
        debug_box.operator("quadbuddy.clear_edit_log", icon='TRASH')
        from . import debug as qb_debug
        note = debug_box.column(align=True)
        note.scale_y = 0.8
        _wrap(note, qb_debug.data_dir(), context.region.width)
        summary = qb_debug.summarize_recipes()
        note.label(text="Favorable: %d   Unfavorable: %d"
                   % (summary.get("favorable", 0), summary.get("unfavorable", 0)))


class QUADBUDDY_PT_mirror(BasePanel, Panel):
    bl_parent_id = "QUADBUDDY_PT_main"
    bl_label = "Mirror"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        self.layout.prop(context.scene.quad_buddy, "respect_mirror", text="")

    def draw(self, context):
        settings = context.scene.quad_buddy
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.active = settings.respect_mirror

        col = layout.column(align=True)
        col.prop(settings, "require_editmode_display")
        col.prop(settings, "require_merge")
        col.prop(settings, "require_on_cage")

        obj = _active_mesh(context)
        if obj is None:
            return

        rows = analysis.mirror_status(obj, settings)
        box = layout.box()
        box.use_property_split = False

        if not rows:
            box.label(text="No Mirror modifier on this object", icon='INFO')
            return

        width = context.region.width
        for row in rows:
            line = box.row()
            if row['trusted']:
                line.label(text="%s  (%s)" % (row['name'], row['axes']),
                           icon='CHECKMARK')
            else:
                line.label(text="%s  (%s)" % (row['name'], row['axes']),
                           icon='ERROR')
                detail = box.column(align=True)
                detail.scale_y = 0.8
                _wrap(detail, "Not trusted: " + ", ".join(row['reasons']), width)

        note = box.column(align=True)
        note.scale_y = 0.8
        _wrap(note,
              "A triangle with one edge on a trusted mirror plane pairs with its "
              "reflection into a symmetric quad, so it is counted separately "
              "instead of flagged red.",
              width)


class QUADBUDDY_PT_display(BasePanel, Panel):
    bl_parent_id = "QUADBUDDY_PT_main"
    bl_label = "Display"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        settings = context.scene.quad_buddy
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column(heading="Flag", align=True)
        col.prop(settings, "show_tris")
        col.prop(settings, "show_ngons")
        col.prop(settings, "show_forgiven")
        col.prop(settings, "show_nonmanifold")
        col.prop(settings, "show_boundary")
        col.prop(settings, "show_poles")

        layout.separator()

        col = layout.column(heading="Draw", align=True)
        col.prop(settings, "draw_fills")
        col.prop(settings, "draw_outlines")
        col.prop(settings, "xray")
        col.prop(settings, "show_in_object_mode")

        layout.separator()

        col = layout.column(align=True)
        col.prop(settings, "outline_width")
        col.prop(settings, "point_size")
        col.prop(settings, "depth_offset")
        col.prop(settings, "max_faces")

        layout.separator()

        col = layout.column(align=True)
        col.prop(settings, "color_tri")
        col.prop(settings, "color_ngon")
        col.prop(settings, "color_forgiven")
        col.prop(settings, "color_nonmanifold")
        col.prop(settings, "color_boundary")
        col.prop(settings, "color_pole_high")
        col.prop(settings, "color_pole_low")


classes = (
    QUADBUDDY_PT_main,
    QUADBUDDY_PT_inspector,
    QUADBUDDY_PT_fixes,
    QUADBUDDY_PT_mirror,
    QUADBUDDY_PT_display,
)
