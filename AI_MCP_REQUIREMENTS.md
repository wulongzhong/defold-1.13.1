# Defold AI MCP 需求文档

状态: **规划稿 / 指导后续实现**（尚未按本文施工）
适用: 本仓库 Defold **1.13.1**
读者: 实现 Agent CLI / 编辑器 `/agent` / 引擎观察通道的人；以及用 Codex / Cursor 写游戏的人
关系: [`AI_MCP.md`](AI_MCP.md) 记录已落地的路径 A/B 和历史对照。**以后以本文为产品需求**；实现细节仍回写 `AI_MCP.md`。

---

## 1. 问题

现在的 MCP 能改磁盘和编辑器图，能编译，能在退出时留一张图和一段日志。它**看不见正在跑的游戏的结构**。

Agent 面对的是作者态（`.collection` / `.go` / 编辑器属性），不是玩家态（脚本改过的位置、动态 factory、世界坐标、当前 collection proxy）。于是它会：

- 没有树和属性可读时，只好拿像素猜「方块在不在、偏了多少」
- 把编辑器里的 `position` 当成运行时真相
- 无法回答「现在树上有哪些对象、这个 go 的 `velocity` 是多少」
- 无法区分「资源写错了」和「跑起来之后脚本改坏了」

这不是工具少两个名字的问题，是观察闭环缺了一整层。

目标不是再堆一堆 `*_manage`。目标是做成一套**完整、健壮、可重复**的第一方 AI MCP：Agent 像人一样写、编、跑、看、对照、再改。

---

## 2. 产品目标

一句话：**Agent 打开空 Defold 工程，能把「意图 → 资源 → 可运行游戏 → 结构化观察 → 下一轮修改」走通，而且每一跳都有机器可读的对错。**

完整闭环：

```text
感知工程（文件 / 编辑器图 / API 文档）
    → 修改（可撤销的图变异，或标明不可撤销的磁盘写）
    → 编译（check，不启动）
    → 运行（headless 批跑 或 保活 Play）
    → 观察（运行时树 + 属性 + 日志；需要时再截屏）
    → 诊断（作者态 vs 运行态，issues 带 file:line）
    → 再改
```

「健壮」指的是：缺编辑器也能工作；开着编辑器时走同一套工具名；失败有稳定错误码；大树可分页；作者态和运行态在回包里分得开；Agent 不必自己 curl、不必往游戏工程塞插件。

---

## 3. 成功标准

下面四条同时成立，才叫完整 MCP 的第一里程碑（R1）。少一条都不算。

1. **空工程能成环。** 「做个能左右走的方块」。Agent 只用 MCP / `defold_agent.py`，不装工程插件，能 check、能跑、能根据观察改位置/输入/颜色，直到对上。
2. **观察以结构化快照为主。** 一次观察**始终**把完整 scene_graph 写成文件，MCP 回包默认只有：快照文件句柄、摘要（节点数 / 根 id / 类型直方图）、issues、目标是否还活着。完整树不进上下文。要某一节点或子树，用查询工具打文件。截屏是可选附件，不是 observe 的默认产物，也不是主环必做步骤。
3. **作者态 ≠ 运行态。** `collection_get_hierarchy` / `gameobject_get_properties` 标明 `source: "editor" | "disk"`。运行时工具标明 `source: "runtime"`。同一 id 两边都能查，Agent 能对比。
4. **编辑器可关。** 关着时走 bob + dmengine + 磁盘回退。开着时同一工具打编辑器图，创建可 Ctrl+Z。

对用户的观感：问「现在玩家在哪」，得到的是运行时 `world_position`，不是 collection 文件里的初始值。

---

## 4. 硬约束（沿用，不重开）

这些已经在仓库里决定过，本文不改：

