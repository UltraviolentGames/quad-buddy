# Quad Buddy

A Blender retopology assistant that highlights triangles and n-gons in the viewport, understands Mirror modifiers, and helps you fix them.

Requires **Blender 4.2+** (tested on 5.0.1).

## Features

- **Viewport overlay** â€” tris in red, n-gons in magenta, optional poles / non-manifold / boundaries
- **Mirror-aware** â€” triangles that sit on a trusted Mirror seam are marked green (paired into a symmetric quad by the reflected half), not flagged as problems
- **Inspector** â€” pick a face and get a short diagnosis plus a one-click fix when one exists
- **Quick Cleanup** â€” select flagged faces and merge triangle pairs back into quads
- **Keyboard shortcuts** for toggle, step-through, and tris-to-quads

Mirror forgiveness only applies when the Mirror modifier has **Display in Edit Mode** on (and **Merge** on by default). That matches how you actually model with the mirrored half visible.

## Install

1. Download or clone this repository
2. Zip the folder contents (the files themselves, not a nested parent folder), **or** copy the folder into:
   - Windows: `%APPDATA%\Blender Foundation\Blender\<version>\extensions\user_default\quad_buddy`
3. In Blender: **Edit â†’ Preferences â†’ Add-ons** â†’ enable **Quad Buddy**
4. Open the **N** sidebar in the 3D viewport â†’ **Quad Buddy** tab

## Shortcuts

| Shortcut | Action |
|---|---|
| `Shift Alt Q` | Toggle overlay |
| `Shift Alt N` | Next problem face |
| `Shift Alt B` | Previous problem face |
| `Shift Alt J` | Tris to Quads on selection |

## License

GPL-3.0-or-later

## Analysis logs

While tuning fixes, Quad Buddy writes durable logs to:

`%APPDATA%\Blender Foundation\Blender\quad_buddy\`

- `edits.jsonl` — every attempt
- `recipes.jsonl` — settings marked as a desired result

See `LOGGING.md` for the workflow. After you mark good results in Blender, ask Cursor to analyse those files.
