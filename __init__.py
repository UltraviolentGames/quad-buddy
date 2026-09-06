"""Quad Buddy - a retopology assistant for Blender.

Flags triangles and n-gons in the viewport, understands when a triangle is
completed into a quad by a Mirror modifier, and offers guided fixes.
"""

import bpy

from . import keymap, operators, overlay, props, ui

_classes = (props.QuadBuddySettings,) + operators.classes + ui.classes


@bpy.app.handlers.persistent
def _on_depsgraph_update(scene, depsgraph):
    overlay.on_depsgraph_update(depsgraph)


@bpy.app.handlers.persistent
def _on_file_load(*_args):
    overlay.reset()


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)

    bpy.types.Scene.quad_buddy = bpy.props.PointerProperty(
        type=props.QuadBuddySettings)

    overlay.enable()

    if _on_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)
    if _on_file_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_file_load)

    keymap.register()


def unregister():
    keymap.unregister()

    if _on_file_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_file_load)
    if _on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)

    overlay.disable()

    try:
        del bpy.types.Scene.quad_buddy
    except AttributeError:
        pass

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
