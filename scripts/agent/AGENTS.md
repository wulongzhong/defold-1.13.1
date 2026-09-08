# Defold agent loop

This project is a Defold game. Treat it like a Godot CLI session: edit text files, compile, run, look at a PNG, edit again. Do **not** install an editor plugin. Hierarchy / create-go / script patch are first-party (`defold_agent.py` + editor `/agent/command`), not an addon.

## Files you write

Defold sources are text:

- `game.project`
- `*.collection` — scenes
- `*.go` — game objects
- `*.script` / `*.gui_script` / `*.render_script` — Lua
- `*.atlas`, `*.tilemap`, `*.particlefx`, `input/*.input_binding`

Keep paths project-relative with a leading slash in Lua (`/main/player.script`).

## One command

From the project root (or this engine repo):

```bash
python path/to/defold-1.13.1/scripts/agent/defold_agent.py doctor
python path/to/defold-1.13.1/scripts/agent/defold_agent.py check
python path/to/defold-1.13.1/scripts/agent/defold_agent.py loop --frames 30 --screenshot .internal/agent/shot.png
python path/to/defold-1.13.1/scripts/agent/defold_agent.py state
python path/to/defold-1.13.1/scripts/agent/defold_agent.py hierarchy --collection /main/main.collection
python path/to/defold-1.13.1/scripts/agent/defold_agent.py create-go --collection /main/main.collection --id cube --position 0,0,0
```

Every command prints JSON on stdout:

```json
{
  "success": false,
  "source": "bob",
  "issues": [
    {
      "severity": "error",
      "resource": "/main/player.script",
      "line": 12,
      "range": { "start": { "line": 11, "character": 0 }, "end": { "line": 11, "character": 0 } },
      "message": "attempt to index a nil value"
    }
  ]
}
```

`range.line` is 0-based (LSP). `line` is 1-based.

## Check (Godot `--check-only`)

`check` compiles and does **not** launch the game.

1. `bob --variant=debug --diagnostics-json=... build` — works with the editor closed.
2. If the editor is open and you pass `--editor`, it uses `POST /command/check` (compile only, same issue JSON). `/command/build` launches the game; do not use that as a syntax check.

Need tools:

- `DEFOLD_BOB` or `--bob` pointing at `bob.jar`
- a JDK on `PATH` if bob is a jar
- `DEFOLD_ENGINE` or `--engine` pointing at `dmengine` for `run` / `loop`

## Run and look (Godot `--path .` + `--write-movie`)

```bash
python .../defold_agent.py run --frames 30 --screenshot .internal/agent/shot.png
```

That is:

```text
dmengine build/default/game.projectc --quit-after-frames=30 --screenshot=.internal/agent/shot.png
```

Then **open the PNG**. Compare it to what you intended. Fix files. `check` again. `loop` again.

`--debug-collisions` turns on the physics overlay (Godot `--debug-collisions`).

If the editor is open, `shot --resource /main/main.collection` uses `GET /preview/{path}` (often faster than launching).

## Edit the live graph (no plugin)

`command` talks to the editor's first-party `POST /agent/command` when the project is open (Bearer from `.internal/editor.token`). Mutations go through the same graph APIs as the outline (Ctrl+Z works). If the editor is closed, a subset falls back to disk text (scripts, hierarchy parse, `game.project` settings). That disk path is **not** undoable.

```bash
python .../defold_agent.py command editor_state
python .../defold_agent.py command gameobject_create --params "{\"collection\":\"/main/main.collection\",\"id\":\"cube\",\"position\":[0,0,0]}"
python .../defold_agent.py create-script --path /main/cube.script
python .../defold_agent.py patch-script --path /main/cube.script --old "function init(self)" --new "function init(self) -- hi"
python .../defold_agent.py command collection_manage --params "{\"op\":\"create\",\"path\":\"/main/level.collection\"}"
```

stdio MCP (still this CLI, still no plugin):

```bash
python .../defold_agent.py mcp
```

Do not curl `/agent/command` as the client protocol; use this CLI. Do not add `addons/` or `*.editor_script` for AI.

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
4. `loop --screenshot .internal/agent/shot.png`.
5. Read the PNG and `issues`. Change one thing. Repeat.