| # | 约束 |
| --- | --- |
| 1 | 客户端只走 **stdio MCP**。禁止默认 `url = "http://..."`。 |
| 2 | **第一方源码**：`scripts/agent/*` + `editor/src/clj/editor/agent.clj` + 引擎观察通道。不进 `addons/`，不写 `*.editor_script`，不做 `defold-ai attach` / FastMCP `:8000`。 |
| 3 | Agent **不准把 curl / `/eval` 当主协议**。发现、鉴权、HTTP 全由 CLI 做。 |
| 4 | 命名用 Defold 词：collection / gameobject / component / script。 |
| 5 | 写入可撤销则走编辑器图事务；磁盘回退必须 `undoable: false` 且 `source: "disk"`。 |
| 6 | 只绑 loopback。引擎服务、编辑器 HTTP 都是本机。 |
| 7 | 业务失败 HTTP 200 + `{status:"error", error.code}`。禁止把 Python / Lua / Clojure traceback 当主回包。 |
| 8 | 工具数压在客户端约 100 以下：常驻 core + 高频具名 + `<domain>_manage(op, params)`。 |

---

## 5. 能力分层

按 Agent 实际要做的事分层。下层没稳，不准靠堆上层工具假装完整。

| 层 | 名字 | 现在 | 本文要求 |
| --- | --- | --- | --- |
| L0 | 工程感知 | 部分：`editor_state`、磁盘读、`api_manage` | 稳定 readiness、工程根、bob/engine/editor 是否可用 |
| L1 | 作者态编辑 | 已有 v0 工具面 | 补 schema、原子 batch、缺的资源域后做 |
| L2 | 编译诊断 | `check` / bob diagnostics / `ERROR:BUILD` | 继续统一 issues 信封 |
| L3 | 运行与生命周期 | `run --frames` 会退出；编辑器 Play 对 Agent 不透明 | 批跑 + 保活 + 停止 + 发现 target |
| L4 | **运行时观察** | **几乎没有**（退出时一张图 + stdout，没有树） | **本阶段主需求：快照文件 + 查询。截屏只是其中一项** |
| L5 | 运行时干预 | 明确未做 | R2 再评估；不阻塞 L4 |
| L6 | 领域资源 | 未做 atlas/tilemap/… | 观察闭环稳定后再做 |

`AI_MCP.md` 把「运行时灌输入 / `game_eval` / debugger 截帧」整包标成不做。那是 L5。L4 **不是**同一件事。引擎里已经有只读观察通道，见 §7。

---

## 6. 现状对照

| Agent 要问的 | 现在实际打到的 | 缺口 |
| --- | --- | --- |
| 工程开着吗、当前资源是谁 | `editor_state` / 磁盘 | 还没有统一的「工具是否齐」doctor 进 MCP |
| collection 树长什么样 | 编辑器图或 `.collection` 文本 | 不是运行时树 |
| 这个 go 的属性 | 编辑器 `_properties` | 不是脚本改过的值 |
| 编译过不过 | `check` + issues JSON | 够用 |
| 画面对不对（可选） | `editor_preview` 或退出时 `--screenshot` | 保活时没有随时截；且不该是主观察手段 |
| 运行时树上有谁、在哪 | **没有 MCP 工具** | 引擎已有 `GET /scene_graph`，CLI 没接 |
| 运行时报错 | 解析 stdout；编辑器开着才有 `logs_read` | 批跑和保活日志没合成一条工具 |
| 目标还在跑吗 | 没有 | 编辑器自己有 target / mDNS，CLI 没用 |

结论：L1/L2 能交差。**完整 MCP 卡在 L3/L4。** 不是引擎从零发明 Godot debugger wire。

---

## 7. 运行时观察（核心需求）

### 7.1 已经存在、必须复用的通道

调试引擎（`dLib::IsDebugMode()`）会起 **engine service**（默认 `127.0.0.1:8001`，可用 `DM_SERVICE_PORT` / `dynamic`）：

| 端点 | 内容 | 给 Agent 的价值 |
| --- | --- | --- |
| `GET /info` | version / platform / sha1 / log_port | 发现、探活 |
| `GET /state` | connection_mode | 是否被编辑器连着 |
| `GET /ping` | 探活 | 超时判定 |
| `GET /scene_graph` | **整棵运行时树的 JSON**：id / type / resource、local+world 变换、组件、`go.property` 当前值、children | **运行时快照的主数据** |
| `GET /gameobjects_data` | 分析器用的二进制 | 不作为 MCP 主合同 |
| `GET /screenshot` | **R1 新增**：当前帧 `image/png` | **可选**视觉能力。不走 `POST /post`。observe 默认不调 |
| `POST /post/{socket}/{ddf}` | 给运行中的引擎发 DDF | **L5 / R3 才考虑**。观察层不用 |

