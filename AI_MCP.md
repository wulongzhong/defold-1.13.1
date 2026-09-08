# Defold AI MCP 实作文档

状态: **路径 A 已落地，并已用源码加深路径 B 的功能面**（编辑器 `POST /agent/command` + `defold_agent.py`，**不是**工程插件 / FastMCP attach）。
对标: Godot **官方 CLI**（`--path` / `--check-only` / `--write-movie` / stdout `SCRIPT ERROR`）。社区插件 [hi-godot/godot-ai](https://github.com/hi-godot/godot-ai) 只提供工具清单参考，不是 Foundation 官方，本仓库也不按插件方式实作。
适用: 本仓库 Defold **1.13.1**
读者: 给 Codex / Cursor 用空工程写游戏的人

**已决定的路线：路径 A 的接法（改引擎 / 编辑器 / bob / agent CLI 源码）。路径 B 的能力做进同一套源码，不进 `addons/`，不写 `*.editor_script` 插件。**

**Godot 官方没有 AI MCP。** 视频里「空项目 + Codex 瞬间做出游戏、还能自己看画面改」走的也不是编辑器插件，而是官方 CLI + 文本工程 + Agent 自己写进项目的截图/日志闭环。godot-ai 那种 dock 插件是另一条路。

---

## 0. Godot 官方到底有什么

查到 2026-09 的事实：

| 层级 | 有没有 |
| --- | --- |
| `godotengine/godot` 引擎 / 编辑器 | 无 MCP、无内置 AI 助手、无 `/mcp` |
| `docs.godotengine.org` | 无 MCP 手册；也没有官方 `llms.txt` |
| 4.6 / 4.7 发行说明 | 无 AI / MCP 条目 |
| Asset Library / Asset Store | 只是社区上架通道。上架 ≠ Foundation 产品 |
| 官方提案 | 反复否决「做进核心」 |

提案侧（`godotengine/godot-proposals`）：

- [#12409](https://github.com/godotengine/godot-proposals/issues/12409) 内置 AI 助手：维护者关闭，**鼓励第三方 addon**。
- [#14090](https://github.com/godotengine/godot-proposals/issues/14090) LLM tie-in：维护者明确「不该进 Godot 本体，addon 已经能做，而且已经有一堆」。
- [#14918](https://github.com/godotengine/godot-proposals/discussions/14918) 官方 ACP / 官方 MCP：当作重复提案关掉；回复大意是 **捐赠不该花在这，插件 API 够用**。

所以上架 Asset Library 的「Godot AI」容易看起来像官方，其实和「Godot MCP Pro」「Godot MCP Toolkit」一样，都是第三方。`hi-godot` 不是 `godotengine`。

**Godot 官方真正提供的，是给插件用的编辑器 API**：`EditorPlugin`、`EditorInterface`、`EditorUndoRedoManager`、`EditorDebuggerPlugin`。MCP 协议、stdio attach、WebSocket 桥，全是插件作者自己长的。

和 Defold 对比：Defold 1.12–1.13 **引擎侧已经有**面向 Agent 的本机 HTTP（`/command/build`、`/eval`、`/ref`、`/preview`、`openapi.json`）。Godot 引擎侧连这层都没有。

---

## 0.1 视频里那种「空项目、没插件、还能调画面」是什么

那不是官方 MCP，也通常不是 `addons/godot_ai`。工程是空的，因为 Codex 打开的是一个只有 `project.godot` 的文件夹。后面发生的事全是 Agent 自己干的。

闭环是这样：

```text
Codex（会看图、会跑命令）
    │  直接读写文本文件
    ▼
project.godot / *.tscn / *.gd     ← Godot 场景和脚本本来就是文本
    │  官方可执行文件，不是插件
    ▼
godot --path .                    ← 跑游戏，stdout 就是报错
godot --path . -d                 ← 调试输出
godot --write-movie ...           ← 官方：把每一帧写成 PNG
    │
    ▼
Agent 自己写进工程的一小段游戏代码
（autoload / 本地 TCP / 截图存 /tmp）
    │
    ▼
PNG + 日志回到 Codex，再改文件，再跑
```

依据：

- Godot 官方 CLI：`--path`、`--headless`、`-d`、`--script`、`--write-movie`。见 [Command line tutorial](https://docs.godotengine.org/en/stable/tutorials/editor/command_line_tutorial.html)。
- 空项目开局、先让 Codex 自己造工具：Oliver Barnum, [Closing the Agentic Loop for Godot Games](https://oliverbarnum.wordpress.com/2026/02/05/closing-the-agentic-loop-for-godot-games/)（2026-02）。第一句就是「Create a new Godot project and open that folder in Codex」，让它写 TCP 测试服、截图、`AGENTS.md`。
- 工程外 MCP、**不往 addons 里装插件**：[Coding-Solo/godot-mcp](https://github.com/Coding-Solo/godot-mcp)。`npx @coding-solo/godot-mcp` 自己去找 `godot` 可执行文件，headless 跑自带的 `godot_operations.gd`。视频里空工程看不到任何 Plugin 勾选。

所以「自己调试画面」= Codex **看见了 PNG**（游戏里存的截图、Movie Maker 序列、或桌面端看窗口），对照日志再改 `.tscn` / `.gd`。不需要编辑器 dock，也不需要 WebSocket 插件。

两条路不要混：

| | 路径 A：文件 + 官方 CLI（视频里常见） | 路径 B：编辑器插件 MCP（godot-ai 等） |
| --- | --- | --- |
| 工程里看不看得见插件 | 看不见。工具在 Codex / 游戏脚本里 | `addons/` + Project Settings 勾选 |
| 改什么 | 磁盘上的文本 | 正在开着的编辑器内存图 |
| 跑游戏 | `godot --path .` | 插件调 Play |
| 看画面 | 游戏内截图 / `--write-movie` / Agent 看窗口 | `editor_screenshot` 等 tool |
| 要不要开编辑器 | 不必 | 要（或插件自己拉起） |
| 官方提供的部分 | CLI + 文本格式 + Viewport 截图 API | 只有 EditorPlugin API |

本仓库对齐「视频里那种手感」走路径 A。路径 B 的**功能**（树、建 go、挂 script、batch）已经做进编辑器源码和同一条 CLI，不是后加插件。

### 官方命令行调试到底强在哪

视频里「调试很牛」，一大半是 **Godot 官方把报错做成了给终端看的文本**，不是官方做了 AI Debugger。

Agent 只要 `godot --path .` 或 `godot --headless --script X.gd --check-only`，stdout 就是：

```
SCRIPT ERROR: Parse Error: Could not find type "void2" in the current scope.
          at: GDScript::reload (res://new_script2.gd:3)
ERROR: Failed to load script "res://new_script2.gd" with error "Parse error".
```

文件路径 + 行号 + 人话。Codex 不用接协议，读终端就能改对文件。

官方 CLI 里和 Agent 真正有关的（[文档](https://docs.godotengine.org/en/stable/tutorials/editor/command_line_tutorial.html)）：

| 开关 | 作用 | Agent 怎么用 |
| --- | --- | --- |
| `--path` | 指定工程 | 每次跑游戏 |
| `--headless --script --check-only` | 只解析、不跑，当编译器 | 写完 `.gd` 先查语法 |
| 直接跑 + 读 stdout | 运行时 `SCRIPT ERROR` / 栈 | 主循环 |
| `-d, --debug` | 本机 stdout 交互调试器 | **少用**。会停在 `debug>` 等输入，Agent 容易卡死 |
| `--debug-server tcp://host:port` | 官方远程调试协议 | 编辑器和部分插件走这条；视频一般不用 |
| `--debug-collisions` / `--debug-paths` / `--debug-navigation` | 画面上画碰撞/导航 | 配合截图，等于「看见物理是不是对」 |
| `--write-movie file.png` | 官方逐帧出图 | 视觉回归 |
| `--gpu-validation` / `--gpu-profile` | GPU 校验 / 剖析 | 渲染挂了才用 |
| `--import --quit` | 先导入资源再退 | 空工程第一次跑 |

远程调试协议（file / func / line / error / 栈帧）是官方的，编辑器 Debugger 面板也吃它。但空项目视频几乎不连这条 TCP，只吃 **stdout 那一层已经够结构化的报错**。

所以：官方做得好的是「CLI 即编译器 + 运行时错误带 `res://file:line`」。画面调试仍然是截图/Movie Maker，不是官方给了 AI 一双眼睛。

Defold 1.13.1 在落地路径 A 之前的缺口（现已补，见下一节）：

| Godot | 补之前的 Defold |
| --- | --- |
| `SCRIPT ERROR` + `res://x.gd:3` | Lua 有 traceback，但没有统一的 `file:line` / `at:` |
| `--check-only` | 只有 `bob` 和会**启动游戏**的 `/command/build` |
| `godot --path .` 跑起来 | `bob` + `dmengine`，没有一条 Agent CLI |
| `--write-movie` | 引擎里已有 `ReadPixels` / `dmRecord`，没有官方开关 |
| `--debug-collisions` | 只有 `physics.debug` / 运行时 toggle |

路径 A 要把 Defold 调试做成「和视频一样牛」，关键不是抄远程协议，而是：**构建错误 JSON 化、运行日志带脚本路径行号、截图落盘。** 这些已经做进本仓库。

### 路径 A 现在在仓库里的对应物

| Godot 官方 CLI | Defold（本仓库，已实现） |
| --- | --- |
| `godot --path .` | `scripts/agent/defold_agent.py run` → `bob --variant=debug build` + `dmengine` |
| `--headless --script --check-only` | `defold_agent.py check`；bob `--diagnostics-json=`；编辑器 `POST /command/check`（只编译不启动） |
| stdout `SCRIPT ERROR` + `res://file:line` | `ERROR:SCRIPT: /file:line: …` + `at: /file:line`；bob `ERROR:BUILD: /file:line: …` |
| 报错是纯文本 | **更好**：同一套 `{success, issues[{resource,line,range,message,severity}]}`，和编辑器 `/command/build` 同形 |
| `--write-movie` + `--quit` | `dmengine --quit-after-frames=N --screenshot=path.png`（一张 PNG，给 Agent 看） |
| `--debug-collisions` | `dmengine --debug-collisions`（等同 `physics.debug=1`） |
| `AGENTS.md` | `scripts/agent/AGENTS.md`（拷到游戏工程根目录） |

Agent 主入口：`scripts/agent/defold_agent.py`（`doctor` / `check` / `run` / `shot` / `loop` / `parse-log`）。Codex 读 stdout JSON，不要自己拼 curl。

Defold 对应路径 A（1.13.1 已有 + 本轮补齐）：

| Godot | Defold |
| --- | --- |
| `godot --path .` | `bob` 构建 + `dmengine` 跑 |
| `godot -d` 日志 | dmengine stdout / 编辑器 `/console` |
| `.tscn` / `.gd` 文本 | `.collection` / `.go` / `.script` 也是文本 |
| 游戏内截图脚本 | `dmengine --screenshot=`；编辑器开着时用 `/preview` |
| `AGENTS.md` 教 Agent 怎么跑 | 拷 `scripts/agent/AGENTS.md` 到工程根目录 |

---

## 1. 目标

先对齐视频里的路径 A，再视需要加路径 B。

**P0（路径 A）**：Codex 打开空 Defold 工程就能写文件、`bob`/`dmengine` 跑起来、读日志、拿截图自己改画面。工程里不必装编辑器插件。

**P1+（路径 B，可选）**：再加 editor script library，让 Agent 操作正在打开的编辑器（hierarchy、transact、`/preview`）。这是 godot-ai 那条路，不是空项目视频的前提。

---

## 2. 硬约束（跟 godot-ai v4）

1. **Codex / Cursor 只走 stdio MCP。** 配置必须是 `command` + `args`，禁止 `url = "http://..."`.
2. **入口命令叫 `defold-ai attach`。** 语义对齐 `godot-ai attach`：由客户端拉起或接管本机后端，编辑器没开也能先列出工具。
3. **Python FastMCP 拥有 MCP 协议、schema、鉴权、session。** 插件不讲 MCP。
4. **插件拥有编辑器变异。** 只有 Lua handler 可以碰 `editor.transact` / `editor.save`。
5. **工具面是「少量表 + 领域收口」。** 常驻 core + 高频具名 + `<domain>_manage(op, params)`，总 tool 数压在客户端约 100 的上限以下。
6. **结构化错误码。** 禁止把 Python traceback 或 Lua stack 直接丢给 Agent。
7. **写入可撤销。** 场景/资源图变异走 `editor.transact`；纯文件写入必须在回包里标明 `undoable=false`。
8. **只绑定 loopback。** 后端默认 `127.0.0.1`。

不跟的只有一处：Godot 插件和 Python 之间是 WebSocket `:9500`，因为 Godot 插件没有 HTTP 服务。Defold 编辑器已经有本机 HTTP，editor script 也不能当 WebSocket 服务端，所以 **这一跳改成「Python 作为 HTTP 客户端，打编辑器已有端口」**。这是实现替换，不是把 Codex 改回 HTTP。

---

## 3. 进程与传输

godot-ai v4 官方 Codex 配置：

```toml
[mcp_servers."godot-ai"]
command = "godot-ai"
args = ["attach", "--port", "8000", "--ws-port", "9500"]
enabled = true
startup_timeout_sec = 60
tool_timeout_sec = 360
```

我们的对应配置：

```toml
[mcp_servers."defold-ai"]
command = "defold-ai"
args = ["attach", "--port", "8000"]
enabled = true
startup_timeout_sec = 60
tool_timeout_sec = 360
```

没有 `--ws-port`。编辑器端口不写死，从打开的工程读取。

### 三跳（只有第一跳是客户端协议）

```text
Codex / Cursor
    │  stdio MCP（JSON-RPC）
    ▼
defold-ai attach
    │  本机 authenticated streamable-http :8000（内部，客户端看不见）
    ▼
Python FastMCP 后端
    │  长连接复用的 HTTP + Bearer（内部，对标 Godot 的 WS :9500）
    ▼
Defold EditorPlugin（.editor_script）
    │  editor.transact / editor.get / editor.save
    │  以及编辑器自带 /command /ref /preview /console
    ▼
正在打开的工程
```

| 跳 | 协议 | 谁看得见 |
| --- | --- | --- |
| Client → `attach` | stdio MCP | Codex 配置里写的就是这条 |
| `attach` → FastMCP `:8000` | 带鉴权的 streamable-http | 内部。attach 拉起或接管后端 |
| FastMCP → 编辑器插件 | `127.0.0.1` HTTP + `Authorization: Bearer <editor.token>`，连接池复用 | 内部。对标 Godot WS |

v3 那种 `url = "http://127.0.0.1:8000/mcp"` **禁止作为默认。** godot-ai v4 已写明：裸 URL 不能鉴权，也不能跟着 capability 轮换。

### 编辑器发现（按顺序）

1. 环境变量 `DEFOLD_AI_EDITOR_URL`（调试用）。
2. 当前工作目录（以及父目录）的 `.internal/editor.port` + `.internal/editor.token`。这是 1.13.1 编辑器自己写的。
3. 最近一次插件 boot 写下的能力文件（见 §9）。多开工程时，**禁止**用 `~/.defold_ai_url` 这种「后写覆盖」；必须按 project path / session 区分。

### 为什么这样快（必须保住）

- stdio 会话常驻，工具表已经在。
- Python↔编辑器连接复用，不是每条命令新开 TCP+TLS。
- handler 改的是编辑器内存图，一次 `transact` = 一次 Ctrl+Z。
- 预览走 `GET /preview/{path}`，文档走 `GET /ref?q=`，构建走 `POST /command/build`，都由 **后端** 去调，Agent 不 curl。

---

## 4. 代码放哪

插件 **不进引擎 C++**。跟 Godot 一样：一份可安装的 addon + 一份 Python 包。

建议本仓库内的落点（实现阶段再建目录，本文先定边界）：

```text
editor/ai-mcp/
├── plugin/                         # 游戏工程里的 library
│   └── defold_ai/
│       ├── defold_ai.editor_script # 入口：路由 + 菜单命令
│       ├── game.project            # 作为 library 被依赖时的清单
│       ├── handlers/               # 一领域一文件
│       │   ├── editor.lua
│       │   ├── collection.lua
│       │   ├── gameobject.lua
│       │   ├── component.lua
│       │   ├── script.lua
│       │   ├── resource.lua
│       │   ├── filesystem.lua
│       │   └── project.lua
│       ├── protocol/
│       │   ├── errors.lua          # 与 Python protocol/errors.py 对齐
│       │   └── envelope.lua
│       └── lib/
│           └── util.lua
└── server/                         # Python 包 defold-ai
    ├── pyproject.toml
    └── src/defold_ai/
        ├── __main__.py             # python -m defold_ai / defold-ai attach
        ├── attach.py               # stdio 桥，对标 godot-ai attach
        ├── server.py               # FastMCP 入口
        ├── transport/
        │   └── editor_http.py      # 发现端口、Bearer、连接池、超时
        ├── sessions/
        │   └── registry.py
        ├── protocol/
        │   └── errors.py
        ├── tools/                  # MCP tool 包装
        │   ├── _meta_tool.py       # register_manage_tool
        │   ├── domains.py
        │   └── ...
        └── handlers/               # 转成 editor command，不直接改工程文件
```

游戏工程安装方式（对标 `addons/godot_ai/`）：

```ini
# game.project
[project]
dependencies#0 = https://.../defold-ai/plugin/defold_ai.zip
```

打开工程后编辑器自动发现 `*.editor_script`，无需 Plugin 勾选。菜单里加一条 `Help → Defold AI: Copy Codex config`，对标 Godot AI dock 的 Configure。

---

## 5. 插件职责

入口只做三件事：注册路由、写能力文件、提供人工配置命令。

```lua
-- defold_ai.editor_script（示意）
local M = {}

function M.get_http_server_routes()
    return {
        http.server.route("/defold-ai/command", "POST", "json", handle_command),
        http.server.route("/defold-ai/status", "GET", handle_status),
    }
end

function M.get_commands()
    return {
        {
            label = "Defold AI: Copy Codex config",
            locations = { "Help" },
            run = copy_codex_config,
        },
    }
end

return M
```

### 命令入口

`POST /defold-ai/command`

请求（对标 Godot WS command）：

```json
{
  "request_id": "uuid",
  "command": "get_hierarchy",
  "params": { "path": "/main/main.collection", "depth": 8 }
}
```

成功：

```json
{
  "request_id": "uuid",
  "status": "ok",
  "data": {},
  "readiness": "ready"
}
```

失败：

```json
{
  "request_id": "uuid",
  "status": "error",
  "readiness": "ready",
  "error": {
    "code": "NOT_FOUND",
    "message": "Game object '/main/Player' not found",
    "hint": "Use collection_get_hierarchy and pass an existing id."
  }
}
```

鉴权：必须带 `Authorization: Bearer <内容与 .internal/editor.token 相同>`。没有或错了回 401。编辑器内建 `/eval` 已经是这套，插件走同一 token，不再发明第二种。

### 并发

Godot 的规则：禁止在 WebSocket 回调里碰 `EditorInterface`，命令排队、按帧预算在 `_process` 里做。

Defold editor script 的 HTTP handler 已经在编辑器运行时里执行，但仍须：

- 每个请求 `pcall`，Lua error → `HANDLER_ERROR`，不要打崩路由。
- 写入前读 readiness；`building` 时除 `project_manage(op="status")` 外拒绝写入。
- 一次请求只做一次 `editor.transact`（`batch_execute` 也是合成一个事务，失败则整笔不算）。
- 大列表（hierarchy、search）必须 `offset` / `limit`。

### 变异合同

```lua
editor.transact({
    editor.tx.add(collection, "children", {
        type = "go",
        id = "Player",
        position = { 0, 0, 0 },
    }),
})
editor.save()
```

- 图变异：`editor.tx.add` / `set` / `remove` / `reorder` / `reset`。
- 缺依赖时（例如加 sprite 还没有 atlas）能自动建的，放进 **同一个** transact，回包带 `*_created: true`。
- `editor.save()` 在事务成功后由 handler 决定；`project_run` 默认 autosave，允许 `autosave=false`。

---

## 6. CLI 职责（源码，不是 attach 插件）

`scripts/agent/defold_agent.py` 只做编排。编辑器开着时走 `POST /agent/command`（Clojure 改图，可撤销）。编辑器关着时，部分命令才直接改磁盘文本，回包必须带 `source: "disk"` / `undoable: false`。

**不实现** `defold-ai attach`、FastMCP `:8000`、工程内 editor script。stdio MCP 就是 `defold_agent.py mcp`。

落点：

| 能力 | 源码 |
| --- | --- |
| 命令分发 | `editor/src/clj/editor/agent.clj` → `POST /agent/command` |
| 图变异 | `collection/add-embedded-game-object!`、`game-object/add-*-component!`、`g/transact` |
| CLI / stdio MCP | `scripts/agent/defold_agent.py`、`agent_ops.py`、`agent_mcp.py` |
| 错误码 | 信封 `{status, readiness, error.code}`，业务失败 HTTP 200 |
| session | `.internal/agent/session.json` + 用户级 registry；`session_manage list` 列本工程编辑器 / CLI live / 其它已开编辑器 |

`batch_execute`：纯作者态时整笔回滚（磁盘 journal，或编辑器开着时同一 `operation-sequence` + 失败 `g/undo!`），回包 `atomic: true`。混有 runtime/check/log 时仍逐步执行，回包 `atomic: false`。

---

## 7. 工具面

命名用 Defold 词，不要用 Godot 词。`node_create` → `gameobject_create`，`scene_open` → `collection_open`。

### v0（先做这些，才能叫 MCP）

常驻 core（始终加载，对标 godot-ai 四件套）：

| Tool | 作用 |
| --- | --- |
| `editor_state` | 版本、工程名、当前打开资源、readiness、是否在 build |
| `collection_get_hierarchy` | 分页走 collection 树 |
| `gameobject_get_properties` | 一个 go / component 的属性快照 |
| `session_activate` | 钉到某个已连接编辑器 |

高频具名：

| Tool | 作用 |
| --- | --- |
| `collection_open` / `collection_save` | 打开、保存 |
| `gameobject_create` | 在 collection 里加 go |
| `component_add` | script / sprite / model / camera / collisionobject / label / sound / particlefx |
| `script_create` / `script_attach` / `script_patch` | 建、挂、锚点补丁 |
| `project_build` | 调编辑器 `POST /command/build`，阻塞到结束，回 issues |
| `logs_read` | 读 `GET /console` + 插件自己的 ring |
| `editor_preview` | 调 `GET /preview/{path}`，把 PNG 交给 MCP |
| `batch_execute` | 多条 **插件 command 名** 同一事务；失败整笔回滚 |

领域收口（每个都是 `op` + `params`）：

| Tool | v0 要有的 op |
| --- | --- |
| `collection_manage` | `create`, `add_instance`, `remove_instance`, `get_roots` |
| `gameobject_manage` | `delete`, `rename`, `set_property`, `find` |
| `component_manage` | `remove`, `set_property` |
| `script_manage` | `read`, `detach` |
| `filesystem_manage` | `read_text`, `write_text`, `search` |
| `project_manage` | `settings_get`, `settings_set`, `stop` |
| `editor_manage` | `state`, `selection_get`, `quit` |
| `session_manage` | `list` |
| `api_manage` | `get` → 转发 `GET /ref?q=` |

`batch_execute.commands[].command` 用 MCP 名（`gameobject_create`）。也认别名 `create_gameobject`。

### 只读 resources（stdio `resources/list` + `resources/read`，不是 HTTP MCP）

```
defold://sessions
defold://editor/state
defold://collection/current
defold://collection/hierarchy
defold://gameobject/{path}/properties
defold://script/{path}
defold://project/info
defold://ref/{query}
```

### v1（core 稳定后再做）

`material_manage`、`particlefx_manage`、`atlas_manage`、`tilemap_manage`、`input_binding_manage`、`render_manage`、`camera_manage`、preset 库、`custom_manage`（给别的 editor script 挂工具）。

运行时灌输入 / `game_eval` / 非交互 debugger：走 `--agent-control` 的 `input.request` / `eval.request` / `debug.request`。不 curl `/eval`，不停 `debug>`。`game_eval` 需 `confirm=true`。

---

## 8. 词汇对照

| Godot / godot-ai | Defold |
| --- | --- |
| Scene `.tscn` | Collection `.collection` |
| Node | Game object（embedded 或 `.go`） |
| GDScript | Lua `.script` / `.gui_script` / `.render_script` |
| Autoload | collection proxy 或 bootstrap collection |
| Signal | `msg.post` |
| AnimationPlayer | `go.animate` / GUI animation |
| ClassDB | `GET /ref` |
| EditorUndoRedoManager | `editor.transact` |
| `addons/godot_ai/plugin.cfg` | library + `*.editor_script` |
| WebSocket `:9500` | 编辑器已有 HTTP + Bearer |
| `_mcp_game_helper` + debugger | 无对等物（v1 再评估） |

---

## 9. 鉴权与 session

两套独立秘密，对标 godot-ai v4「HTTP capability 与 WS capability 分开」：

| 秘密 | 用途 | 来源 |
| --- | --- | --- |
| FastMCP `:8000` capability | attach 认后端 | 用户私有目录，例如 `%LOCALAPPDATA%\Defold\ai-mcp\capability.json`（POSIX 权限 0600） |
| `.internal/editor.token` | 后端认编辑器 | 编辑器每会话已有 |

插件 boot 时写一份 **按工程分条** 的状态，供 attach 发现，禁止单文件互踩：

```json
{
  "schema": 1,
  "session_id": "hello-cube@7f9c3a10d8e426b1",
  "project_path": "C:/Games/hello-cube",
  "editor_url": "http://127.0.0.1:51743",
  "editor_pid": 12345,
  "plugin_version": "0.1.0",
  "defold_version": "1.13.1",
  "readiness": "ready"
}
```

Windows 上 token 不防同一台机器的其他本地账户，和 godot-ai 的声明一致。默认不提供 `--allow-host`。

### readiness

| 值 | 含义 | 写入 |
| --- | --- | --- |
| `ready` | 可写 | 允许 |
| `loading` | 工程/库还在加载 | 拒绝 |
| `building` | `/command/build` 进行中 | 拒绝（status 除外） |
| `no_collection` | 没有可编辑的 collection | 只允许建 collection / 读工程 |

每条 command 回包带 `readiness`。Python 侧缓存；写入前若缓存不可写，先打一次 `editor_state` 再决定。不要给每个 tool 自己写重试环。

---

## 10. 客户端配置（必须一键能抄）

第一方菜单 **Help → Copy MCP Config**（不是工程插件）把 Cursor 用的 stdio 片段拷到剪贴板。CLI 仍可用 `mcp-config --write`。Codex TOML 用 `--format codex`。写出形态：

**Codex** (`~/.codex/config.toml`)：

```toml
[mcp_servers."defold-agent"]
command = "python"
args = ["C:/GameProject/defold-1.13.1/scripts/agent/defold_agent.py", "mcp"]
enabled = true
startup_timeout_sec = 60
tool_timeout_sec = 360
```

**Cursor** (`.cursor/mcp.json`)：

```json
{
  "mcpServers": {
    "defold-agent": {
      "command": "python",
      "args": ["C:/GameProject/defold-1.13.1/scripts/agent/defold_agent.py", "mcp"]
    }
  }
}
```

把路径换成你本机仓库。禁止默认写入 `http://127.0.0.1:8000/mcp`。不要 `attach`，不要工程插件。

---

## 11. 错误码

与 Python `protocol/errors.py`、Lua `protocol/errors.lua` 双端同一份表。新增码必须两边一起加。

| code | 何时 |
| --- | --- |
| `MISSING_PARAM` | 缺必填 |
| `INVALID_PARAM` | 类型/范围不对 |
| `NOT_FOUND` | 资源 / go / 路径不存在 |
| `NOT_ALLOWED` | 当前编辑器状态不允许 |
| `EDITOR_NOT_READY` | readiness 门闩；`data.sub_code` 写具体状态 |
| `INVALID_PATH` | 路径越出工程或格式非法 |
| `OLD_TEXT_NOT_FOUND` | `script_patch` 锚点 0 次 |
| `MULTIPLE_MATCHES` | `script_patch` 锚点多于 1 次 |
| `UNKNOWN_OP` | manage 不认识的 op，带 `data.suggestions` |
| `UNKNOWN_COMMAND` | 插件 dispatcher 不认识 |
| `UNAUTHORIZED` | Bearer 错 |
| `HANDLER_ERROR` | Lua `pcall` 失败，message 可含短 traceback |
| `EDITOR_UNREACHABLE` | Python 找不到 `.internal/editor.port` 或连不上 |

HTTP 层：鉴权失败 401；业务失败仍 **200 + `status=error`**（和 Godot 信封一致，避免 MCP 把 404 吃成传输错误）。

---

## 12. 分阶段

### P0 — 对齐视频：空工程 + CLI + 看图（路径 A）✅

不装编辑器插件。给 Codex 的是 `scripts/agent/AGENTS.md` + `defold_agent.py`。

已落地：

- `scripts/agent/defold_agent.py`：`doctor` / `check` / `run` / `shot` / `loop` / `parse-log` / `command` / `state` / `hierarchy` / `create-go` / `mcp`，stdout 一律 JSON
- 编辑器 `POST /agent/command`：路径 B 工具面（hierarchy / create-go / script_patch / manage），源码不是插件
- `bob --diagnostics-json=`：issues 带 `resource` / `line` / LSP `range`；stdout 另打 `ERROR:BUILD: file:line:`
- 编辑器 `POST /command/check`：只编译不启动（Godot `--check-only`；`/command/build` 仍会跑游戏）
- `dmengine --quit-after-frames=N --screenshot=path.png --runtime-dump=path.json --agent-control=dir --debug-collisions`（live 另认 `screenshot.request`）
- `defold_agent.py observe` / `runtime_observe`：完整 scene_graph 落盘，MCP 默认只回摘要；`runtime_snapshot_query` 从文件取切片。live 走 `--agent-control` 文件握手。截屏不是默认观察；`runtime_screenshot` 同样走文件。
- Lua 运行时：`ERROR:SCRIPT: file:line: message` + `at: file:line`（能从 traceback 补位置）
- `scripts/agent/AGENTS.md`：空工程模板

验收：空工程里说「做个能走的方块」，Agent 能 `check`、能 `loop`、能根据 `.internal/agent/shot.png` 改位置/颜色，直到对上。需要本机 `bob.jar` + `dmengine`（或开着的编辑器做 check / preview）。

### P0b — 工程外 MCP，仍不往工程里塞插件 ✅

`python scripts/agent/defold_agent.py mcp`：stdio JSON-RPC（Content-Length），工具名就是路径 B 那套。编辑器可关；关着时走磁盘回退。

### P1 — 路径 B 功能，源码实作（不是插件）✅

- 编辑器 `POST /agent/command` + `GET /agent/state`（Bearer，和 `/eval` 一样）
- Clojure `editor.agent/handle`：走 collection / game-object 图 API，创建可 Ctrl+Z
- CLI：`state` / `hierarchy` / `create-go` / `create-script` / `patch-script` / `command`
- core 四件套 + 具名工具 + v0 manage op
- `script_patch` 唯一锚点（`OLD_TEXT_NOT_FOUND` / `MULTIPLE_MATCHES`）
- `api_manage` → `GET /ref`；`logs_read` → `GET /console`；`editor_preview` → `GET /preview`
- **没有** `defold-ai attach`、FastMCP `:8000`、工程内 `*.editor_script`

验收：Codex 问「当前 collection 树是什么」，开着编辑器时回到真实节点；「创建一个名为 Cube 的 go」，编辑器里能 Ctrl+Z 掉。编辑器关着时，hierarchy / script / 部分 create 走磁盘文本。

### P2 — 对照与领域 ✅（需求 R2）

- `runtime_diff`、MCP `defold://runtime/snapshot/{id}`
- `runtime_state.targets` 多 target 列表（仍默认一个当前）
- atlas / tilemap / tilesource / font / sound / gamepads / display_profiles / model / factory / collectionproxy / collisionobject / gui / input / particlefx / material / camera / render 的 manage
- 磁盘回退：`collection_manage` / `gameobject_manage` / `component_add` / `script_attach`；stdio `--exclude-domains`
- 不做 HTTP MCP；不做 custom tool 注册 / 签名更新

### P3 — 干预 ✅（需求 R3）

- `runtime_input` / `game_eval` / `runtime_debug` 走 `--agent-control` 文件（`input.request` / `eval.request` / `debug.request`）
- `game_eval` 默认关（`confirm=true` 或 `DEFOLD_AGENT_GAME_EVAL=1`），源码预算 + 禁 os/io/socket
- `runtime_debug` 可 pause/step/breakpoint，断点命中时抓 Lua 栈和 locals；`stack`/`continue` 可读栈并恢复，回包 `prompt: false`，主环不停 `debug>`
- 不注册编辑器 `POST /eval` 或 mobdebug TCP 为 tool

### 明确不做（直到引擎有能力）

- 给 Codex 配 HTTP URL。
- 让 Agent 直接 curl `/eval` 当主协议。
- 在插件里实现完整 MCP JSON-RPC（那是 Python 的事）。vlaaad gist 那种「editor script 自己当 MCP」只作参考，不作主方案。
- 把编辑器 `POST /eval` 或 mobdebug 交互提示当 Agent 主协议。干预用控制文件：`runtime_input` / `game_eval` / `runtime_debug`。
- 把 WebSocket 塞进引擎，除非 P0/P1 证明确实被 HTTP 往返拖死。

---

## 13. 和现有编辑器 HTTP 的关系

1.13.1 已经有、**后端可以调用、Agent 不准当主协议** 的接口：

| 端点 | 插件 / 后端怎么用 |
| --- | --- |
| `GET /openapi.json` | 调试时看内建能力 |
| `POST /command/build` | `project_build` |
| `GET /console` | `logs_read`（`source=all` 还会合并 `engine.log`；`diagnostics_read` 再叠上次 check 和快照 issues） |
| `POST /command/hot-reload` | `project_manage op=hot_reload`（只要编辑器开着） |
| `GET /ref?q=` | `api_manage` |
| `GET /preview/{path}` | `editor_preview` |
| `POST /eval` | 编辑器扩展运行时。不注册成 MCP tool。运行时 Lua 用 `game_eval` 控制文件 |
| `.internal/editor.port` / `editor.token` | 发现与鉴权 |

`POST /agent/command` 是编辑器源码里的命令面，对应 Godot 插件那条 WS command。不要把每个 op 再暴露成一条 REST，也不要在游戏工程里加插件路由。

---

## 14. 和社区移植的关系

- [estebanrfp/defold-ai](https://github.com/estebanrfp/defold-ai)：godot-ai 的早期移植，editor script + FastMCP，约 35 个 tool。可借 handler 词汇和 `transact` 写法。它把 Python 当「stdio↔HTTP 薄代理」，**没有 attach / session / readiness / 工具收口**，客户端协议也偏 HTTP。本规格不 fork 它的进程模型。
- [vlaaad gist](https://gist.github.com/vlaaad/395bd021e8a4ba6561fd4f8d3562456f)：editor script 直接讲 MCP。只作协议对照，不采用。

---

## 15. 参考

- godot-ai 仓库: https://github.com/hi-godot/godot-ai
- 插件架构: https://github.com/hi-godot/godot-ai/blob/main/docs/plugin-architecture.md
- 工具表: https://github.com/hi-godot/godot-ai/blob/main/docs/TOOLS.md
- 客户端配置（含 Codex `attach`）: https://github.com/hi-godot/godot-ai/blob/main/docs/client-configuration.md
- 本编辑器 HTTP: `editor/doc/http-api.md`
- editor script 路由示例: `editor/test/resources/editor_extensions/http_server_project/test.editor_script`
- editor script 模板: `editor/resources/templates/template.editor_script`
