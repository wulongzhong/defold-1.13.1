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

---

## 1. 验收对象

本轮 **P0（签字）** 只验这一条用户路径：

1. 从 Hosted desktop 最新成功 run 只下载 `Defold-x86_64-win32`。
2. 解压到仓库 `.cache/`，用自带 JRE 的 `Defold.exe` 打开隔离测试工程。
3. 用本机已有 Python 跑 `scripts/agent/defold_agent.py`（stdio MCP 同一套 dispatch）。
4. 编辑器开着。check 走 `POST /command/check`。live 引擎来自这份 zip 的 unpack / 包内 jar，带 `--agent-control`。

**P1**–**P26** 已在打包编辑器上按 ID 实跑。**P27** 是需求里已写、合同还没编号的 exists / get_roots / add_instance 缺参。关编辑器的磁盘 / live 路径（L3-05）是其中一条，不是整表的前提。

不是验收对象：`bob-jar-*` artifact、单独的 `dmengine-x86_64-win32` artifact、系统 JDK、本仓库当游戏工程、`test_defold_agent.py` 单测（单测是开发回归，不能代替本表）。

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
| ENV-06 | 测试工程是 `.cache/mcp-acceptance-game/`，不是引擎仓库 | 隔离 |
| ENV-07 | `Defold.exe --preferences <test>/prefs.json <test>/game.project` | 不改用户 Defold 配置 |
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

一层通过：该层全部 **P0** 用例通过。  
P0 签字：§5 全部 P0 通过，且 §2 硬约束未破。  
P1–P27 必须实跑；漏跑 = 失败。禁止把没跑的条目标成通过。仅 L5-07（可选截屏）允许 `SKIP`。

---

## 4. 准备（每次复测）

按顺序做，做完才能进 §5。

| 步骤 | 动作 | 通过 |
| --- | --- | --- |
| S-01 | `gh run list --workflow "Hosted desktop" --branch main`，取最新 success | 有 `Defold-x86_64-win32` |
| S-02 | 停掉旧 `Defold.exe` / 测试工程相关 `java.exe` / `dmengine.exe` | 旧 zip 能删 |
| S-03 | 删 `.cache/Defold-x86_64-win32`，只下载新 zip 并解压 | `Defold.exe` 存在；`config` 的 `editor_sha1` 对应当前 `main` |
| S-04 | jar 内 `libexec/x86_64-win32/dmengine.exe` 含 `agent-control`、`runtime-dump`、`quit-after-frames` | 注入成功 |
| S-05 | 测试工程保留：`game.project`、`prefs.json`、`main/main.collection`、`main/player.script`、`input/game.input_binding`。其它 `main/*` 领域文件可清 | 工程能打开 |
| S-06 | 启动编辑器，等到**新的** `.internal/editor.port` + `editor.token` | `editor_state` 的 `project_title` 是 `MCP Acceptance` |
| S-07 | 环境：设 `DEFOLD_EDITOR` 为这份 `Defold.exe`；**取消** `DEFOLD_ENGINE`、`DEFOLD_BOB` | doctor 的 engine 路径含 unpack 或 `agent-engine` 或本 zip，不含 `hosted-desktop` / `bob-jar` / `dynamo_home` |

失败闭环（产品问题，不是用例写错）：

```text
修源码 → commit + push main → 等 Hosted desktop 的 Windows zip
→ 停进程 → 删旧解压 → 只下新 zip → 从 S-03 再来 → §5 整表重跑
```

禁止：修完只复测失败的那一条。

---

## 5. 用例

优先级：**P0** = 签字必须过。**P1**–**P26** = 已在打包编辑器上跑过。**P27** = 本轮按需求补的编号；失败与 P0 一样要修，不得从合同删掉。

断言里的「约等于」：坐标误差 ≤ 1.5（作者态）或移动判定为 x 至少减少 0.5（输入后）。

### 5.0 环境与医生

| ID | P | 需求 | 步骤 | 断言 |
| --- | --- | --- | --- | --- |
| ENV-04 | P0 | §3.4 | `project_doctor` | `java_required is false`。`java` 真假都不判失败 |
| ENV-09 | P0 | §4.1 | `editor_manage op=mcp_config` | 文本含 `command` 与 `args`；不含 `http://` / `https://` |
| ENV-10 | P0 | §4.3 | 读 live `engine.json` | argv 含 `--agent-control`；不含 `/eval` |
| L0-01 | P0 | §6、§8.1 | `editor_state` | `project_title == MCP Acceptance`；`project_root` 以 `mcp-acceptance-game` 结尾；`commands` 含 `gameobject_create`；回包可带 `engine` / `ready` |
| L0-02 | P0 | §6 | `project_doctor` | `ready.editor`、`ready.user_path`、`ready.game_project` 为真。`mcp.transport == stdio`，`mcp.url` 为空。`engine` 来自用户 zip（ENV-07） |
| L0-03 | P0 | §7.5 | `session_manage op=list` | 有 `kind=editor` 且 `alive`。`session_activate` 该 id 后 `status=ok` |
| L0-04 | P0 | §8.3 | `api_manage op=get q=go.set_position` | 结果提到 `set_position` |
| L0-05 | P0 | §8.2 | `editor_manage op=state` 与 `selection_get` | `status=ok`，state 与 `editor_state` 同类字段可读 |
| L0-06 | P1 | §8.4 | `tools/list`（stdio MCP） | 每个 tool 有关闭 schema（`additionalProperties: false`）；工具数 20–99 |
| L0-07 | P1 | §7.8 | `resources/list` + `resources/read` `defold://runtime/snapshot/{id}` | 读回是摘要，**没有** `scene_graph` |
| L0-08 | P1 | §8.2 | prompts：`defold-observe` / `defold-live` / `defold-check` | 能列出；文案要求 observe 后 query，不截屏 |
| L0-09 | P2 | §8.1、R1 | live 之后再 `editor_state` | `data.engine.alive`（或 `engine` 里等价字段）为 true；开着编辑器也要认 CLI live |
| L0-10 | P2 | R2 | `resources/list` + `resources/read` `defold://editor/state` 与 `defold://project/mcp-config` | list 还含 `defold://collection/hierarchy`。state 摘要有 title；mcp-config 文本无 `http://` / `https://` |
| L0-11 | P3 | §9、L0 | 置 `building.lock` 后 `script_create` | `status=error`，`EDITOR_NOT_READY`，`sub_code=building`。`editor_state` 仍 ok。测完删锁 |
| L0-12 | P3 | §9、L0 | 只写 `dump.request`（无 `dump.ready`）后 `script_create` | `status=error`，`EDITOR_NOT_READY`，`sub_code=observing`。测完删 request |
| L0-13 | P6 | §9 | live 之后再 `editor_state` | 信封 `readiness=running` |
| L0-14 | P7 | §9 | `project_stop` 之后再 `editor_state` | 信封 `readiness` 不是 `running` |
| L0-15 | P8 | §9 | `project_stop` 之后再 `runtime_state` | 信封 `readiness=no_runtime` |
| L0-16 | P17 | §8.4、§10 | `editor_state` `tool_timeout_sec=explode` | `INVALID_PARAM` |
| L0-17 | P17 | §8.4 | `editor_manage op=mcp_config format=codex` | 文本含 `tool_timeout_sec` |