`TraverseIterateProperties` 已经能吐：

- go：`id`, `type`, `resource`, `position`, `rotation`, `scale`, `world_position`, `world_rotation`, `world_scale`
- component：`id`, `type`, `resource`，以及 script 的 `go.property` 运行时值

编辑器侧已经会发现 launched / mDNS target。CLI 和 MCP **还没有**把这些收成工具。

### 7.2 两种观察模式

| 模式 | 何时 | 行为 |
| --- | --- | --- |
| **batch** | 编辑器关着，或 Agent 要可复现的一轮 | `dmengine` 跑 N 帧后退出。退出时写**完整 scene_graph**。CLI 包成快照文件，MCP 默认只回摘要 + 文件句柄。截屏仅当请求了才写。 |
| **live** | 游戏还在跑（CLI 保活或编辑器 Play） | 发现 target → 拉完整 `/scene_graph` → **先落盘** → 读日志。MCP 同样只回摘要。不退出游戏。截屏另走 `runtime_screenshot` 或 `include=["screenshot"]`。 |

无论哪种模式：**完整信息在文件里，不在模型上下文里。** 见 §7.8。

R0 必须先把 batch 做硬：空工程主环不依赖「一直开着一个窗口」。R1 补 live，否则「现在玩家在哪」仍然要重启游戏。

### 7.3 必须交付的工具

命名固定。不要用 Godot 的 `node_*`。

| Tool | 作用 | 最低 params |
| --- | --- | --- |
| `runtime_state` | 有没有可观察的 target | 无；可选 `url` |
| `runtime_observe` | **主入口**：落盘完整快照，回摘要 | 见 §7.4、§7.8。可选 `inline`、`frames`、`dest`、`include` |
| `runtime_snapshot_query` | **从快照文件取精确切片** | `snapshot` + `op`，见 §7.8。引擎已死也能查 |
| `runtime_get_hierarchy` | 查树（默认读最新快照文件） | `snapshot` 默认 `"latest"`；`"live"` 才打 HTTP。`limit` 默认 200 |
| `runtime_get_properties` | 一个 go / component（默认读最新快照） | `id`，可选 `component`、`snapshot` |
| `runtime_screenshot` | **可选**：当前帧写成 PNG | 不在 core。live 打 `GET /screenshot`。observe 默认不调用 |
| `project_run` | 启动；`mode=batch\|live` | `frames` 仅 batch。**编辑器关着也允许 live**，进程由 CLI 管 |
| `project_stop` | 停掉本 CLI 拉起的引擎 | 无 |

`logs_read` 扩展为：编辑器 `/console` **或** 本轮 dmengine stdout / log_port，回包写 `source`。不要再做一个只读 stdout 的平行工具。

`editor_preview` 仍是作者态预览，不是运行时截屏。回包继续 `source: "editor-preview"`。

### 7.4 `runtime_observe` 信封

这是 Agent 主环应该优先调用的工具。它的职责是**采集并落盘**，不是把整棵树喂给模型。

默认 `inline=summary`：

```json
{
  "status": "ok",
  "readiness": "ready",
  "data": {
    "source": "runtime",
    "mode": "batch",
    "target": {
      "url": "http://127.0.0.1:8001",
      "name": "Defold",
      "alive": false
    },
    "frame": 30,
    "snapshot": {
      "id": "20260908T093012Z-a1b2",
      "path": "C:/game/.internal/agent/snapshots/20260908T093012Z-a1b2.json",
      "bytes": 182440,
      "node_count": 240,
      "inline": "summary"
    },
    "summary": {
      "roots": ["main"],
      "types": {"goc": 12, "scriptc": 4, "spritec": 3}
    },
    "issues": [],
    "logs": { "source": "engine-stdout", "total": 18, "lines": [] }
  }
}
```

`inline`（已决）：

| 值 | 回包里有什么 | 何时用 |
| --- | --- | --- |
| **`summary`（默认）** | 句柄 + 摘要，**没有**节点列表 | 主环。几乎总是这个 |
| `preview` | 另加最多 **80** 个浅节点（id / type / resource / `world_position` / 子 id） | 空工程扫一眼；仍须 `truncated` |
| `full` | 把快照 JSON **整份**放进 `data.snapshot.inline_data` | 只要完整信息且体积 ≤ **48 KB**。超过则 `INLINE_TOO_LARGE`，文件已写好，hint 去 `runtime_snapshot_query` |

