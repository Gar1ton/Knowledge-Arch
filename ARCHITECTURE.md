# ARCHITECTURE — 架构圣经

> 本文件解释**为什么这样分层、代码放哪、依赖指向哪**。任何 agent 在落地代码前必须理解本文件。
> 核心理念一句话：**单向分层 + 组合根集中装配 + 接口先行 + 类型化配置**。

---

## 1. 依赖方向（最重要的一张图）

```
          桌面壳 (Tauri) / 开发脚本 / 测试
                              │  启动进程、HTTP 请求
                              ▼
   ┌──────────────────────────────────────────────────────────┐
   │  shell (ka_server)       进程薄壳：目录/锁/信号/HTTP 生命周期 │  最外层
   ├──────────────────────────────────────────────────────────┤
   │  web (HTTP 路由)         请求 ↔ kacore/api 的翻译，零业务     │
   ├──────────────────────────────────────────────────────────┤
   │  managers / pipelines    编排服务：业务流程、用例编排         │
   ├──────────────────────────────────────────────────────────┤
   │  repository              仓储：持久化抽象（ABC）+ 实现        │
   ├──────────────────────────────────────────────────────────┤
   │  domain                  纯数据模型：零依赖（圆心）           │  最内层
   └──────────────────────────────────────────────────────────┘

   横切支撑（被各层使用，自身不反向依赖业务）：
     adapters（框架↔domain 翻译） · mixins（可复用行为） · utils（工具） · config（类型化配置）
```

**铁律：依赖只能指向内层（向下），永不反向。**

- `domain` 不 import 任何其他层（连框架、连数据库都不知道）。
- `repository` 只依赖 `domain`。
- `managers` 依赖 `repository` 接口 + `domain`，**不直接 import 框架**。
- `web` / `ka_server` 依赖 aiohttp 与运行环境，但把业务全部委派给 `kacore/api` 与 managers。
- 需要框架数据进入业务时，先经 `adapters` 翻译成 `domain` 对象。

> 这条规则是「可视性」的根：看一个文件的 import，就能判断它属于哪层、是否越界。
> 越界 import（如 `domain` 里出现 `import sqlite3` 或框架 API）= 架构腐坏的第一信号。

---

## 2. 薄壳原则（Thin Shell）

进程入口 `ka_server/__main__.py`（`python -I -m ka_server serve`）**只做进程生命周期**：

1. 解析数据目录（`ka_server/paths.py`）、取数据目录独占锁、装配组合根；
2. 在 127.0.0.1 上起 HTTP（`web/server.py` 的 `build_app`），打印 `KA_READY port=…`；
3. 等待退出（`/api/shutdown`、SIGTERM/SIGINT、父进程消失），再反序拆除。

入口里**不写任何业务逻辑**。好处：

- 换运行方式（桌面壳 sidecar / 开发脚本 / 测试子进程）时只换启动方式，业务层一行不动。
- 服务软重启只重建组合根，HTTP 服务与端口不变；业务状态集中在组合根，便于干净重建。

---

## 3. 组合根模式（Composition Root）

**所有对象的创建与装配集中在一个地方**：`kacore/app_initializer.py` 的 `AppInitializer`（组合根）。

- 按**依赖顺序**构造：先建无依赖的（config、repository），再建依赖它们的（managers、pipelines），最后建薄壳引用。
- **构造器注入**：每个组件通过构造参数接收依赖，**自己绝不 new 依赖**、绝不读全局单例。
  - 好处：组件可被单测独立实例化，注入 mock；依赖关系在组合根一目了然。
- **生命周期对称**：用 `AsyncExitStack`（或等价机制）注册资源，`teardown` 时**反序**释放。
  - `initialize()`：构造 → start → 注册定时任务。
  - `teardown()`：cancel 任务 → stop 子系统 → 关闭连接（与构造顺序相反）。

> 判断「装配代码该放哪」：凡是 `X = SomeClass(...)` 把零件接起来的代码，都属于组合根，不属于业务层。