### 5.1 L1 作者态

前置：S-06。先把 collection 写成只有 `name: "main"`，再 `collection_open`。

| ID | P | 需求 | 步骤 | 断言 |
| --- | --- | --- | --- | --- |
| L1-01 | P0 | §6 | `collection_open` + `collection_get_hierarchy` | `source` 为 `editor`（或磁盘回退时 `disk`）。空树不含 `cube` |
| L1-02 | P0 | §8.2 | `gameobject_create` cube `[100,40,0]` | hierarchy 含 `cube`。`gameobject_get_properties` 的 position ≈ `[100,40,0]` |
| L1-03 | P0 | §8.3 | `gameobject_manage set_property` position `[120,40,0]` | 再 get 仍是 `[120,40,0]` |
| L1-04 | P0 | §8.3 | `gameobject_manage find` id=cube | `matches` 非空 |
| L1-05 | P0 | §8.2 | `script_create /main/cube.script` | 磁盘文件存在且含 `function init(self)` |
| L1-06 | P0 | §8.2 | `script_attach` cube ← cube.script | properties / components 含该 script |
| L1-07 | P0 | §8.2 | `gameobject_create` player `[0,0,0]` + `script_attach` player.script | hierarchy 含 `player` |
| L1-08 | P0 | §8.2 | `script_patch` 在 `init` 行加上 `-- mcp`；`script_manage op=read` | 读回文本含 `-- mcp` |
| L1-09 | P0 | §8.2 | `component_add` type=label | properties 能看出 label |
| L1-10 | P0 | §8.3 | 建 `tempgo`，`collection_manage remove_instance` | hierarchy 不再含 `tempgo` |
| L1-11 | P0 | §8.3 | `collection_manage create /main/empty.collection` | 文件存在且非空 |
| L1-12 | P0 | §8.3 | `collection_manage get_roots` | id 集合含 `cube` 与 `player` |
| L1-13 | P0 | §8.2 | `collection_save` | 磁盘 collection 文本含 `cube` 与 `player` |
| L1-14 | P0 | §8.3 | filesystem：`write_text` / `read_text` / `exists` / `list` / `search query=hello` / `copy` | 读回 `hello`；list 含 `note.txt`；search 命中；`note2.txt` 内容相同 |
| L1-15 | P0 | §8.4 | `batch_execute`：先写 `/main/batch.txt`，再 patch 不存在的 script | `status=error`（**不是** Nested batch_execute）；`error.data.rolled_back is true`；`batch.txt` 不存在 |
| L1-16 | P0 | §3.3 | 作者态 get 的 `source` | `editor` 或 `disk`，不是 `runtime` |
| L1-17 | P1 | §8.3 | `gameobject_manage rename` 再改回 | hierarchy 跟新 id，再改回 `cube` |
| L1-18 | P1 | §8.3 | `gameobject_manage delete` 临时 GO | 删除后 hierarchy 无该 id |
| L1-19 | P1 | §8.3 | `component_manage set_property` / `remove` | 属性变化；remove 后组件消失 |
| L1-20 | P1 | §8.3 | `script_manage detach` 临时 script | 组件列表不再含该 script |
| L1-21 | P1 | §8.3 | filesystem `mkdir` / `move` / `delete` | 目录存在；move 后旧无新有；delete 后不存在 |
| L1-22 | P0 | §7.8 | `filesystem_manage read_text` 指向最新 snapshot json | `status=error`，`NOT_ALLOWED` |
| L1-23 | P1 | §8.3 | `gameobject_create` 带 `parent` | 子 GO 在 hierarchy 的 parent 下 |
| L1-24 | P2 | §8.4 | `filesystem_manage search` `query=hello` `offset=0` `limit=1` | 回包有 `offset`/`limit`；`truncated` 是 bool |
| L1-25 | P2 | §8.4 | `collection_get_hierarchy` `offset=0` `limit=1` | 回包有 `offset`/`limit`；`truncated` 是 bool |
| L1-26 | P2 | §7.8 | `filesystem_manage delete` 指向 snapshot json | `status=error`，`NOT_ALLOWED` |
| L1-27 | P2 | §8.3 | `collection_manage add_instance` 临时 GO | hierarchy 含该 id；测完删掉 |
| L1-28 | P2 | §8.4 | `batch_execute`：filesystem 写 + `project_check` | `status=ok`；`data.atomic is false` |
| L1-29 | P6 | §10 | `gameobject_manage` 假 op | `UNKNOWN_OP` |
| L1-30 | P6 | §10 | `gameobject_get_properties` 不传 id | `MISSING_PARAM` |
| L1-31 | P10 | §10 | `filesystem_manage` 假 op | `UNKNOWN_OP` |
| L1-32 | P13 | §8.4 | `filesystem_manage exists` `path=/main/../game.project` | `INVALID_PARAM` |
| L1-34 | P15 | §8.3、§10 | `filesystem_manage search` 不传 query | `MISSING_PARAM` |
| L1-33 | P16 | §8.4 | `filesystem_manage exists` `path=main/player.script`（无前导 `/`） | `INVALID_PARAM` |
| L1-35 | P18 | §8.3、§10 | `filesystem_manage mkdir` 不传 path | `MISSING_PARAM` |
| L1-36 | P18 | §8.3、§10 | `filesystem_manage copy` 有 path 不传 dest | `MISSING_PARAM` |
| L1-37 | P18 | §8.3、§10 | `filesystem_manage delete` 不传 path | `MISSING_PARAM` |
| L1-38 | P19 | §8.3、§10 | `gameobject_manage set_property` 不传 property | `MISSING_PARAM` |
| L1-39 | P19 | §8.3、§10 | `component_manage set_property` 不传 component | `MISSING_PARAM` |
| L1-40 | P20 | §8.3、§10 | `collection_open` 不传 path | `MISSING_PARAM` |
| L1-41 | P20 | §8.2、§10 | `script_create` 不传 path | `MISSING_PARAM` |
| L1-42 | P20 | §8.2、§10 | `script_manage read` 不传 path | `MISSING_PARAM` |
| L1-43 | P21 | §8.3、§10 | `collection_save` 不传 path | `MISSING_PARAM` |
| L1-44 | P21 | §8.4、§10 | `batch_execute` 不传 commands | `MISSING_PARAM` |
| L1-45 | P22 | §8.3、§10 | `gameobject_manage rename` 不传 name | `MISSING_PARAM` |
| L1-46 | P22 | §8.3、§10 | `collection_manage remove_instance` 不传 id | `MISSING_PARAM` |
| L1-47 | P23 | §8.2、§10 | `gameobject_create` 不传 collection | `MISSING_PARAM` |
| L1-48 | P23 | §8.2、§10 | `script_attach` 有 collection 不传 id | `MISSING_PARAM` |
| L1-49 | P23 | §8.2、§10 | `script_patch` 不传 path | `MISSING_PARAM` |
| L1-50 | P24 | §8.1、§10 | `collection_get_hierarchy` 不传 path | `MISSING_PARAM` |
| L1-51 | P24 | §8.2、§10 | `component_add` 有 collection 不传 id | `MISSING_PARAM` |
| L1-52 | P24 | §8.3、§10 | `collection_manage create` 不传 path | `MISSING_PARAM` |
| L1-53 | P25 | §8.3、§10 | `gameobject_manage set_property` 不传 id | `MISSING_PARAM` |
| L1-54 | P25 | §8.3、§10 | `component_manage set_property` 不传 property | `MISSING_PARAM` |
| L1-55 | P25 | §8.3、§10 | `filesystem_manage write_text` 有 path 不传 text | `MISSING_PARAM` |
| L1-56 | P26 | §8.3、§10 | `filesystem_manage write_text` 不传 path | `MISSING_PARAM` |
| L1-57 | P26 | §8.3、§10 | `filesystem_manage move` 有 path 不传 dest | `MISSING_PARAM` |
| L1-58 | P26 | §8.3、§10 | `filesystem_manage read_text` 不传 path | `MISSING_PARAM` |
| L1-59 | P27 | §8.3、§10 | `filesystem_manage exists` 不传 path | `MISSING_PARAM` |
| L1-60 | P27 | §8.3、§10 | `collection_manage get_roots` 不传 path | `MISSING_PARAM` |
| L1-61 | P27 | §8.3、§10 | `collection_manage add_instance` 不传 collection | `MISSING_PARAM` |

