# Defold AI MCP 验收规范

状态: **合同**（用例编号是唯一验收单位）
适用: 本仓库 Defold **1.13.1**，用户路径 = 打包 Windows 编辑器
读者: 做验收的人 / Agent；以及修 CLI、编辑器、引擎的人

| 文档 | 职责 |
| --- | --- |
| [`AI_MCP_REQUIREMENTS.md`](AI_MCP_REQUIREMENTS.md) | 产品需求。本文每条用例必须能指回它的章节 |
| [`AI_MCP.md`](AI_MCP.md) | 实现与历史。验收不读它当通过标准 |
| [`scripts/agent/AGENTS.md`](scripts/agent/AGENTS.md) | Agent 日常用法 |
| **本文** | **唯一**可判定通过 / 失败的验收合同 |

没有编号的检查、聊天里临时想到的断言、只看 `status == ok`，都不算验收。要加检查，先改本文，再改执行器。

执行器：`.cache/run_mcp_acceptance.py`（gitignore）。它必须按本文 **ID** 打印和写报告，不得另起一套名字。

**退役：** 旧 `L*` / `D*` 编号以及 P1–P60「已跑过」印章全部作废。执行器不得再打印或写入那些编号。历史绿标不能继承。

---

## 1. 验收对象

本轮只验这一条用户路径：

1. 本轮 `main` push 对应的 Hosted desktop run 一旦打出 `Defold-x86_64-win32`，就只下这个 zip 换包（不等 macOS editor；不下载 `bob-jar` / 单独 `dmengine`）。
2. 解压到仓库 `.cache/`，用自带 JRE 的 `Defold.exe` **只打开一次**官方样例。
3. 用本机已有 Python 跑 `scripts/agent/defold_agent.py`（stdio MCP 同一套 dispatch）。
4. 编辑器开着。check 走 `POST /command/check`。**整表只拉一次** live（`project_run mode=live`）。关编辑器 / 再 live 只允许出现在表尾 X 段。