规则：

- **每次 observe 都写完整快照 JSON**，与 `inline` 无关。`full` 失败不等于采集失败。
- 禁止把引擎 `/scene_graph` 原样当默认 MCP 回包。
- `logs.lines` 默认最多 40 行；全文走 `logs_read` 或 sidecar。
- 没有 target：`ENGINE_UNREACHABLE`。batch 已退后：`target.alive=false`，只要 **JSON 快照**写成仍算成功。缺 PNG 不是失败。
- 主环：记住 `snapshot.id`，用 `runtime_snapshot_query` 取需要的节点。不要指望 observe 回包里已有完整树。
- **截屏不是 observe 的默认行为。** 只有 `include` 含 `"screenshot"`，或单独调用 `runtime_screenshot`，才写 PNG 并把路径放进回包。问位置、树、属性、报错时不要先截图。

### 7.5 发现 target（按顺序）

1. 本 CLI 刚拉起的进程：解析 stdout「Target listening」+ 实际端口（`DM_SERVICE_PORT=dynamic` 时必须解析，不能写死 8001）。
2. 环境变量 `DEFOLD_AI_ENGINE_URL`（调试用）。
3. 工程 `.internal/agent/engine.json`（CLI 写入的上次成功地址；PID 对不上则作废）。
4. 编辑器已选 target（编辑器开着时由 `/agent` 转发，Agent 仍不 curl）。
5. 探测 `http://127.0.0.1:8001/info`。

多 target：R1 只认一个「当前」target。`runtime_state` 列出看见的，`session_activate` 以后再钉。禁止用「后写覆盖的全局 url 文件」互踩两个工程。

### 7.6 引擎要补的（只补观察，不补 MCP）

现有 `/scene_graph` 够读，但还不够给 Agent 用：

| 缺口 | 要求 | 阶段 |
| --- | --- | --- |
| 退出时没有运行时树 | `dmengine --runtime-dump=path.json`：退出时写完整 scene_graph。不依赖是否同时截屏 | R0 |
| 保活时没有随时截屏 | 引擎服务增加 **`GET /screenshot`** → `image/png`（loopback）。复用已有 `ReadPixels`。**可选能力**，不走 `POST /post` | R1 |
| 整树过大 | **完整树只进快照文件。** MCP 回包用摘要 / 查询切片。查询工具的单次回包硬上限 256 节点 / 48 KB。R1 可给 `/scene_graph` 加 `?id=` 减少拉取时间，但落盘仍应是调用方请求的那棵子树或全量 | R0 文件 + 查询；R1 可选引擎过滤 |
| 端口对 Agent 不透明 | CLI 拉起时写 `.internal/agent/engine.json`；dynamic 端口必须能从日志或该文件读到 | R0 |

**不要**把 MCP JSON-RPC 做进引擎。**不要**为了观察往游戏 collection 里挂 `_mcp_game_helper`。

### 7.7 作者态 vs 运行态对照（R1）

同一次诊断里 Agent 应能拿到：

| | 作者态 | 运行态 |
| --- | --- | --- |
| 树 | `collection_get_hierarchy` | `runtime_get_hierarchy`（默认读快照文件） |
| 一个对象 | `gameobject_get_properties` | `runtime_get_properties` / `runtime_snapshot_query op=get_node` |
| 日志 | `logs_read`（编辑器） | 同工具，source 为引擎 |
| 画面（可选） | `editor_preview` | `runtime_screenshot`，或 observe 且 `include` 含 screenshot |

R1 靠两边各查一次手比，不单独做 diff 工具。`runtime_diff` **进 R2**。对照时运行态一侧读**同一份快照文件**，不要一边读文件一边打 live HTTP。

### 7.8 快照文件与精确查询（已决）

完整信息可以进 MCP 回包（`inline=full` 且 ≤ 48 KB），但**默认不这么做**。完整信息的归宿是文件；模型只拿它需要的切片。

#### 文件