### 5.2 L2 编译诊断

| ID | P | 需求 | 步骤 | 断言 |
| --- | --- | --- | --- | --- |
| L2-01 | P0 | §8.2 | `project_check` | `source=editor`，`launched=false`，`success` 隐含于 `status=ok` |
| L2-02 | P0 | §8.2 | `project_build` | 同上，`launched=false`（这是 check 别名，不是 Play） |
| L2-03 | P0 | §6 | `logs_read source=all` | 回包含 `lines` 或 `issues` 列表（空列表也算） |
| L2-04 | P0 | §6 | `diagnostics_read` | 有 `issues` 列表；**不**重新编译 |
| L2-05 | P0 | §3、§10 | 把 cube.script 改成非法 Lua，再 `project_check` | `status=error`。`issues` 里有 `/main/cube.script`，且有 `line` 或 `range.start` |
| L2-06 | P0 | §8.2 | 恢复脚本再 check | `status=ok` |
| L2-07 | P0 | §7.3 | `editor_preview` collection，写 PNG | 文件存在；头 8 字节是 PNG 魔数；`source=editor-preview` |
| L2-19 | P21 | §7.3、§10 | `editor_preview` 不传 path | `MISSING_PARAM` |
| L2-08 | P1 | §8.2 | `project_manage op=hot_reload` | 编辑器开着时 `source=editor` |
| L2-09 | P2 | §8.4 | `logs_read` `limit=2` `offset=0` | 回包有 `offset`/`limit`；`truncated` 是 bool |
| L2-10 | P3 | §7.3 | `logs_read` `source=all` `severity=error` | 回包有 `issues` 与 `prints` 列表（可空）；`severity` 为 `error` |
| L2-11 | P3 | §7.3 | `logs_read` `source=editor-file` | `status=ok`；`source` 为 `editor-file`（或 `sources` 含它）；`lines` 是列表（可空） |
| L2-12 | P4 | §7.3 | `logs_read` `source=all` `domain=ENGINE` | `status=ok`；`domain` 为 `ENGINE`；`lines` 是列表（可空） |
| L2-13 | P5 | §7.3 | `logs_read` `source=all` `q=ENGINE` | `status=ok`；`q` 为 `ENGINE`；`lines` 是列表（可空） |
| L2-14 | P7 | §7.4 | 默认 `runtime_observe` 回包 | `logs.lines` 长度 ≤ 40 |
| L2-15 | P11 | §7.3 | `logs_read` 不传 source | `status=ok`；`lines` 是列表；`sources` 是列表 |
| L2-16 | P12 | §7.3、§10 | `logs_read source=explode` | `INVALID_PARAM` |
| L2-17 | P13 | §7.3 | `diagnostics_read` | `status=ok`；`source=diagnostics`；不写 `last_check.json`（mtime 不变） |
| L2-18 | P14 | §7.3 | `logs_read source=engine` | `status=ok`；`source` 为 `engine-log`，或 `sources` 含它；`lines` 是列表 |

