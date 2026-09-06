# Quad Buddy analysis logs

These files are written by the Blender add-on so Cursor (or any tool) can
inspect what fixes were tried and which settings produced desired results.

## Location

`%APPDATA%\Blender Foundation\Blender\quad_buddy\`

On this machine that is usually:

`C:\Users\xboxp\AppData\Roaming\Blender Foundation\Blender\quad_buddy\`

| File | Purpose |
|---|---|
| `edits.log` | Human-readable transcript |
| `edits.jsonl` | One JSON event per attempt / finish / cancel / desired mark |
| `recipes.jsonl` | Settings that the user marked as a desired result |

## Workflow

1. Tune **Fix Settings** in the Quad Buddy panel
2. Run a fix / Quick Cleanup
3. If the result looks right, click **Mark Good**
4. If it looks wrong, click **Mark Bad** (optional note: distortion, worse density, etc.)
5. Ask Cursor to analyse `edits.jsonl` / `recipes.jsonl`

Recipes store `fix_settings`, `scene_settings`, before/after counts, and a
`favorable` / `unfavorable` flag plus rating (`great` / `good` / `bad` / `reject`).
Load Last Favorable Settings only applies good/great recipes.
