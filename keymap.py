"""Keyboard shortcuts for Quad Buddy."""

import bpy

_items = []


def register():
    window_manager = bpy.context.window_manager
    if window_manager is None:
        return
    configs = window_manager.keyconfigs.addon
    if configs is None:
        return

    view3d = configs.keymaps.new(name="3D View", space_type='VIEW_3D')

    item = view3d.keymap_items.new(
        "quadbuddy.toggle_overlay", 'Q', 'PRESS', shift=True, alt=True)
    _items.append((view3d, item))

    item = view3d.keymap_items.new(
        "quadbuddy.step_problem", 'N', 'PRESS', shift=True, alt=True)
    item.properties.direction = 'NEXT'
    _items.append((view3d, item))

    item = view3d.keymap_items.new(
        "quadbuddy.step_problem", 'B', 'PRESS', shift=True, alt=True)
    item.properties.direction = 'PREV'
    _items.append((view3d, item))

    mesh = configs.keymaps.new(name="Mesh", space_type='EMPTY')

    item = mesh.keymap_items.new(
        "quadbuddy.fix", 'J', 'PRESS', shift=True, alt=True)
    item.properties.action = 'TRIS_TO_QUADS'
    _items.append((mesh, item))


def unregister():
    for keymap, item in _items:
        try:
            keymap.keymap_items.remove(item)
        except (RuntimeError, ReferenceError):
            pass
    _items.clear()