### 5.3 L3 生命周期

| ID | P | 需求 | 步骤 | 断言 |
| --- | --- | --- | --- | --- |
| L3-01 | P0 | §9 | `project_run mode=live` | `status=ok`。随后 `engine.json` 的 command[0] 来自用户 zip unpack / agent-engine |
| L3-02 | P0 | §4.3、§15.1 | 读 `engine.json` | 含 `--agent-control=`；不含 `/eval` |
| L3-03 | P0 | §9 | 再 `project_run mode=live`（不先 stop） | `NOT_ALLOWED` 或实现先停再拉、回包说明只有一个 live |
| L3-04 | P0 | §9 | `project_stop` | `live_status.alive` 不是 true |
| L3-05 | P1 | §3.4、§15.3 | 关编辑器后 `project_run mode=live` | 仍能 observe；关编辑器时的磁盘写 `source=disk` 且 `undoable:false` |
| L3-06 | P3 | §8.3、§3.4 | 关编辑器时 `api_manage op=get q=go.set_position` | 结果提到 `set_position`（引擎 `/*#` / `engine-docs`） |
| L3-07 | P4 | §7.2、§9 | `project_stop` 之后 `project_run mode=batch` `frames=5` | `status=ok`；随后 CLI live `alive` 不是 true |
| L3-08 | P5 | §10 | `project_run` 指向不存在的 `engine` | `ENGINE_UNREACHABLE` |
| L3-10 | P11 | §7.3、§10 | `project_run mode=explode` | `INVALID_PARAM` |

L3-03 若实现是「先停再拉」且回包诚实，算通过；禁止静默两个 dmengine。

### 5.4 L4 运行时观察

前置：L3-01 已 live。主环：**先 observe，再 query 文件。** 不要 `snapshot=live` 当默认。