---

## 4. 接口先行（Interface-First）

每一层用**抽象基类（ABC）**定义契约，实现与契约分离：

```
repository/
  base.py      # ABC：定义方法签名 + docstring 契约（唯一真相源）
  sqlite.py    # 生产实现
  memory.py    # 内存实现（测试用，无 I/O）
managers/
  base.py      # ABC：BaseManager → BaseXxxManager（抽象方法 + 契约）
  xxx.py       # 具体编排实现
```

- **生产实现 + 测试实现共用同一接口** → 「接口对换测试」：测试里注入 `memory` 实现，无需真实 DB/网络。
- 契约（同步顺序、主键约定、返回 None 的语义等）写在 `base.py` 的 docstring 里，是该层的**唯一真相源**。
- 改接口 = 改契约：必须同时更新 `base.py` docstring、所有实现、所有调用方、相关测试。

---

## 5. 类型化配置（Typed Config）

原始配置（dict / JSON / 环境变量）**只在一个地方**被解析成结构化对象：

```
原始 dict ──► Config（kacore/config）──► get_xxx_config() ──► XxxConfig (dataclass)
                                                              ▲
                                          各子系统只接收自己的 XxxConfig
```

- 杜绝业务代码里散落的 `cfg.get("some_key", default)`：键名、默认值、类型集中在 `Config`。
- 每个子系统拿到**专属的 dataclass**，可独立单测，IDE 有补全与类型检查。
- 配置项的默认值与界面保持同源（P2 起由 `kacore/settings` 的 SettingSpec 统一登记）。

---

## 6. 持久化与迁移

- 数据库 schema 用**编号迁移**演进：`migrations/NNN_描述.sql`（`001`, `002`, …）。
- 一个**幂等 runner**：每次启动按文件名顺序应用未执行的迁移，用 `_migrations` 跟踪表去重。
- 详见 `migrations/README.md`。

---

## 7. 可选全栈（web）

界面与后端遵循「**业务门面 + 路由委派**」：

- `kacore/api.py`：**框架无关**的纯业务函数（不含 HTTP 概念）。
- `web/server.py`：HTTP 层，把请求参数翻译后**委派给 `kacore/api`**，再把结果包装成响应；
  只监听 127.0.0.1，按每次启动的 token 鉴权，CORS 只放行桌面壳窗口。
- `web/frontend/`：前端源码（Next.js 静态导出）；产物由桌面壳从安装包加载，后端不托管静态文件。

详见 `web/README.md`。

---

## 8. ✅ 新增子系统清单（Agent 落地代码时照此执行）

向项目加入一个新业务能力（如 "notifier"）时，按序执行：

1. **定位层级**：它是编排逻辑（→ `managers/` 或 `pipelines/`）、持久化（→ `repository/`）还是纯模型（→ `domain/`）？
2. **先定接口**：在该层 `base.py` 增加 ABC / 抽象方法，**用 docstring 写清契约**（输入、输出、副作用、错误语义）。
3. **写实现**：生产实现 + （若涉及 I/O）内存/桩实现各一份，满足同一接口。
4. **加配置**：若需配置，在 `kacore/config` 增加 `XxxConfig` dataclass 与 `get_xxx_config()`，并登记写入策略。
5. **在组合根注入**：在 `kacore/app_initializer.py` 按依赖顺序构造它、传入依赖、注册生命周期/定时任务。
6. **接路由**（若需界面触发）：在 `web/server.py` 注册路由并委派给 `kacore/api`，**不在路由里写业务**。
7. **写测试**：用内存实现做接口对换测试，覆盖契约。
8. **更新治理**：在 `TODO.md` 勾对应 Phase；收尾在 `CHANGELOG.md` 追加条目（点名新增文件/模块）。

> 任一步缺失（尤其 2、5、7）都视为未完成——这是「可视性」不被 vibe coding 侵蚀的保证。