| 项 | 决定 |
| --- | --- |
| 目录 | `{project}/.internal/agent/snapshots/` |
| 一轮产物 | `{id}.json`（完整快照，**必有**）。`{id}.png` **仅当请求了截屏**。`latest.json`（及可选 `latest.png`）改写为这一轮（Windows 不用 symlink） |
| `id` | UTC `YYYYMMDDTHHMMSSZ` + 短随机，例如 `20260908T093012Z-a1b2` |
| JSON 合同 | `{schema:1, id, source:"runtime", mode, captured_at, frame, target, scene_graph, issues}`。`screenshot` 字段可缺省。`scene_graph` 就是引擎那棵完整树 |
| 日志 | 不塞进快照 JSON（以免再胀）。observe 回包带截断 lines；全文走 `logs_read` 或 sidecar |
| 保留 | 最近 **8** 个 id。超出删最旧的 `{id}.*`。显式 `dest` 的文件不参与轮转 |
| 写入时机 | batch 退出时、live 每次 observe。采集失败不覆盖 `latest` |

#### `runtime_snapshot_query`

从**文件**取精确部分。不要求引擎还活着。`snapshot` 为 `"latest"`（默认）、快照 `id`、或绝对/`/` 工程路径。

| op | 作用 | 默认回包上限 |
| --- | --- | --- |
| `list` | 已有快照 id / 路径 / 字节数 / 节点数 | 8 条 |
| `summary` | 与 observe 摘要相同 | — |
| `list_ids` | 分页 id；可选 `type`、`id_glob` | `limit` 200 |
| `get_node` | 一个 go 或 component 的完整属性 | 1 个节点 |
| `get_subtree` | 从 `id` 起的子树；`depth`、`limit` | `limit` 80 |
| `find` | `type` / `id_glob` / `has_property` | `limit` 50 |
| `get_path` | JSON Pointer（RFC 6901），例如 `/scene_graph/children/0/world_position` | 48 KB；超过 `INLINE_TOO_LARGE` |

单次查询回包超过 256 节点或 48 KB：成功则必须 `truncated: true` + hint；`get_path` / 调用方 `truncate=false` 则报 `INLINE_TOO_LARGE`。

`runtime_get_hierarchy` / `runtime_get_properties` 是同一套读文件代码的薄封装：`snapshot` 默认 `"latest"`。只有 `snapshot="live"` 才打引擎 HTTP（另一帧，和上一份快照可能对不齐）。**主环先 observe，再 query 文件。**

#### 观察能力清单（截屏不是中心）

| 能力 | 工具 | 主环？ |
| --- | --- | --- |
| 落盘完整运行时树 | `runtime_observe` | **是** |
| 按 id / 类型 / Pointer 取切片 | `runtime_snapshot_query` | **是** |
| 编译/运行 issues | observe 信封 / `logs_read` | **是** |
| 探活 / 生命周期 | `runtime_state`、`project_run` / `stop` | **是** |
| 当前帧画面 | `runtime_screenshot` | 否。只在问「看起来对不对、颜色、重叠」时用 |

#### Agent 用法（写进 `AGENTS.md`）

```text
runtime_observe                         → 记住 snapshot.id
runtime_snapshot_query get_node / find  → 位置、属性、树上有谁
logs_read                               → 报错
runtime_screenshot                      → 仅当结构化数据不够、需要看画面
需要整棵且很小才 inline=full
```

禁止：把快照 JSON 整文件读进对话；对 `filesystem_manage read_text` 打开 snapshot。查询必须走 query 工具。禁止把「先截一张图」写成默认循环。

---

## 8. 完整工具面（目标）

### 8.1 常驻 core（始终加载）

| Tool | 层 | 现在 | 要求 |
| --- | --- | --- | --- |
| `editor_state` | L0 | 有 | 增加 `engine`：有无 target、url、alive |
| `collection_get_hierarchy` | L1 | 有 | schema 写清 params；分页保留 |
| `gameobject_get_properties` | L1 | 有 | 回包明确 `source` |
| `session_activate` | L0 | 单会话空操作 | R1 仍可单会话；多编辑器到 R2 |
| `runtime_observe` | L4 | **无** | **新 core**（落盘 + 摘要） |
| `runtime_snapshot_query` | L4 | **无** | **新 core**（从文件切片） |
| `runtime_state` | L3/L4 | **无** | 新 core |