| ID | P | 需求 | 步骤 | 断言 |
| --- | --- | --- | --- | --- |
| L4-01 | P0 | §7.4、§3.2 | `runtime_observe`（默认） | `data.snapshot.id` 非空。`data` 与 `data.snapshot` **都没有** `scene_graph`。整包 JSON &lt; 48 KB。`summary.roots` 含 player。默认**没有** `screenshot` |
| L4-02 | P0 | §7.8 | 再 observe 一次 | 新 `snapshot.id` 与上一份不同 |
| L4-03 | P0 | §7.3 | `runtime_state` | 能看出 CLI live `alive=true` |
| L4-04 | P0 | §3.3 | `runtime_get_hierarchy` | `source=runtime`。节点列表含 player（`/player` 或 `player`） |
| L4-05 | P0 | §3.3 | `runtime_get_properties` id=player | `source=runtime` |
| L4-06 | P0 | §3 观感、§7.8 | `runtime_snapshot_query op=get_node id=player` | **节点 type 是 `goc`（或等价 GO）**，不是 `scriptc`。有 `world_position` 且能解析成 vec3。`source=runtime` |
| L4-07 | P0 | §7.8 | `op=list_ids` | ids 含 player |
| L4-08 | P0 | §7.8 | `op=find type=scriptc` | matches 提到 player 或 cube 的 script |
| L4-09 | P0 | §7.8 | `op=get_subtree id=player` | 回到 GO（`/player`），不是裸 script 节点 |
| L4-10 | P0 | §7.8 | `op=get_path path=/scene_graph/id` | `value` 非空 |
| L4-11 | P0 | §7.7、§7.8 | `op=compare_authoring id=player collection=/main/main.collection` | 有 player 这一项；`authoring.position` 能解析（缺 z 当 0）；`runtime.world_position` 存在 |
| L4-12 | P0 | §7.7 R2 | 两次 observe 后 `runtime_diff` | `source=runtime`；不把 `_raw.json` 的 `id: main` 当成快照 |
| L4-13 | P0 | §7.8 | `op=list` 与 `op=summary` | list 有快照句柄；summary 有 roots / types，无整树 |
| L4-14 | P1 | §7.4 | `inline=preview` | 可有最多 80 浅节点，且 `truncated` 字段合法 |
| L4-15 | P1 | §7.4 | `runtime_observe inline=full`；再对同一份真实 record 注入超预算 padding，走 `observe_envelope` | 本工程快照 &lt; 48 KB 时允许 `status=ok`，快照文件仍在。padding 后必须 `INLINE_TOO_LARGE`，且原快照文件仍在 |
| L4-16 | P1 | §7.8 | `snapshot=live` 的 get_node | 与文件查询分开；主环测试不得把它当默认 |
| L4-17 | P2 | §10 | `runtime_snapshot_query` 假 id | `status=error`，`SNAPSHOT_NOT_FOUND` |
| L4-18 | P3 | §7.3、§8.4 | `runtime_get_hierarchy` `offset=0` `limit=1` | 回包有 `offset`/`limit`；`truncated` 是 bool；`source=runtime` |
| L4-19 | P4 | §7.8、§10 | 对一份注入超预算 padding 的快照 `get_path` `/padding` | `INLINE_TOO_LARGE`；原快照文件仍在 |
| L4-20 | P4 | §7.8、§8.4 | `runtime_snapshot_query op=list_ids` `offset=0` `limit=1` | 回包有 `offset`/`limit`；`truncated` 是 bool |
| L4-21 | P5 | §9、§10 | 无 live 时 `runtime_observe mode=live` | `ENGINE_NOT_RUNNING`。已有快照仍可用 `runtime_snapshot_query` |
| L4-22 | P5 | §10 | `project_run mode=batch` `no_build=true`，`engine` 是会立刻退出、不写 dump 的可执行文件 | `RUNTIME_DUMP_MISSING` |
| L4-23 | P5 | §10 | `runtime_snapshot_query get_node` 假 id | `NOT_FOUND` |
| L4-24 | P5 | §7.8 | 快照目录先多写再 observe | `*.json` 除去 `latest.json` 与 `_raw.json` 不超过 8 个 |
| L4-25 | P6 | §7.8、§8.4 | `runtime_snapshot_query op=get_subtree` `id=player` `offset=0` `limit=1` | 回包有 `offset`/`limit`；`truncated` 是 bool |
| L4-26 | P6 | §7.8、§8.4 | `runtime_snapshot_query op=find` `offset=0` `limit=1` | 回包有 `offset`/`limit`；`truncated` 是 bool |
| L4-27 | P6 | §7.8 | `runtime_observe dest=` 工程内 json，再多写快照触发轮转 | dest 文件仍在且含 `scene_graph` |
| L4-28 | P6 | §7.8、§10 | 对一份 ≥257 节点的快照 `get_subtree` `truncate=false` | `INLINE_TOO_LARGE` |
| L4-29 | P6 | §7.3 | `runtime_get_hierarchy` 不传 limit | 回包 `limit==200` |
| L4-30 | P7 | §7.8 | `runtime_observe dest=` 快照目录内自定义 json，再触发轮转 | dest 文件仍在 |
| L4-31 | P7 | §7.8 | `get_subtree` 不传 limit | 回包 `limit==80` |
| L4-32 | P7 | §7.8 | `find` 不传 limit | 回包 `limit==50` |
| L4-33 | P7 | §7.8 | 对一份 ≥257 节点的快照 `get_subtree` `limit=256` | `status=ok`；`truncated` 为 true；`hint` 非空 |
| L4-34 | P7 | §10 | `runtime_snapshot_query` 假 op | `UNKNOWN_OP` |
| L4-35 | P7 | §10 | `get_subtree` 不传 id | `MISSING_PARAM` |
| L4-36 | P8 | §10 | `get_node` 不传 id | `MISSING_PARAM` |
| L4-37 | P8 | §10 | `compare_authoring` 不传 collection | `MISSING_PARAM` |
| L4-38 | P8 | §7.8 | `runtime_snapshot_query op=list` | `snapshots` 长度 ≤ 8 |
| L4-39 | P8 | §7.8 | `find has_property=world_position` | matches 非空 |
| L4-40 | P8 | §7.8 | `list_ids type=goc` | ids 含 player |
| L4-41 | P9 | §7.8、§10 | `get_path` 指针不以 `/` 开头 | `INVALID_PARAM` |
| L4-42 | P9 | §7.8 | `list_ids id_glob=*player*` | ids 含 player |
| L4-43 | P9 | §7.8 | `find id_glob=*player*` | matches 非空 |
| L4-44 | P9 | §7.8 | `compare_authoring` 不传 limit | 回包 `limit==80` |
| L4-45 | P9 | §10 | `get_path` 不传 path | `MISSING_PARAM` |
| L4-46 | P10 | §10 | `get_node` 假 id | `NOT_FOUND`；hint 提到 `runtime_get_hierarchy` |
| L4-47 | P10 | §7.8、§10 | `get_path` 指针 `/no/such/pointer` | `NOT_FOUND` |
| L4-48 | P10 | §7.8 | `list_ids` 不传 limit | 回包 `limit==200` |
| L4-49 | P10 | §7.8 | `get_node id=player` 不传 snapshot | `status=ok`；读的是 latest |
| L4-50 | P11 | §10 | `get_subtree` 假 id | `NOT_FOUND`；hint 提到 `runtime_get_hierarchy` |
| L4-51 | P11 | §10 | `runtime_get_properties` 不传 id | `MISSING_PARAM` |
| L4-52 | P12 | §7.8 | `runtime_get_properties id=player` 不传 snapshot | `status=ok`；`source=runtime`；`snapshot` 等于 `latest.json` 的 id |
| L4-53 | P12 | §7.8 | `get_subtree id=player` 不传 depth | `status=ok`；回包 `depth==8` |

### 5.5 L5 干预

前置：live 仍在。player.script 必须 `acquire_input_focus`，binding 有 `left` / `right`。

