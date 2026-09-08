# Defold agent loop

This project is a Defold game. Treat it like a Godot CLI session: edit text files, compile, run, inspect a **runtime snapshot file**, edit again. Do **not** install an editor plugin. Hierarchy / create-go / script patch are first-party (`defold_agent.py` + editor `/agent/command`), not an addon.

## Files you write

Defold sources are text:

- `game.project`
- `*.collection` — scenes
- `*.go` — game objects
- `*.script` / `*.gui_script` / `.render_script` — Lua
- `*.atlas`, `*.tilemap`, `*.particlefx`, `input/*.input_binding`

Keep paths project-relative with a leading slash in Lua (`/main/player.script`).

## One command

From the project root (or this engine repo):

```bash
python path/to/defold-1.13.1/scripts/agent/defold_agent.py doctor
python path/to/defold-1.13.1/scripts/agent/defold_agent.py check
python path/to/defold-1.13.1/scripts/agent/defold_agent.py observe --frames 30
python path/to/defold-1.13.1/scripts/agent/defold_agent.py snapshot-query --op get_node --id cube
python path/to/defold-1.13.1/scripts/agent/defold_agent.py snapshot-diff
python path/to/defold-1.13.1/scripts/agent/defold_agent.py state
python path/to/defold-1.13.1/scripts/agent/defold_agent.py hierarchy --collection /main/main.collection
python path/to/defold-1.13.1/scripts/agent/defold_agent.py create-go --collection /main/main.collection --id cube --position 0,0,0
```

Every command prints JSON on stdout.

`range.line` is 0-based (LSP). `line` is 1-based.

## Check (Godot `--check-only`)

`check` compiles and does **not** launch the game.

1. `bob --variant=debug --diagnostics-json=... build` — works with the editor closed.
2. If the editor is open and you pass `--editor`, it uses `POST /command/check` (compile only, same issue JSON). `/command/build` launches the game; do not use that as a syntax check.

Need tools:

- `DEFOLD_BOB` or `--bob` pointing at `bob.jar`
- a JDK on `PATH` if bob is a jar
- `DEFOLD_ENGINE` or `--engine` pointing at `dmengine` for `run` / `observe`

## Observe (structured runtime, not a screenshot loop)

```bash
python .../defold_agent.py observe --frames 30
python .../defold_agent.py snapshot-query --op get_node --id cube
python .../defold_agent.py snapshot-query --op find --type spritec
```

`observe` runs:

```text
dmengine build/default/game.projectc --quit-after-frames=30 --runtime-dump=.internal/agent/snapshots/_raw.json
```

It writes a **full** scene graph to `.internal/agent/snapshots/{id}.json` and returns only a **summary** (handle, roots, type counts, issues). The tree does not go into the chat.

Then query the file:

- `get_node` — one go / component (`world_position`, script properties)
- `find` / `list_ids` / `get_subtree` — search or a small slice
- `get_path` — JSON Pointer, e.g. `/scene_graph/children/0/world_position`

Do **not** `filesystem_manage read_text` a snapshot. Do **not** start the loop with a screenshot. Use `runtime_screenshot` / `observe --screenshot` only when you need to judge color, overlap, or layout that numbers cannot answer.

After two observes, `runtime_diff` / `snapshot-diff` compares the files (default previous vs latest). MCP `resources/list` exposes `defold://runtime/snapshot/{id}` as a handle; `resources/read` returns a summary, not the tree.

`runtime_screenshot` is optional and file-based (`screenshot.request` / `screenshot.ready`). Do not treat it as the main loop.

Authoring domains when the editor is closed: `atlas_manage`, `tilemap_manage`, `gui_manage`, `input_binding_manage`, `particlefx_manage`, `material_manage`, `camera_manage`, `render_manage`. Writes are `undoable: false` and `source: "disk"`.

`loop` is check + observe (still no screenshot unless you pass `--screenshot`).

## Live run (still files, never HTTP MCP)

```bash
python .../defold_agent.py project-run --mode live
python .../defold_agent.py observe
python .../defold_agent.py snapshot-query --op get_node --id cube
python .../defold_agent.py project-stop
```

`project-run --mode live` starts dmengine with `--agent-control=.internal/agent/control` and leaves it running. Later `observe` writes `dump.request`; the engine writes the scene graph to a file and `dump.ready`. Query that file. There is no HTTP MCP and no `GET /scene_graph` in the agent loop.

One live process per project. `project-stop` kills the pid recorded in `.internal/agent/engine.json`.

## Edit the live graph (no plugin)

`command` talks to the editor's first-party `POST /agent/command` when the project is open (Bearer from `.internal/editor.token`). Mutations go through the same graph APIs as the outline (Ctrl+Z works). If the editor is closed, a subset falls back to disk text (scripts, hierarchy parse, `game.project` settings). That disk path is **not** undoable.

```bash
python .../defold_agent.py command editor_state
python .../defold_agent.py command gameobject_create --params "{\"collection\":\"/main/main.collection\",\"id\":\"cube\",\"position\":[0,0,0]}"
python .../defold_agent.py command gameobject_create --params "{\"collection\":\"/main/main.collection\",\"id\":\"hat\",\"parent\":\"cube\"}"
python .../defold_agent.py command gameobject_manage --params "{\"op\":\"set_property\",\"collection\":\"/main/main.collection\",\"id\":\"cube\",\"property\":\"rotation\",\"value\":90}"
python .../defold_agent.py command filesystem_manage --params "{\"op\":\"list\",\"path\":\"/\"}"
python .../defold_agent.py command filesystem_manage --params "{\"op\":\"copy\",\"path\":\"/main/note.txt\",\"dest\":\"/main/note2.txt\"}"
python .../defold_agent.py command tilemap_manage --params "{\"op\":\"set_tile\",\"path\":\"/main/level.tilemap\",\"layer\":\"ground\",\"x\":2,\"y\":3,\"tile\":7}"
python .../defold_agent.py command gui_manage --params "{\"op\":\"set_node\",\"path\":\"/main/hud.gui\",\"id\":\"score\",\"property\":\"text\",\"value\":\"99\"}"
python .../defold_agent.py create-script --path /main/cube.script
python .../defold_agent.py patch-script --path /main/cube.script --old "function init(self)" --new "function init(self) -- hi"
python .../defold_agent.py command collection_manage --params "{\"op\":\"create\",\"path\":\"/main/level.collection\"}"
```