`runtime_screenshot` **不进 core**。它是高频具名里的可选视觉工具。

### 8.2 高频具名

已有且保留：`collection_open` / `collection_save`、`gameobject_create`、`component_add`、`script_create` / `script_attach` / `script_patch`、`project_build`（语义改为优先 check）、`logs_read`、`editor_preview`、`batch_execute`。

新增：`project_run`、`project_stop`、`runtime_get_hierarchy`、`runtime_get_properties`、`runtime_snapshot_query`。可选具名：`runtime_screenshot`。

`runtime_get_*` 默认读 `latest` 快照文件，不是每次打 live HTTP。

`project_run mode=live` **不要求编辑器开着**。编辑器关着时由 CLI 拉起并管死 dmengine（见 §9、§15）。编辑器开着时仍优先认本 CLI 拉起的进程，避免和编辑器 Play 打成两套。

`project_build` 今天文档和实现容易混「编译」和「启动」。需求：

- `project_check`：只编译（bob 或 `POST /command/check`）。
- `project_build` 作为别名可以暂时指向 check，但回包必须说有没有启动游戏。
- 要跑游戏走 `project_run`。

### 8.3 领域收口

v0 已有的 manage 保留。R2 再加：`gui_manage`、`atlas_manage`、`tilemap_manage`、`particlefx_manage`、`material_manage`、`input_binding_manage`、`camera_manage`、`render_manage`。

### 8.4 工具质量（健壮性，和功能同等优先级）

现在 `agent_mcp.py` 的 `inputSchema` 是 `additionalProperties: true`。这不叫完整 MCP。

| 要求 | 说明 |
| --- | --- |
| 每个 tool 有真实 schema | 必填、类型、枚举 `op`、默认值 |
| 统一信封 | `{status, readiness, data\|error}`；runtime 再加 `source` / `mode` |
| 分页 | 树、日志、search 必须有 `offset`/`limit` 和 `truncated` |
| 超时 | 探活短超时；batch run 用 `tool_timeout_sec`；live observe 不阻塞到游戏自己退出 |
| 路径 | 工程内路径以 `/` 开头；快照 JSON、可选 PNG 给绝对路径 |
| `batch_execute` | R1 做成**同一事务**失败整笔回滚。现在逐步 undo 必须在文档和回包里写明，不能假装原子 |

---

## 9. 生命周期与 readiness

在现有 `ready` / `loading` / `building` / `no_collection` / `no_editor` 上增加运行态：

| readiness | 含义 | 允许 |
| --- | --- | --- |
| `ready` | 可改作者态 | L1 写入、L2 check |
| `running` | 有活 target | L4 观察；L1 仍可改，但 Agent 应知道要重新 run 才看得到 |
| `observing` | 正在拉 scene_graph（或可选截屏） | 只读；其它写入排队或拒绝 |
| `no_runtime` | 没有活 target | 禁止 live 采集。`runtime_snapshot_query` / 读已有文件仍允许 |

`building` 时禁止作者态写入（status / check 除外），与现规格一致。

CLI 拉起的 live 进程由 CLI 管死：`project_stop`、进程退出、工程切换。不要留下无主 dmengine。每个工程同时只允许一个 CLI 管的 live 进程；再 `project_run live` 必须先停掉旧的，或回 `NOT_ALLOWED`。

---

## 10. 错误码（在现表上追加）

| code | 何时 |
| --- | --- |
| `ENGINE_UNREACHABLE` | 找不到 dmengine 可执行文件 |
| `ENGINE_NOT_RUNNING` | 要 live 观察但进程已退，且没有 batch dump |
| `RUNTIME_DUMP_MISSING` | batch 结束但快照 **JSON** 没写成。缺 PNG 不报这个码 |
| `SNAPSHOT_NOT_FOUND` | `snapshot` id/路径不存在或不是本工程快照 |
| `INLINE_TOO_LARGE` | `inline=full` 或 `get_path` 超过 48 KB；文件已在磁盘上 |
| `RUNTIME_TRUNCATED` | 不当错误；成功回包用 `truncated`。`truncate=false` 且超上限时才报错 |
| `UNKNOWN_TARGET` | `session_activate` / url 对不上当前工程 |