| ID | P | 需求 | 步骤 | 断言 |
| --- | --- | --- | --- | --- |
| L5-01 | P0 | §11 R3 | `game_eval` 无 `confirm` | `NOT_ALLOWED` |
| L5-02 | P0 | §11 R3 | `game_eval` `os.execute(...)` 且 `confirm=true` | `NOT_ALLOWED`（禁 os/io/socket） |
| L5-03 | P0 | §11 R3 | `runtime_input key=left hold=24` | `status=ok`。再 observe + `get_node player`：`world_position.x` 比输入前小至少 0.5 |
| L5-04 | P0 | §7.7 | 输入后再 `compare_authoring` | 有 position / world_position 类 delta；runtime x &lt; authoring x |
| L5-05 | P0 | §11 R3 | 再 `runtime_observe` + `get_node player`，立刻 `game_eval confirm=true`：`return go.get_position('/player')` | 结果能解析成 vector3；x 与**这一次**刚观察的 player 同量级（容差 8，因为游戏还在跑） |
| L5-06 | P0 | §11 R3 | `runtime_debug`：`status` / `pause` / `stack` / `set_breakpoint` file=`/main/player.script` line=8 / `continue` | 各 `status=ok`。回包不得要求人在 `debug>` 打字（`prompt` 不为 true） |
| L5-07 | P0 | §7.3 | `runtime_screenshot` | 可选：成功则 PNG 魔数对；失败标 SKIP，不挡 P0 签字（截屏不是主环） |
| L5-08 | P0 | §9 | `project_stop` | 同 L3-04 |
| L5-09 | P2 | §10、§11 | 无 live 时 `runtime_input` | `ENGINE_NOT_RUNNING` |
| L5-10 | P2 | §10 | `session_activate` 假 id | `UNKNOWN_TARGET` |
| L5-11 | P2 | §11 R3 | live 时 `runtime_debug op=locals` | `status=ok`；`prompt` 不为 true |
| L5-12 | P3 | §11 R3 | live 时对 `update` **会执行的行**下断（`/main/player.script` line=9），`continue` 后再 `stack` | `status=ok`；`frames` 非空；`stack_reason=breakpoint`；`prompt` 不为 true |
| L5-13 | P3 | §11 R3 | `runtime_debug op=clear_breakpoint` 同一 file/line | `status=ok`；`prompt` 不为 true |
| L5-14 | P3 | §11 R3 | `game_eval` `confirm=true` 且 code &gt; 4096 字节 | `INVALID_PARAM` |
| L5-15 | P3 | §11 R3 | `runtime_input` `hold=31` | `INVALID_PARAM` |
| L5-16 | P3 | §11 R3 | live 时 `runtime_debug op=step` | `status=ok`；`prompt` 不为 true |
| L5-17 | P4 | §11 R3 | `runtime_input` 17 个 key | `INVALID_PARAM` |
| L5-18 | P5 | §7.5、§8.1 | `session_activate` 指向其它工程的 editor | `NOT_ALLOWED` |
| L5-19 | P5 | §11 R3 | L5-12 命中后再 `runtime_debug op=status` | `frames` 非空；`stack_reason=breakpoint`；`prompt` 不为 true |
| L5-20 | P6 | §7.5、§8.1 | `session_activate` 用本工程 editor 的 url | `status=ok` |
| L5-21 | P8 | §7.5、§8.1 | live 时 `session_activate` `id=cli-live` | `status=ok`；`session.kind=cli-live` |
| L5-22 | P13 | §10、§11 R3 | `runtime_debug op=explode` | `UNKNOWN_OP` |
| L5-23 | P13 | §10、§11 R3 | `runtime_debug op=set_breakpoint` 不传 file | `MISSING_PARAM` |
| L5-24 | P13 | §10、§11 R3 | `runtime_input` 不传 key | `MISSING_PARAM` |
| L5-25 | P13 | §10、§11 R3 | `game_eval confirm=true` 不传 code | `MISSING_PARAM` |
| L5-26 | P14 | §10、§11 R3 | `runtime_input key=not-a-key` | `INVALID_PARAM` |
| L5-27 | P14 | §10、§11 R3 | `runtime_debug op=set_breakpoint` 有 file 不传 line | `MISSING_PARAM` |

L5-07 在需求里不是主环，故失败 → SKIP，不算 P0 崩盘。成功仍要验 PNG。

### 5.6 L6 领域资源

每个领域一套 **P0** 最小合同：`create` → 磁盘文件非空 → `get` ok → `list` 含该文件名。另加读回合同。

| ID | P | 工具 | 额外断言 |
| --- | --- | --- | --- |
| L6-01 | P0 | `atlas_manage` | create `/main/sprites.atlas` |
| L6-02 | P0 | `tilesource_manage` | create `/main/tiles.tilesource` |
| L6-03 | P0 | `tilemap_manage` | create + `add_layer` name=ground + `set_tile` (2,3)=7 + `get_tile` == 7。文件含 `ground` |
| L6-04 | P0 | `gui_manage` | create + `add_text` id=score + `set_node` text=99 + `get_node` text==99 |
| L6-05 | P0 | `input_binding_manage` | create `/input/extra.input_binding` + `add_key` KEY_SPACE/jump。文件含 `jump` |
| L6-06 | P0 | `particlefx_manage` | create `/main/burst.particlefx` |
| L6-07 | P0 | `material_manage` | create `/main/custom.material` |
| L6-08 | P0 | `font_manage` | create `/main/ui.font` |
| L6-09 | P0 | `sound_manage` | create `/main/beep.sound` |
| L6-10 | P0 | `gamepads_manage` | create `/input/default.gamepads` |
| L6-11 | P0 | `display_profiles_manage` | create `/main/display.display_profiles` |
| L6-12 | P0 | `model_manage` | create `/main/box.model` |
| L6-13 | P0 | `factory_manage` | create `/main/enemy.factory` |
| L6-14 | P0 | `collectionfactory_manage` | create `/main/room.collectionfactory` |
| L6-15 | P0 | `collectionproxy_manage` | create `/main/level.collectionproxy` |
| L6-16 | P0 | `collisionobject_manage` | create `/main/body.collisionobject` |
| L6-17 | P0 | `cubemap_manage` | create `/main/sky.cubemap` |
| L6-18 | P0 | `mesh_manage` | create `/main/mesh.mesh` |
| L6-19 | P0 | `texture_profiles_manage` | create `/main/textures.texture_profiles` |
| L6-20 | P0 | `compute_manage` | create `/main/work.compute` |
| L6-21 | P0 | `appmanifest_manage` | create `/main/app.appmanifest` |
| L6-22 | P0 | `render_manage` | create `/main/custom.render` |
| L6-23 | P0 | `camera_manage add` collection+id=cube | 再 get cube properties 含 camera |
| L6-24 | P0 | `project_manage settings_get` `project.title` | 值是 `MCP Acceptance` |
| L6-25 | P0 | `settings_set` `project.version=9.9` 再 get | 读回是 `9.9`（或实现规范化后的等价） |
| L6-26 | P1 | 各 manage 的 `set_property` / `remove` | 按 schema 各验一条 |
| L6-27 | P10 | `atlas_manage` 假 op | `UNKNOWN_OP` |
| L6-28 | P15 | §8.2、§10 | `project_manage settings_get` 不传 key | `MISSING_PARAM` |
| L6-29 | P15 | §8.2、§10 | `project_manage settings_set` 不传 key | `MISSING_PARAM` |

