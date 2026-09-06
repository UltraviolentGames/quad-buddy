# Quad Buddy

A Blender retopology assistant that highlights triangles and n-gons in the viewport, understands Mirror modifiers, and helps you fix them.

Requires **Blender 4.2+** (tested on 5.0.1).

## Features

- **Viewport overlay** — tris in red, n-gons in magenta, optional poles / non-manifold / boundaries
- **Mirror-aware** — triangles that sit on a trusted Mirror seam are marked green (paired into a symmetric quad by the reflected half), not flagged as problems
- **Inspector** — pick a face and get a short diagnosis plus a one-click fix when one exists
- **Quick Cleanup** — select flagged faces and merge triangle pairs back into quads
- **Keyboard shortcuts** for toggle, step-through, and tris-to-quads

Mirror forgiveness only applies when the Mirror modifier has **Display in Edit Mode** on (and **Merge** on by default). That matches how you actually model with the mirrored half visible.

## Install

1. Download or clone this repository
2. Zip the folder contents (the files themselves, not a nested parent folder), **or** copy the folder into:
   - Windows: `%APPDATA%\Blender Foundation\Blender\<version>\extensions\user_default\quad_buddy`
3. In Blender: **Edit → Preferences → Add-ons** → enable **Quad Buddy**
4. Open the **N** sidebar in the 3D viewport → **Quad Buddy** tab

## Shortcuts

| Shortcut | Action |
|---|---|
| `Shift Alt Q` | Toggle overlay |
| `Shift Alt N` | Next problem face |
| `Shift Alt B` | Previous problem face |
| `Shift Alt J` | Tris to Quads on selection |

## License

GPL-3.0-or-later