原有 `MISSING_PARAM` / `NOT_FOUND` / `EDITOR_UNREACHABLE` 等继续用。运行时找不到 id 用 `NOT_FOUND`，hint 指向 `runtime_get_hierarchy`。

---

## 11. 分阶段

### R0 — 批观察闭环（先做这个）

没有活进程也能「跑完一轮并看见结构」。

- CLI：`run` / `loop` 写快照目录；`observe` 默认摘要。
- 引擎：`--runtime-dump=path.json`（退出时写完整 scene_graph）。
- MCP：`runtime_observe`（batch，`inline=summary`）+ **`runtime_snapshot_query`**（`get_node` / `list_ids` / `find`）。
- `runtime_get_hierarchy` / `runtime_get_properties` 读 `latest` 文件。
- 发现：写/读 `.internal/agent/engine.json`。
- 验收：空工程方块。observe 回包**没有**整棵树、**默认没有** PNG；`get_node` 能读出方块 `world_position`。整文件不得靠 `read_text` 进对话。截屏不参与这条验收。

### R1 — 活观察 + 工具质量

- `project_run mode=live` / `project_stop`（**编辑器可关**，CLI 管进程）。
- live dump 走 **`--agent-control` 文件握手**（`dump.request` / `dump.ready`）。**禁止**用引擎 HTTP 或 HTTP MCP 做观察。
- `runtime_state`、live 采集后同样落盘再查。`inline` 三档；查询 op 含 `get_path`。
- `runtime_get_hierarchy` 默认 `limit=200` 且默认读文件。
- `editor_state.engine`。
- 工具 schema、观察硬上限、`batch_execute` 原子化（或回包诚实标注）。
- `logs_read` 统一引擎日志。
- **不做 `runtime_diff`**（留给 R2）。
- 验收：编辑器关着也能 live run；问「玩家现在在哪」= 再 observe 一份新文件 + `get_node`，不必重启、**不必截屏**；两次快照 id 不同，数值会变。

### R2 — 对照与领域

- `runtime_diff`。
- MCP resources（`defold://runtime/snapshot/{id}` 指向文件，读仍建议走 query 工具）。
- atlas / tilemap / gui / input 的 manage。
- 多 target 列表（仍默认一个当前）。

### R3 — 干预（默认不做，单独立项）

只有 L4 稳定、且有明确游戏用例再开：

- 按帧灌输入（合成 HID 或 DDF，需安全预算）。
- `game_eval`（默认关，与 `/eval` 同级）。
- 交互式 debugger（mobdebug）。Agent 主环仍然禁止停在 `debug>`。

R3 不进「完整 MCP」的完成定义。完整 = R1 验收通过。

---

## 12. 明确不做

- 游戏工程里的观察 helper、autoload、TCP 后门。
- 引擎内实现 MCP 协议。
- 把 `/eval` 或 mobdebug 注册成默认 tool。
- 客户端配置裸 HTTP MCP URL。
- 用桌面截窗口代替引擎截屏（分辨率/焦点不稳定）。
- 把 WebSocket 塞进引擎，除非测出 loopback HTTP 是瓶颈。
- 为了对标 godot-ai 而恢复 attach / FastMCP / 工程插件。

---

## 13. 实现落点（需求级，不是目录幻想）

| 职责 | 落点 |
| --- | --- |
| 工具名、stdio、schema | `scripts/agent/agent_mcp.py`、`defold_agent.py` |
| 发现、裁剪、合成信封、bob/dmengine | `scripts/agent/agent_ops.py` 及相邻模块 |
| 作者态图变异 | `editor/src/clj/editor/agent.clj` |
| 编辑器转发 live target（可选） | 同一 `/agent/command`，例如 `runtime_*` 在编辑器开着时去打已选 target |
| 退出 dump、可选 live 截屏、可选过滤 | `engine/engine/src/engine.cpp`、`engine_service.cpp` |
| Agent 用法 | `scripts/agent/AGENTS.md` 增加 observe 步骤 |

测试最低集：

- 单元：摘要不含整树；`get_node` / `find` / 分页；`inline=full` 超 48 KB 报 `INLINE_TOO_LARGE` 且文件仍在；作者态与运行态 `source`。
- 集成：小 collection 跑 N 帧，快照文件里有嵌入 go 的 id 和 `world_position`；observe 回包 JSON 远小于文件。
- 契约：`tools/list` 含 `runtime_observe`、`runtime_snapshot_query`，schema 写清 `inline` / `op`。
- 回归：编辑器关着，现有 `check` / `hierarchy` / `script_patch` 磁盘回退仍可用。

