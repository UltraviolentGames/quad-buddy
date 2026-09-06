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
3. If the result looks right, click **Mark Desired Result**
4. Ask Cursor to analyse `edits.jsonl` / `recipes.jsonl`

Desired recipes store `fix_settings` (angles, grow, include n-gons, action)
and `scene_settings` (mirror trust knobs), plus before/after problem counts.
