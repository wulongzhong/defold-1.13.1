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
python .../defold_agent.py create-script --path /main/cube.script
python .../defold_agent.py patch-script --path /main/cube.script --old "function init(self)" --new "function init(self) -- hi"
python .../defold_agent.py command collection_manage --params "{\"op\":\"create\",\"path\":\"/main/level.collection\"}"
```

`batch_execute` runs steps in order. It is **not** one undo. Success and failure both set `atomic: false` and `undoable_separately: true`. Prior steps stay applied.

stdio MCP (still this CLI, still no plugin):

```bash
python .../defold_agent.py mcp
```

Do not curl `/agent/command` as the client protocol; use this CLI. Do not add `addons/` or `*.editor_script` for AI.

Authoring tree (`collection_get_hierarchy`) is not the running game. Runtime tree is `runtime_get_hierarchy` / `runtime_snapshot_query` against a snapshot file.

## Logs you can read without the CLI

Engine:

```
ERROR:SCRIPT: /main/player.script:12: attempt to index a nil value
          at: /main/player.script:12
```

Bob:

```
ERROR:BUILD: /main/player.script:12: unexpected symbol near 'endd'
```

Do not use the interactive debugger. Do not curl the editor as your main protocol; let `defold_agent.py` do that.

## Suggested iteration

1. `doctor` — confirm bob / dmengine / optional editor.
2. Write a collection, a go, a script.
3. `check` until `success` is true.
4. `observe --frames 30` — remember `data.snapshot.id`.
5. `snapshot-query --op get_node --id <go>` (and `find` if needed).
6. Change one thing. Repeat.