关着编辑器时这些写必须 `undoable: false` 且 `source: disk`（P1，需求 §5 L6）。

---

## 6. 工具覆盖（防漏）

`scripts/agent/agent_mcp.py` 的 `TOOLS` 每一行必须出现在下表。新增 tool 先改需求，再改本表，再改执行器。

| Tool | 层 | 覆盖用例 | 最低读回 |
| --- | --- | --- | --- |
| `editor_state` | L0 | L0-01；P2：L0-09；P6：L0-13；P7：L0-14；P17：L0-16 | title / root / commands；live 后 running；stop 后不是 running；假 timeout 为 INVALID_PARAM |
| `project_doctor` | L0 | L0-02、ENV-04 | ready / mcp / java_required |
| `session_manage` | L0 | L0-03 | 含本工程 editor |
| `session_activate` | L0 | L0-03；P2：L5-10；P5：L5-18；P6：L5-20；P8：L5-21 | ok；假 id 为 UNKNOWN_TARGET；其它工程 NOT_ALLOWED；可按 url / cli-live 钉 |
| `api_manage` | L0 | L0-04；P3：L3-06 | 文档命中；关编辑器走引擎 `/*#` |
| `editor_manage` | L0 | L0-05、ENV-09；P17：L0-17 | mcp_config 无 URL，含 `tool_timeout_sec`。**不验 `quit`**（会杀掉验收进程） |
| `collection_open` | L1 | L1-01；P20：L1-40 | ok；缺 path 为 MISSING_PARAM |
| `collection_get_hierarchy` | L1 | L1-01、L1-02、L1-16；P2：L1-25；P24：L1-50 | source + ids；分页 truncated；缺 path 为 MISSING_PARAM |
| `collection_save` | L1 | L1-13；P21：L1-43 | 磁盘含 id；缺 path 为 MISSING_PARAM |
| `collection_manage` | L1 | L1-10、L1-11、L1-12；P2：L1-27；P22：L1-46；P24：L1-52；P27：L1-60、L1-61 | create / remove / get_roots / add_instance；缺 id / path / collection 为 MISSING_PARAM |
| `gameobject_create` | L1 | L1-02、L1-07；P23：L1-47 | hierarchy + position；缺 collection 为 MISSING_PARAM |
| `gameobject_get_properties` | L1 | L1-02、L1-03、L1-16；P6：L1-30 | source + position / components；缺 id 为 MISSING_PARAM |
| `gameobject_manage` | L1 | L1-03、L1-04；P1：L1-17、L1-18；P6：L1-29；P19：L1-38；P22：L1-45；P25：L1-53 | set_property 读回；find 命中；假 op 为 UNKNOWN_OP；缺 property / name / id 为 MISSING_PARAM |
| `component_add` | L1 | L1-09；P24：L1-51 | 有 label；缺 id 为 MISSING_PARAM |
| `component_manage` | L1 | L1-19；P19：L1-39；P25：L1-54 | P1；缺 component / property 为 MISSING_PARAM |
| `script_create` | L1 | L1-05；P3：L0-11、L0-12；P20：L1-41 | 文件 + init；building/observing 拒写；缺 path 为 MISSING_PARAM |
| `script_attach` | L1 | L1-06、L1-07；P23：L1-48 | components；缺 id 为 MISSING_PARAM |
| `script_patch` | L1 | L1-08、L2-05；P23：L1-49 | 文本变化；缺 path 为 MISSING_PARAM |
| `script_manage` | L1 | L1-08；P1：L1-20；P20：L1-42 | read 文本；缺 path 为 MISSING_PARAM |
| `filesystem_manage` | L1 | L1-14、L1-21、L1-22；P2：L1-24、L1-26；P10：L1-31；P13：L1-32；P15：L1-34；P16：L1-33；P18：L1-35–L1-37；P25：L1-55；P26：L1-56–L1-58；P27：L1-59 | 读写搜拷分页；拒读/删快照；假 op 为 UNKNOWN_OP；`..` / 无前导 `/` 为 INVALID_PARAM；缺 query / path / dest / text |
| `batch_execute` | L1 | L1-15；P2：L1-28；P21：L1-44 | rolled_back；混 check 时 atomic=false；缺 commands 为 MISSING_PARAM |
| `project_check` | L2 | L2-01、L2-05、L2-06 | launched=false；坏 Lua 有 file:line |
| `project_build` | L2 | L2-02 | launched=false |
| `logs_read` | L2 | L2-03；P2：L2-09；P3：L2-10、L2-11；P4：L2-12；P5：L2-13；P11：L2-15；P12：L2-16；P14：L2-18 | lines 或 issues；分页；severity/prints；editor-file；engine-log；domain；q；默认 source；假 source 为 INVALID_PARAM |
| `diagnostics_read` | L2 | L2-04；P13：L2-17 | issues 列表；不重编；不写 last_check |
| `editor_preview` | L2 | L2-07；P21：L2-19 | PNG 魔数；缺 path 为 MISSING_PARAM |
| `project_manage` | L2/L6 | L6-24、L6-25；P1：L2-08；P15：L6-28、L6-29 | settings 读回。缺 key 为 MISSING_PARAM。`stop` 与 `project_stop` 对齐即可 |
| `project_run` | L3 | L3-01、L3-03；P4：L3-07；P5：L3-08、L4-22；P11：L3-10 | 一个 live；batch 跑完不留 live；假引擎 / 无 dump；假 mode |
| `project_stop` | L3 | L3-04、L5-08 | 进程死 |
| `runtime_state` | L4 | L4-03；P8：L0-15 | alive；stop 后 no_runtime |
| `runtime_observe` | L4 | L4-01、L4-02；P5：L4-21、L4-24；P6：L4-27；P7：L2-14、L4-30 | 句柄 + 摘要；无 live 拒绝；快照只留 8；dest 不轮转；logs.lines ≤ 40 |
| `runtime_snapshot_query` | L4 | L4-06–L4-13；P2：L4-17；P4：L4-19、L4-20；P5：L4-23；P6：L4-25、L4-26、L4-28；P7：L4-31–L4-35；P8：L4-36–L4-40；P9：L4-41–L4-45；P10：L4-46–L4-49；P11：L4-50；P12：L4-53 | get_node 是 GO；分页；默认 latest / list_ids 200；id_glob；Pointer 校验；NOT_FOUND hint；默认 depth 8 |
| `runtime_get_hierarchy` | L4 | L4-04；P3：L4-18；P6：L4-29 | source=runtime；分页 truncated；默认 limit 200 |
| `runtime_get_properties` | L4 | L4-05；P11：L4-51；P12：L4-52 | source=runtime；缺 id 为 MISSING_PARAM；默认读 latest |
| `runtime_diff` | L4 | L4-12 | 两份真快照 |
| `runtime_screenshot` | L5 | L5-07 | 可选 PNG |
| `runtime_input` | L5 | L5-03；P2：L5-09；P3：L5-15；P4：L5-17；P13：L5-24；P14：L5-26 | player x 变小；无 live 拒绝；hold / 事件上限；缺 key / 假 key |
| `game_eval` | L5 | L5-01、L5-02、L5-05；P3：L5-14；P13：L5-25 | 无 confirm 拒绝；go.* 返回向量；超 4096 字节拒绝；缺 code 为 MISSING_PARAM |
| `runtime_debug` | L5 | L5-06；P2：L5-11；P3：L5-12、L5-13、L5-16；P5：L5-19；P13：L5-22、L5-23；P14：L5-27 | 不进 `debug>`；命中后 frames；status 仍带上次栈；假 op / 缺 file / 缺 line |
| `atlas_manage` … `appmanifest_manage` | L6 | L6-01–L6-22；P10：L6-27 | 文件 + get + list；假 op 为 UNKNOWN_OP |
| `camera_manage` | L6 | L6-23 | cube 上有 camera |
| `tilemap_manage` / `gui_manage` / `input_binding_manage` | L6 | L6-03–L6-05 | 读回 tile / text / jump |

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