---

## 14. 风险

| 风险 | 处理 |
| --- | --- |
| `/scene_graph` 在大场景上又慢又大 | 完整树只落盘；MCP 默认摘要；切片走 query。R1 可加 `?id=` 加快采集 |
| `DM_SERVICE_PORT=dynamic` 端口难猜 | 以日志和 `engine.json` 为准，禁止猜 8001 |
| release 引擎没有 service | 观察只保证 debug / 开发引擎；doctor 要说清楚 |
| 编辑器 Play 与 CLI 各拉一个引擎 | `runtime_state` 列出；默认本 CLI 拉起的；不要静默打错进程 |
| 把截屏写成默认循环 | AGENTS.md 写死：主环是 observe + query。有 PNG 再打开；没有就不要截 |
| 把 L5 提前做 | 不灌输入、不 eval，直到 R1 验收过 |

---

## 15. 已决（原未决四条，不再重开）

| # | 决定 | 理由 |
| --- | --- | --- |
| 1 | live 观察 = **`--agent-control` 文件握手**。不实现 HTTP MCP，不用 `GET /scene_graph` / `GET /screenshot` 当 Agent 协议 | HTTP MCP 慢且不稳。stdio MCP + 落盘查询才是主路径。引擎 HTTP 分析器可以留着给人用，不注册成 tool。 |
| 2 | **`runtime_observe` 默认 `inline=summary`**（无节点列表）。`preview` 最多 80 浅节点。**`runtime_get_hierarchy` 默认 `limit=200`** | 主环信封是快照句柄 + 摘要 + issues，不是图。preview 给空工程扫一眼。单独查树与作者态对齐用 200。 |
| 3 | **编辑器关着允许 `project_run mode=live`** | 成功标准第 4 条：编辑器可关。关着就不能保活，则「现在玩家在哪」仍要重启，R1 白做。进程由 CLI 管：`project_stop`、退出、换工程、每工程一个 live。编辑器后来自己 Play 了，`runtime_state` 列出，默认仍打 CLI 拉起的那个。 |
| 4 | **`runtime_diff` 进 R2，不进 R1** | 完整 MCP = R1。R1 已有两边 get + `source` 字段，Agent 能手比。diff 是省事工具，放进 R1 会拖住「做完」的定义。R2 标题就是对照与领域。 |
| 5 | **完整快照落盘；MCP 默认摘要；用 `runtime_snapshot_query` 取精确部分。** `inline=full` 仅 ≤ 48 KB | 整棵 `/scene_graph` 进对话会撑爆上下文。文件是那一帧的真相；查询按 id/子树/JSON Pointer 切片。`full` 给真小场景一条路，超限必须失败并指向文件，禁止默默截断后假装完整。 |
| 6 | **截屏是可选视觉能力，不是观察主环，不进 core，observe 默认不截** | 主观察是 scene_graph 文件 + 查询 + 日志。把截屏当默认循环会让 Agent 用像素猜结构，并把 PNG 送进上下文。问位置/树/属性/报错走 query；只有问「看起来对不对」才 `runtime_screenshot`。 |

---

## 16. 建议的实现顺序

1. 引擎 `--runtime-dump`（退出写 scene_graph；不绑在 `--screenshot` 上）。
2. CLI 把 dump 收成 `.internal/agent/snapshots/{id}.json`，observe 默认摘要、默认不截屏。
3. `runtime_snapshot_query`（`get_node` / `list_ids` / `find` / `get_subtree`），写测试。
4. 挂上 MCP schema。`AGENTS.md`：check → observe → query 文件 → 再改。禁止 `read_text` 快照。截屏不写进默认循环。
5. 再做 live：保活、`--agent-control` 落盘、`runtime_state`、`get_path`、`inline=full` 预算。不做 HTTP MCP。
6. 最后才碰领域 manage 和干预。

R0 之前不要开 atlas/tilemap 工具面。观察层比再加三个 manage 更能改变 Agent 会不会做对游戏。