`batch_execute` runs steps in order. Authoring-only batches are one unit: with the editor closed, disk writes are journaled and a failed step rolls the batch back (`atomic: true`). With the editor open, the same `commands[]` is one `/agent/command` whose graph edits share one undo sequence; a failed step undoes that sequence and deletes files the batch created (`atomic: true`). If the batch mixes runtime/check/log tools, steps stay sequential (`atomic: false`).

stdio MCP (this CLI, no plugin, no HTTP URL):

```bash
python .../defold_agent.py mcp
python .../defold_agent.py mcp --exclude-domains atlas,tilemap
python .../defold_agent.py mcp-config --format cursor
python .../defold_agent.py mcp-config --write --project <dir>
```

Sample Cursor config: `scripts/agent/examples/cursor.mcp.json`. Or use **Help → Copy MCP Config** in the editor (stdio snippet only, never an HTTP URL). Clients should use `command` + `args`. Resources (`defold://editor/state`, `defold://collection/hierarchy?path=...`, `defold://project/mcp-config`, `defold://runtime/snapshot/{id}`, …) and prompts (`defold-observe`, `defold-live`, `defold-check`) are on the same stdio server.

Aliases (`create_gameobject`, `create_script`, `patch_script`, `add_component`, `node_set_property`, …) resolve in both the CLI dispatcher and the editor.

`api_manage` uses the editor `GET /ref` when it is open. If the editor is closed, the CLI searches `/*#` comments in this repo's engine sources (`go.set_position`, `msg.post`, …).

Do not curl `/agent/command` as the client protocol; use this CLI. Do not add `addons/` or `*.editor_script` for AI.

Authoring tree (`collection_get_hierarchy`) is not the running game. Runtime tree is `runtime_get_hierarchy` / `runtime_snapshot_query` against a snapshot file.

`session_manage op=list` shows this project's editor (from `.internal/agent/session.json`), the CLI live engine, other runtime targets, and other open editors in the user session registry. `session_activate` pins by `id` or `url`. Another project's editor cannot be driven from this MCP; start a stdio server with that `--project`.

## Diagnose (logs, check, authoring vs runtime)

```bash
python .../defold_agent.py check
python .../defold_agent.py logs --source all --severity error
python .../defold_agent.py diagnostics
python .../defold_agent.py snapshot-query --op compare_authoring --id cube --collection /main/main.collection
```

`logs_read` merges the editor console and `.internal/agent/engine.log` when `source=all`. Filter with `severity`, `domain` (SCRIPT / BUILD / GAMESYS / GRAPHICS / CRASH / …), and `q`. `issues[]` include Lua `stack` frames. `prints[]` are `DEBUG:SCRIPT` (`print` / `pprint`). `source=editor-file` reads the latest `editor2.*.log` from the Defold support directory.

`diagnostics_read` does **not** rebuild. It merges the last `project_check` (cached in `.internal/agent/last_check.json`), parsed logs, and issues from the latest snapshot. Use this after observe when asking “what is broken?”.

`runtime_snapshot_query op=compare_authoring` diffs one (or many) authoring GO transforms against the snapshot: local/world position, rotation, scale, parent. That is how you tell “the collection is wrong” from “a script moved it”.

`project_manage op=hot_reload` calls the open editor (`POST /command/hot-reload`). With only a CLI live engine, `project_stop` then `project_run` after `script_patch`.

Engine:

```
ERROR:SCRIPT: /main/player.script:12: attempt to index a nil value
          at: /main/player.script:12
stack traceback:
  /main/player.script:12: in function update
```

Bob:

```
ERROR:BUILD: /main/player.script:12: unexpected symbol near 'endd'
```

Live intervention (needs `project_run mode=live` and a dmengine built with `--agent-control`):

```bash
python .../defold_agent.py input --key left --hold 8
python .../defold_agent.py eval --code "return go.get_position('/cube')" --confirm
python .../defold_agent.py debug --op pause
python .../defold_agent.py debug --op step
python .../defold_agent.py debug --op set_breakpoint --file /main/player.script --line 12
python .../defold_agent.py debug --op continue
```

`runtime_input` writes `input.request` (keyboard / mouse). `game_eval` is off until `confirm=true` or `DEFOLD_AGENT_GAME_EVAL=1`; it is the same class as `/eval` — do not curl `/eval`. `runtime_debug` pause/step/breakpoints never sit at `debug>`.

Do not curl the editor as your main protocol; let `defold_agent.py` do that.

## Suggested iteration

1. `doctor` / `project_doctor` — confirm bob / dmengine / optional editor.
2. Write a collection, a go, a script.
3. `check` until `success` is true.
4. `observe --frames 30` — remember `data.snapshot.id`.
5. `snapshot-query --op get_node --id <go>` (and `find` if needed).
6. `diagnostics` if something failed; `compare_authoring` if a GO is not where the collection says.
7. If it is live and you need to press a key or pause a frame: `input` / `debug`. `eval --confirm` only when a read-only Lua check is the point.
8. Change one thing. Repeat.