---

## 8. 报告

执行器 stdout 一行一条：

```text
PASS  L4-06  get_node is goc with world_position  ...
FAIL  L1-15  batch_execute rollback  Nested batch_execute is not allowed
SKIP  L5-07  optional screenshot
```

`.cache/mcp-acceptance-report.json`：

```json
{
  "spec": "AI_MCP_ACCEPTANCE.md",
  "editor_sha1": "...",
  "zip_run": "34294924984",
  "results": [
    {"id": "L4-06", "priority": "P0", "passed": true, "status": "ok", "message": ""}
  ],
  "p0_failed": [],
  "p1_failed": [],
  "p2_failed": [],
  "p3_failed": [],
  "p4_failed": [],
  "p5_failed": [],
  "p6_failed": [],
  "p7_failed": [],
  "p8_failed": [],
  "p9_failed": [],
  "p10_failed": [],
  "p11_failed": [],
  "p12_failed": [],
  "p13_failed": [],
  "p14_failed": [],
  "p15_failed": [],
  "p16_failed": [],
  "p17_failed": [],
  "p18_failed": [],
  "p19_failed": [],
  "p20_failed": [],
  "p21_failed": [],
  "p22_failed": [],
  "p23_failed": [],
  "p24_failed": [],
  "p25_failed": [],
  "p26_failed": [],
  "p27_failed": [],
  "p1_skipped": []
}
```

签字条件：`p0_failed` 为空，且每个 P0 ID 都出现在 `results` 里（漏跑 = 失败）。

---

## 9. 空工程主环（需求 §3.1 的一条故事）

下面不是另一套用例，是 P0 用例必须串起来的故事。缺一环不能签字。

```text
开空 collection
  → 建 cube / player，挂 script，绑 left/right（L1-*）
  → check 通过；坏 Lua 能报 file:line（L2-*）
  → project_run live（L3-01）
  → observe 摘要（L4-01）
  → get_node player = 运行时 world_position（L4-06）
  → input left，x 变小（L5-03）
  → compare_authoring 能区分「集合里的 0」和「脚本挪过」（L5-04）
  → project_stop（L5-08）
```

问「现在玩家在哪」：必须用 L4-06 的运行时坐标回答，禁止用 collection 初始值冒充。