唯一工程：官方 [sample-pixel-line-platformer](https://github.com/defold/sample-pixel-line-platformer)，克隆到 `.cache/mcp-demo-platformer/`。标题保持 `PixelLinePlatformer`。不改官方玩法脚本；临时文件只写 `/mcp/`；表尾恢复 `game/game.collection`。

不是验收对象：`.cache/mcp-acceptance-game/` 烟测方块工程、`bob-jar-*` artifact、单独的 `dmengine-x86_64-win32` artifact、系统 JDK、本仓库当游戏工程、`test_defold_agent.py` 单测。

---

## 2. 硬约束

违反任一条，整次验收作废，即使工具全绿。

| ID | 约束 | 对应 |
| --- | --- | --- |
| ENV-01 | 不装 JDK，不改 `PATH` / `JAVA_HOME`，不用 winget/choco/scoop | 用户路径 |
| ENV-02 | 不把 `bob-jar-*`、单独引擎 artifact 当验收对象 | 用户路径 |
| ENV-03 | 不用系统 `java -jar bob.jar` 做 check / observe | 需求 §4、§7.2 |
| ENV-04 | `project_doctor.java == false` **不是**失败；`java_required` 必须是 `false` | 需求 §3.4 |
| ENV-05 | 只下 Hosted desktop 的 `Defold-x86_64-win32` | CI |
| ENV-06 | 唯一工程是 `.cache/mcp-demo-platformer/`，不是烟测方块、不是引擎仓库 | 隔离 |
| ENV-13 | 工程来自官方 `defold/sample-pixel-line-platformer`。不改官方脚本逻辑，不加 `addons/` | 需求 §3 |
| ENV-07 | `Defold.exe --preferences <demo>/prefs.json <demo>/game.project` | 不改用户 Defold 配置 |
| ENV-08 | 不新装 pip 包；只用已有 Python | 环境 |
| ENV-09 | 客户端协议是 stdio MCP：`command` + `args`，禁止 HTTP URL | 需求 §4.1、§12 |
| ENV-10 | Agent 不 curl `/agent/command`、`/eval`、`/scene_graph` 当主协议 | 需求 §4.3、§15.1 |
| ENV-11 | 不往游戏工程加 `addons/` 或 `*.editor_script` | 需求 §4.2、§12 |
| ENV-12 | 不 `filesystem_manage read_text` 快照 JSON | 需求 §7.8 |

---

## 3. 全局通过定义

一条用例 **通过**，当且仅当下面全部成立：

1. 回包是统一信封：`{status, readiness, data|error}`。业务失败是 `status=error` + 稳定 `error.code`，不是 Python / Lua / Clojure traceback 当主文案。
2. **读回**了本文「断言」列里的字段或文件内容。`status=ok` 单独不够。
3. `source` 分得开：作者态 `editor` 或 `disk`；运行态 `runtime`；预览 `editor-preview`。
4. 写成功后，作者态能在 hierarchy / 磁盘 / `get` 上看到；运行态能在快照 query 上看到。
5. 没有做「禁止」列里的事。

签字：§5 全部 **P0** 通过，且 §2 硬约束未破。  
S/A/C/R/M/T/N/X **必须实跑**；漏跑 = 失败。禁止把没跑的条目标成通过。仅 R-16（可选截屏）允许 `SKIP`。

会话约束：A/C 阶段不得 `project_run`。R+M+T+N 共用同一次 live，不得 `project_stop`。X 之前不得杀编辑器。单次调用通过不够：M 段连打上限，T 段随机 30–60 分钟。

---

## 4. 准备（每次复测）

| 步骤 | 动作 | 通过 |
| --- | --- | --- |
| S-01 | `gh run list --workflow "Hosted desktop" --branch main`，等到**本轮 push** 的 run 已有 artifact `Defold-x86_64-win32` | 有 zip |
| S-02 | 停掉旧 `Defold.exe` / 测试工程相关 `java.exe` / `dmengine.exe` | 旧进程不挡 |
| S-03 | 本轮 `main` push 对应的 Hosted desktop run 一旦打出 `Defold-x86_64-win32`，就停旧进程、删旧解压、只下这个 zip（不等 macOS editor；不下载 `bob-jar` / 单独 `dmengine`） | `Defold.exe` 存在 |
| S-04 | jar 内 `libexec/x86_64-win32/dmengine.exe` 含 `agent-control`、`runtime-dump`、`quit-after-frames` | 注入成功 |
| S-05 | 官方样例保留官方文件。临时只写 `/mcp/` | 工程能打开 |
| S-06 | **启动一次**编辑器，等到**新的** `.internal/editor.port` + `editor.token` | `editor_state.project_title` 是 `PixelLinePlatformer` |
| S-07 | 设 `DEFOLD_EDITOR`；**取消** `DEFOLD_ENGINE`、`DEFOLD_BOB` | doctor 的 engine 路径含 unpack 或 `agent-engine` 或本 zip，不含 `hosted-desktop` / `bob-jar` / `dynamo_home` |
| S-08 | 克隆官方样例到 `.cache/mcp-demo-platformer/`（可删 `docs/`）。写隔离 `prefs.json` | 有 `game/game.collection`、`game/player.script`、`game/level.tilemap` |

失败闭环（产品问题，不是用例写错）：

```text
修源码 → commit + push main → 等本次 Hosted desktop 的 `Defold-x86_64-win32`
→ 停进程 → 换 zip → 从 S-06 再来 → §5 整表（含 T）重跑
```

禁止：修完只复测失败的那一条。禁止每个 ID 后重开引擎。

---

## 5. 用例

优先级：**P0** = 签字必须过。其余失败同样要修，不得从合同删掉。

断言里的「约等于」：坐标误差 ≤ 1.5（作者态）或移动判定为 x 至少减少 0.5（输入后）。

主 collection：`/game/game.collection`。

### 5.1 准备（S）

| ID | P | 需求 | 步骤 | 断言 |
| --- | --- | --- | --- | --- |
| S-08 | P0 | §3 | 确认官方样例在 `.cache/mcp-demo-platformer/` | 有 `game/game.collection`、`game/player.script`、`game/level.tilemap` |

### 5.2 作者态（A）— 编辑器开，无 live

| ID | P | 需求 | 步骤 | 断言 |
| --- | --- | --- | --- | --- |
| A-01 | P0 | §6、§8.1 | `editor_state` | `project_title == PixelLinePlatformer`；`project_root` 以 `mcp-demo-platformer` 结尾；`commands` 含 `collection_get_hierarchy` 与 `gameobject_create` |
| A-02 | P0 | §6、§3.4 | `project_doctor` | `ready.editor`、`ready.user_path`、`ready.game_project` 为真。`mcp.transport == stdio`，`mcp.url` 为空。`engine` 来自用户 zip。`java_required is false` |
| ENV-04 | P0 | §3.4 | 同上 | `java_required is false`。`java` 真假都不判失败 |
| A-03 | — | §7.5 | `session_manage op=list` 再 `session_activate` 该 editor id | list 有 `kind=editor` 且 `alive`；activate `status=ok` |
| A-04 | — | §8.3 | `api_manage op=get q=go.set_position` | 结果提到 `set_position` |
| A-05 | — | §8.2 | `editor_manage op=state` 与 `selection_get` | `status=ok`，state 与 `editor_state` 同类字段可读 |
| ENV-09 | P0 | §4.1 | `editor_manage op=mcp_config` | 文本含 `command` 与 `args`；不含 `http://` / `https://` |
| A-06 | — | §8.4 | `editor_manage op=mcp_config format=codex` | 文本含 `tool_timeout_sec` |
| A-07 | — | §8.2 | `collection_open` `/game/game.collection` | `status=ok` |
| A-08 | — | §3.3 | `gameobject_get_properties` collection=`/game/game.collection` id=`level` | `source` 为 `editor` 或 `disk`，不是 `runtime` |
| A-09 | P0 | §3.3、§8.2 | `collection_get_hierarchy` `/game/game.collection` | ids 含 `player`、`level`、`bee1`、`slime`、`instructions`；id 数 ≥ 8；`source` 为 `editor` 或 `disk` |
| A-10 | P0 | §8.3 | `tilemap_manage get` `/game/level.tilemap`，再 `get_tile` layer=`layer1` x=15 y=6 | get 成功；该格 tile == 16 |
| A-11 | P0 | §8.3 | `input_binding_manage get` `/input/game.input_binding` | 绑定含 `left`、`right`、`jump`、`fire` |
| A-12 | — | §8.2 | `filesystem_manage list` `/game` | 含 `player.script`、`bee.script`、`slime.script`、`level.tilemap`、`dust.particlefx` |
| A-13 | — | §8.3 | `tilesource_manage get` `/assets/game.tilesource` | `status=ok`；回包或磁盘能看出 tilesource |
| A-14 | — | §8.3 | `particlefx_manage get` `/game/dust.particlefx` | `status=ok` |
| A-15 | — | §8.2 | `gameobject_create` id=`mcp_marker` 到 `/game/game.collection` position=`[12,20,0]` | hierarchy 与 `game.collection` 文本都含 `mcp_marker` |
| A-16 | — | §8.2 | `collection_save` `/game/game.collection` | 磁盘仍含 `mcp_marker` |
| A-17 | — | §8.3 | `gameobject_manage find` id=`mcp_marker`；`set_property` position=`[14,22,0]` 再 get | find 非空；position ≈ `[14,22,0]` |
| A-18 | — | §8.2 | `component_add` type=label 到 `mcp_marker` | properties 能看出 label |
| A-19 | — | §8.2 | `script_create` `/mcp/note.script`；`script_patch` 在 `init` 行加 `-- mcp`；`script_manage op=read` | 文件存在；读回含 `-- mcp` |
| A-20 | — | §8.2 | `script_attach` `mcp_marker` ← `/mcp/note.script` | properties / components 含该 script |
| A-21 | — | §8.3 | filesystem：`write_text` `/mcp/hello.txt` = `hello`，再 read / exists / list `/mcp` / search `hello` / copy → `/mcp/hello2.txt` | 读回 `hello`；list 含 `hello.txt`；search 命中；`hello2.txt` 相同 |
| A-22 | — | §8.4 | `batch_execute`：先写 `/mcp/batch.txt`，再 patch 不存在的 script | `status=error`（**不是** Nested batch_execute）；`error.data.rolled_back is true`；`batch.txt` 不存在 |
| A-23 | — | §8.3 | `collection_manage create` `/mcp/empty.collection`；`get_roots` 主 collection | 空 collection 文件非空；roots 含 `mcp_marker` |
| A-24 | — | §8.2 | `project_manage settings_get` `project.title`；`settings_set` `project.version=9.9` 再 get | title 是 `PixelLinePlatformer`；version 读回是 `9.9` 或等价 |
| A-25 | — | §8.3 | 对每个领域 tool `create` `/mcp/<name>.<ext>` 再 `get` + `list` | 每个文件非空；get `status=ok`；list 含该文件名 |
| A-26 | — | §8.3 | `camera_manage add` collection+id=`mcp_marker` | 再 get `mcp_marker` properties 含 camera |
| A-27 | — | §8.3 | `gui_manage create` `/mcp/hud.gui` + `add_text` id=score + `set_node` text=99 + `get_node` | text==99 或磁盘含 `99` |
| A-28 | — | §8.3 | `tilemap_manage create` `/mcp/room.tilemap` + `add_layer` ground + `set_tile` (2,3)=7 + `get_tile`；`input_binding_manage create` `/mcp/extra.input_binding` + `add_key` KEY_SPACE/jump | tile==7；文件含 `jump` |
| A-29 | — | §8.4 | stdio `tools/list` | 每个 tool 有关闭 schema（`additionalProperties: false`）；工具数 20–99 |
| A-30 | — | §8.2 | prompts：`defold-observe` / `defold-live` / `defold-check` | 能列出；文案要求 observe 后 query，不截屏 |

A-25 领域文件（均在 `/mcp/`）：`sprites.atlas`、`tiles.tilesource`、`burst.particlefx`、`custom.material`、`ui.font`、`beep.sound`、`default.gamepads`、`display.display_profiles`、`box.model`、`enemy.factory`、`room.collectionfactory`、`level.collectionproxy`、`body.collisionobject`、`sky.cubemap`、`mesh.mesh`、`textures.texture_profiles`、`work.compute`、`app.appmanifest`、`custom.render`。`tilemap` / `gui` / `input_binding` 由 A-27、A-28 覆盖。

### 5.3 编译与诊断（C）— 仍无 live

| ID | P | 需求 | 步骤 | 断言 |
| --- | --- | --- | --- | --- |
| C-01 | P0 | §7.2 | `project_check` | `status=ok`；`success` 为真；`launched` 为假 |
| C-02 | — | §7.2 | `project_build` | `launched` 为假 |
| C-03 | — | §6 | `logs_read source=all` | 回包含 `lines` 或 `issues` 列表（空列表也算） |
| C-04 | — | §7.3 | `diagnostics_read` | 有 `issues` 列表；**不**重新编译；不写 `last_check.json`（mtime 不变） |
| C-05 | — | §7.3 | `editor_preview` `/game/game.collection` 写 PNG | 文件存在；头 8 字节是 PNG 魔数；`source=editor-preview` |
| C-06 | — | §3、§10 | 把 `/mcp/note.script` 改成非法 Lua，再 `project_check`，然后恢复 | 坏脚本时 `status=error`，`issues` 含 `/mcp/note.script` 且有 `line` 或 `range.start`；恢复后再 check `status=ok` |

### 5.4 一次 live（R）

前置：A/C 已过，此时才 `project_run mode=live`。主环：先 observe，再 query 文件。

| ID | P | 需求 | 步骤 | 断言 |
| --- | --- | --- | --- | --- |
| R-01 | P0 | §9 | `project_run mode=live` | `status=ok`。`engine.json` 的 command[0] 来自用户 zip unpack / agent-engine |
| R-02 | — | §4.3、§15.1 | 读 `engine.json` | 含 `--agent-control=`；不含 `/eval` |
| ENV-10 | P0 | §4.3 | 同上 | 同 R-02 |
| R-03 | — | §9 | 再 `project_run mode=live`（不先 stop） | `NOT_ALLOWED` 或实现先停再拉且回包说明只有一个 live。禁止静默两个 dmengine。随后 `alive` 仍为 true |
| R-04 | — | §8.1、§7.3 | `editor_state` + `runtime_state` | editor `readiness` 为 `running` 或 `engine.alive` 为 true；runtime 能看出 CLI live `alive=true` |
| R-05 | P0 | §7.4、§3.2 | `runtime_observe`（默认） | `data.snapshot.id` 非空。`data` 与 `data.snapshot` **都没有** `scene_graph`。节点数 ≥ 12。默认**没有** `screenshot` |
| R-06 | — | §7.8 | 再 observe 一次 | 新 `snapshot.id` 与上一份不同 |
| R-07 | P0 | §3、§7.8 | `runtime_snapshot_query op=get_node` id=`player/player`（或 `player`） | 节点 type 是 `goc`（或等价 GO）。有 `world_position` 且能解析成 vec3。`source=runtime` |
| R-08 | — | §7.8 | `find` / `list_ids` | 树上有 `level` 与至少一个 `bee`/`slime` |
| R-09 | — | §7.8 | `list_ids`；`get_subtree` id=玩家 `depth` 默认 8；`get_path` `/scene_graph/id` | list 含玩家；subtree 回包有 `depth`；path 的 `value` 非空 |
| R-10 | — | §3.3 | `runtime_get_hierarchy` + `runtime_get_properties` 玩家 | `source=runtime` |
| R-11 | — | §7.7 | `runtime_diff`；`compare_authoring` id=玩家 collection=`/game/game.collection` 或 `/game/player.collection` | diff `source=runtime`；compare 有玩家项且 authoring/runtime 位置能解析 |
| R-12 | P0 | §7.9 | 记玩家 x，`runtime_input key=left hold=24`，再 observe | 玩家世界 x 至少减少 0.5 |
| R-13 | — | §7.7 | 输入后再 `compare_authoring` | runtime x 小于 authoring x 至少 0.5，或有 position 类 delta 且 runtime x 更小 |
| R-14 | — | §11 R3 | 再 observe + get_node，立刻 `game_eval confirm=true`：`return go.get_position('/player/player')` | 结果能解析成 vector3；x 与这一次刚观察的玩家同量级（容差 8） |
| R-15 | — | §11 R3 | `runtime_debug`：`status` / `pause` / `stack` / `set_breakpoint` file=`/game/player.script` line=8 / `continue` | 各 `status=ok`。`prompt` 不为 true |
| R-16 | — | §7.3 | `runtime_screenshot` | 可选：成功则 PNG 魔数对；失败标 SKIP |
| R-17 | — | §7.8 | `resources/list` + `resources/read` `defold://runtime/snapshot/{id}` | 读回是摘要，**没有** `scene_graph` |
| R-18 | — | §10 | `runtime_snapshot_query` 假 snapshot id | `SNAPSHOT_NOT_FOUND` |

### 5.5 连打与内存（M）— 仍是同一次 live，不 stop

单次 `status=ok` 不算过。必须连打，并读回上限。快照保留上限与产品 `RETAIN` 一致，为 **8**。默认回包硬上限 **48 KB**（需求 §7）。

| ID | P | 需求 | 步骤 | 断言 |
| --- | --- | --- | --- | --- |
| M-01 | P0 | §3.2、§7.8 | 连续 `runtime_observe` **12** 次（超过保留上限） | 每次 `status=ok`，`data`/`data.snapshot` **都没有** `scene_graph`，整份信封 JSON 小于 48 KB，12 个 snapshot id 互不相同。live **同一 pid**。目录里匹配 `\d{8}T\d{6}Z-*.json` 的文件数 ≤ 8。第 1 次的 id 再 `get_node` 为 `SNAPSHOT_NOT_FOUND`；第 12 次的 id 仍能 `get_node` 到玩家 `world_position` |
| M-02 | — | §7.8 | 连续 20 轮：`get_node` 玩家 + `find` + `list_ids` | 每轮 `status=ok`，回包没有 `scene_graph`，每份信封小于 48 KB。最后一轮仍有玩家 `world_position` |
| M-03 | — | §3.3、§8.2 | 连续 8 轮：`collection_get_hierarchy` + `tilemap_manage get_tile` (15,6) + `editor_state` | 每轮 hierarchy 含 `player`/`level`；tile 仍 == 16；title 仍是 `PixelLinePlatformer`。后一轮信封字节数 ≤ 前一轮的 2 倍 + 8 KB |
| M-04 | — | §7.9 | 4 个循环：`runtime_input key=right hold=4` → observe → `key=left hold=4` → observe | 每次都能 `get_node` 玩家；世界 x 有限（`abs(x) < 10000`）。live 仍是 M-01 那个 pid |
| M-05 | — | §3.2 | 读 live dmengine 的 WorkingSet：M 段开始 vs M-04 之后 | 结束值 ≤ `max(开始*3, 开始+256MB)`。读不到 WorkingSet = 失败 |
| M-06 | — | §7.8 | `runtime_snapshot_query op=list`；`resources/list` 8 次 | list 的 snapshot 条数 ≤ 8。每次 resources/list 的 JSON **没有** `scene_graph` |
| M-07 | — | §10 | 连续 12 次 `atlas_manage op=get`（无 path） | 全部 `MISSING_PARAM`；live 仍 alive |

### 5.6 同一 live 随机长跑（T）— 仍不 stop、不重开引擎

T 段接在 M 之后、N 之前。禁止 `project_stop` / 再 `project_run` / `editor_manage quit` / 改官方 `player.script` / `filesystem_manage read_text` 快照 JSON。关编辑器只在 X。一步失败**记录后继续跑完**本场（T/N/X），报告写清 `soak_seed`、全部失败步的步号、tool、回包。整场结束后再一次性修产品，不放宽本表断言。

随机池只打读 + `/mcp/` 或 `mcp_marker` 安全写 + 有限干预 + 已有稳定 `error.code` 负例。权重偏读。`project_stop` / `project_run` / `quit` 只允许出现在 R/X 已有步骤；T 池用读和安全写代替这三者及其破坏性 op。

墙钟由 `DEFOLD_ACCEPTANCE_SOAK_SEC` 控制，默认 **1800**，上限 **3600**。

| ID | P | 需求 | 步骤 | 断言 |
| --- | --- | --- | --- | --- |
| T-01 | P0 | §3.2 | `random.Random(soak_seed)` 在同一 live 上随机连打 | 墙钟 ≥ 30 分钟且 ≤ 60 分钟。`soak_seed` 写入报告。随机步数 ≥ 80。`scripts/agent/agent_mcp.py` 的每个 tool 名至少被抽到 1 次（`project_stop` / `project_run` / `quit` 除外，它们只在 R/X） |
| T-02 | — | §3.2、§7.8 | 每 20 步做不变量 | 同一 live pid；id 快照文件 ≤ 8；observe / snapshot_query 信封无 `scene_graph` 且 < 48 KB；`/game/level.tilemap` `(15,6)==16`；`player/player` 世界 x 有限（`abs < 10000`）；title 仍是 `PixelLinePlatformer` |
| T-03 | — | §3.2 | 读 live dmengine WorkingSet：T 开始 vs T 结束 | 结束值 ≤ `max(T开始*3, T开始+256MB)`。读不到 WorkingSet = 失败 |
| T-04 | — | §3.2、§3.3 | T 结束后立刻 `get_node` 玩家 + `get_tile` (15,6) + `live_status` | 有玩家 `world_position`；tile == 16；live 仍 alive。然后才进 N |

### 5.7 合并负例（N）— live 仍开着，不 stop

| ID | P | 需求 | 步骤 | 断言 |
| --- | --- | --- | --- | --- |
| N-01 | — | §10 | 每个领域 `*_manage`：无 `op`；`op=get` 无 `path`（camera 无 collection+id） | 全部 `MISSING_PARAM` |
| N-02 | — | §7.8、§10 | filesystem：`read_text` 最新 snapshot json；`op=explode`；path=`..`；path=`game`（无前导 `/`） | 分别为 `NOT_ALLOWED`、`UNKNOWN_OP`、`INVALID_PARAM`、`INVALID_PARAM` |
| N-03 | — | §10 | `gameobject_create` 无 collection；`collection_get_hierarchy` 无 path；`script_patch` 缺 `old_text` 或 `new_text` | 皆 `MISSING_PARAM` |
| N-04 | — | §11 R3 | `game_eval` 无 confirm；`os.execute` 且 confirm；无 code；code 超 4096 字节 | `NOT_ALLOWED` / `NOT_ALLOWED` / `MISSING_PARAM` / `INVALID_PARAM` |
| N-05 | — | §10 | `runtime_input` 无 key；假 key；`hold=31` | `MISSING_PARAM` / `INVALID_PARAM` / `INVALID_PARAM` |
| N-06 | — | §10 | `runtime_debug` 假 op；`set_breakpoint` 无 file；无 line | `UNKNOWN_OP` / `MISSING_PARAM` / `MISSING_PARAM` |
| N-07 | — | §10 | `logs_read source=explode`；`project_run mode=explode` | 皆 `INVALID_PARAM`。**不得**因此停掉当前 live |
| N-08 | — | §7.5 | `session_activate` 假 id | `UNKNOWN_TARGET` |
| N-09 | — | §10 | `editor_state` `tool_timeout_sec=explode` | `INVALID_PARAM` |

### 5.8 表尾关停（X）— 整表唯一允许的引擎/编辑器重开

| ID | P | 需求 | 步骤 | 断言 |
| --- | --- | --- | --- | --- |
| X-01 | P0 | §9 | `project_stop` | `live_status.alive` 不是 true |
| X-02 | — | §9、§10 | 无 live 时 `runtime_observe mode=live`；再 `get_node` 玩家（旧快照） | observe 为 `ENGINE_NOT_RUNNING`；query 仍 `status=ok` |
| X-03 | — | §7.2、§9 | `project_run mode=batch` `frames=5` | `status=ok`；随后 CLI live `alive` 不是 true |
| X-04 | — | §3.4 | 杀掉打开该样例的 `Defold.exe` / 相关 `java.exe` | `read_editor_endpoint` 为空 |
| X-05 | — | §8.3、§3.4 | 关编辑器时 `api_manage op=get q=go.set_position` | 结果提到 `set_position` |
| X-06 | — | §3.4、§15.3 | 关编辑器后 `project_run mode=live` 再 observe | observe 有 snapshot id |
| X-07 | — | §3.4 | 关编辑器时领域 create `/mcp/closed.atlas` | `source=disk` 且 `undoable:false` |
| X-08 | — | §3 | 恢复官方 `game/game.collection` | 磁盘文本不再含 `mcp_marker` |

X-06 是整表唯一的第二次 live。X 段结束后 `project_stop`，不把第二次 live 留在机器上。

---

## 6. 工具覆盖（防漏）

`scripts/agent/agent_mcp.py` 的 `TOOLS` 每一行必须出现在下表。新增 tool 先改需求，再改本表，再改执行器。

| Tool | 覆盖用例 | 最低读回 |
| --- | --- | --- |
| `editor_state` | A-01、A-05、R-04、N-09、T-01、T-02 | title / root / commands；live 后 running；长跑 title 不变 |
| `project_doctor` | A-02、ENV-04、T-01 | ready / mcp / java_required |
| `session_manage` | A-03、T-01 | 含本工程 editor |
| `session_activate` | A-03、N-08、T-01 | ok；假 id 为 UNKNOWN_TARGET |
| `api_manage` | A-04、X-05、T-01 | 文档命中；关编辑器走引擎 `/*#` |
| `editor_manage` | A-05、ENV-09、A-06、T-01 | mcp_config 无 URL。**不验 `quit`** |
| `collection_open` | A-07、T-01 | ok |
| `collection_get_hierarchy` | A-09、A-15、N-03、M-03、T-01 | source + 官方样例 ids；连打读回不变；缺 path 为 MISSING_PARAM |
| `collection_save` | A-16、T-01 | 磁盘含 mcp_marker |
| `collection_manage` | A-23、T-01 | create / get_roots |
| `gameobject_create` | A-15、N-03、T-01 | hierarchy + 磁盘；缺 collection 为 MISSING_PARAM |
| `gameobject_get_properties` | A-08、A-17、A-18、A-26、T-01 | source + position / components |
| `gameobject_manage` | A-17、T-01 | find + set_property 读回 |
| `component_add` | A-18、T-01 | 有 label |
| `component_manage` | A-26、T-01 | camera 添加后 properties 可读 |
| `script_create` | A-19、T-01 | 文件 + init |
| `script_attach` | A-20、T-01 | components |
| `script_patch` | A-19、C-06、N-03、T-01 | 文本变化；缺 old/new 为 MISSING_PARAM |
| `script_manage` | A-19、T-01 | read 文本 |
| `filesystem_manage` | A-12、A-21、N-02、T-01 | 读写搜拷；拒读快照 |
| `batch_execute` | A-22、T-01 | rolled_back |
| `project_check` | C-01、C-06、T-01 | launched=false；坏 Lua 有 file:line |
| `project_build` | C-02、T-01 | launched=false |
| `logs_read` | C-03、N-07、T-01 | lines 或 issues；假 source 为 INVALID_PARAM |
| `diagnostics_read` | C-04、T-01 | issues；不写 last_check |
| `editor_preview` | C-05、T-01 | PNG 魔数 |
| `project_manage` | A-24、T-01 | title / version |
| `project_run` | R-01、R-03、X-03、X-06、N-07 | 一个 live；batch 不留 live；假 mode。**T 不抽** |
| `project_stop` | X-01 | 进程死。**T 不抽** |
| `runtime_state` | R-04、T-01 | alive |
| `runtime_observe` | R-05、R-06、M-01、M-04、T-01、T-02、X-02、X-06 | 句柄 + 摘要；连打 / 长跑仍无整树；文件数 ≤ 8；无 live 拒绝；节点数 ≥ 12 |
| `runtime_snapshot_query` | R-07–R-09、R-11、R-18、M-01、M-02、M-06、T-01、T-02、T-04、X-02 | get_node 是 GO；连打切片小于 48 KB；假/已淘汰 id 为 SNAPSHOT_NOT_FOUND |
| `runtime_get_hierarchy` | R-10、T-01 | source=runtime |
| `runtime_get_properties` | R-10、T-01 | source=runtime |
| `runtime_diff` | R-11、T-01 | 两份真快照 |
| `runtime_screenshot` | R-16、T-01 | 可选 PNG |
| `runtime_input` | R-12、M-04、N-05、T-01 | 玩家 x 变小；左右连打 x 仍有限；缺 key / 假 key / hold 上限 |
| `game_eval` | R-14、N-04、T-01 | 无 confirm 拒绝；坐标同量级 |
| `runtime_debug` | R-15、N-06、T-01 | 不进 `debug>`。T 只用 status/stack/continue |
| `atlas_manage` … `appmanifest_manage` | A-25、N-01、T-01、X-07 | `/mcp/` 文件 + get + list；缺 path/op 为 MISSING_PARAM |
| `camera_manage` | A-26、N-01、T-01 | mcp_marker 上有 camera |
| `tilemap_manage` / `gui_manage` / `input_binding_manage` | A-10、A-11、A-27、A-28、N-01、T-01、T-02、T-04 | 官方 tile==16；绑定含 jump/fire；自造 tile/gui 读回；长跑 tile 仍 16 |
| `tilesource_manage` / `particlefx_manage` | A-13、A-14、A-25、T-01 | 官方 get + `/mcp/` create |

---

## 7. 明确不验 / 不做

| 项 | 原因 |
| --- | --- |
| `editor_manage op=quit` | 会结束正在验收的编辑器 |
| 让 Agent 桌面截窗口 | 需求 §12 |
| 引擎内 MCP、游戏里 helper、HTTP MCP URL | 需求 §12 |
| 把 `GET /scene_graph` 或 `POST /eval` 注册成 tool | 需求 §15 |
| 用像素判断「玩家在哪」 | 需求 §3、§15.6 |
| 本机装 JDK 好让 bob 过 | 用户路径；ENV-01 |
| 只跑 `test_defold_agent.py` 就签字 | 单测不是打包编辑器 |
| 每个 ID 后 `project_stop` / 重开编辑器 | 本表会话模型 |
| 把旧 L/D ID 标成 PASS | 已退役 |

---

## 8. 报告

执行器 stdout 一行一条：

```text
PASS  R-07  get_node is goc with world_position
FAIL  A-22  batch_execute rollback
SKIP  R-16  optional screenshot
```

`.cache/mcp-acceptance-report.json`：

```json
{
  "spec": "AI_MCP_ACCEPTANCE.md",
  "editor_sha1": "...",
  "zip_run": "34294924984",
  "results": [
    {"id": "R-07", "priority": "P0", "passed": true, "status": "ok", "message": ""}
  ],
  "p0_failed": [],
  "s_failed": [],
  "a_failed": [],
  "c_failed": [],
  "r_failed": [],
  "m_failed": [],
  "t_failed": [],
  "n_failed": [],
  "x_failed": [],
  "skipped": [],
  "soak_seed": 0,
  "soak_steps": 0,
  "soak_sec": 0
}
```

出现 `p1_failed`…`p60_failed` 字段 = 执行器没换干净，整次作废。

签字条件：`p0_failed` 为空，且每个 §5 ID 都出现在 `results` 里（漏跑 = 失败）。其它桶失败同样使整表失败。

---

## 9. 一次会话主环（需求 §3.1）

```text
打开官方样例（一次编辑器）
  → 读 hierarchy / tilemap / 绑定（A-09–A-11）
  → /mcp/ 写资源，主 collection 加 mcp_marker（A-15–A-28）
  → check 通过；坏 Lua 只挂 /mcp/note.script（C-01、C-06）
  → project_run live 一次（R-01）
  → observe 摘要（R-05）
  → get_node player/player = 运行时 world_position（R-07）
  → input left，x 变小（R-12）
  → 连打 observe / query / 输入，快照 ≤ 8，回包无整树（M-01–M-07）
  → 同一 live 随机 30–60 分钟（T-01–T-04）
  → 负例不重开进程（N-*）
  → project_stop（X-01）后才关编辑器
```

问「现在玩家在哪」：必须用 R-07 的运行时坐标回答，禁止用 collection 初始值冒充。
