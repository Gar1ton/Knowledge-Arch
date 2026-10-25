# CHANGELOG

> 精简版，只列正式发布的版本。逐条细节见 `developer` 分支的 CHANGELOG.md。

## [v2.0.0-rc.3]

### 改进 (Changed)

- Linux AppImage 自动更新
- 默认深色界面

### 新增 (Added) — P16 首次使用引导

- `web/frontend/components/desktop/FirstRunTour.tsx`：不用 AI 助手时的游戏式引导（遮罩锁定其它区域、高亮目标、提示卡与进度点），六步按真实状态自动推进到「收到第一条回复」
- `lib/tour.ts`（本机状态与事件）

### 新增 (Added) — P15 首次启动 AI 辅助配置

- 欢迎页：`web/frontend/components/desktop/WelcomeScreen.tsx` 改为「选择语言 → 导入旧数据 / 从空库开始 → 用 AI 助手完成配置」
- AI 助手接入
- 权限与机密
- prompt 与一键安装
- 路径不外泄

### 改进 (Changed) — skill 适配桌面版

- `.agents/skills/operate-knowledge-arch`：客户端改为读连接文件 + Bearer token（去掉 WebUI 登录、密码与 keyring，删除 `requirements-codex-skill.txt`）
- 打包：`desktop/scripts/bundle_python.py` 把 skill 放进 `ka/agent_skill/`，补登 `web/agent_routes.py`

### 测试 (Tests)

- `tests/backend/test_agent_access.py`
- `tests/backend/test_knowledge_arch_skill_setup.py`
- `tests/backend/test_ka_server.py`
- `tests/backend/test_knowledge_arch_skill_client.py` 改为连接文件 + Bearer

### 新增 (Added) — P14 发布分支 main 自动生成

- `.github/scripts/build_main.py`：由 developer 的发布提交生成干净的 main 提交（临时 index，不碰工作区）：按 `.release-exclude` 删去治理文档、内部设计稿、`dev/`、`tools/` 与仅 developer 使用的 CI，CHANGELOG 换成精简版（只留正式版本与每条首句），相对链接指向被删文件即失败，`git commit-tree` 线性接在现有 main 之后
- `.github/workflows/desktop-release.yml`：新增 publish-main 作业（只接受 developer 上的标签，重跑同一标签不重复追加），build 作业从该 main 提交出包
- `.gitattributes`：与 `.release-exclude` 同步的 `export-ignore`，tag 源码包同样干净（测试核对两者一致）
- README 拆为 `README.md`（中文）/ `README_EN.md`（英文）：徽章互相跳转，注明项目承接自旧插件 v1.2.0，新增「参与贡献」（issue / PR 走 developer）
- `CLAUDE.md` §1.1、`docs/GIT_WORKFLOW.md`、`docs/RELEASE.md`

### 改进 (Changed) — P13 安装包体积与 Linux AppImage

- `desktop/scripts/bundle_python.py`：新增 `slim_interpreter`（POSIX 只留一个实体 `bin/python3`，Tauri 复制资源会把符号链接展开成三份解释器
- macOS DMG 改为 LZMA（ULMO）
- Linux AppImage 后注入 Python

### 测试 (Tests)

- `tests/backend/test_build_main.py`
- `tests/backend/test_bundle_python.py`

### 改进 (Changed) — P12 全英文输出

- 后端日志与界面消息改英文
- 中文回答适配保留
- 桌面壳：`desktop/src-tauri/src/lib.rs`（菜单 / 托盘）、`sidecar.rs`（崩溃与启动失败提示）、`updater.rs`（更新通知）改英文
- 前端：`components/layout/TopBar.tsx` 字标改用 Bitcount
- `kacore/api_capabilities.py`

### 新增 (Added) — P11 线上 Rerank

- `kacore/repository/reranker/remote.py`：`RemoteReranker` 调用 OpenAI 兼容连接上的 `/rerank`（Cohere / Jina / SiliconFlow / Voyage 响应格式）
- 设置：`kacore/settings/schema.py` 新增 `flow.retrieval.rerank.remote`
- 前端：`components/settings/SettingInput.tsx` 抽出 `ConnectionModelInput`（Embedding / Rerank 共用）

### 改进 (Changed)

- Embedding 与 Rerank 默认线上 API

### 改进 (Changed) — P10 安装包瘦身

- `desktop/scripts/bundle_python.py`：新增 `prune_runtime`（去字节码、site-packages 的 `tests/` 与 `include/`、pyarrow 的 Flight / S3 / C++ 自测库），不再 `compileall`，构建期解释器带 `-B`
- `ka_server/bytecode_cache.py`：打包产物里把 `sys.pycache_prefix` 指向用户缓存目录（`ka_server/__init__.py`最先调用），应用目录只读也只需首次启动编译
- Windows 安装包改为 NSIS `setup.exe`：`tauri.conf.json` 的 `bundle.windows.nsis`（当前用户安装、中英文界面）
- 开发环境对齐安装包
- `.github/workflows/desktop-release.yml`

### 新增 (Added) — P9 组件安装失败归类

- `kacore/extras/failure.py`：把 uv 输出归为磁盘不足 / 权限 / 网络 / 无适用安装包 / 依赖冲突 / 编译失败 / 未知
- 前端：`lib/settingsApi.ts`（`InstallFailureReason`、`installFailureText`）、`lib/api.ts`（`installResultOf`：后端 500 的结构化失败结果按结果返回）、`components/prefs/PrefsGroups.tsx`（组件行下方显示说明与原始报错）、`components/panels/FlowPageContent.tsx`（数据流页安装失败提示本地化）

### 修复 (Fixed)

- README 开发命令补 `--dev-origin http://localhost:3000`

### 改进 (Changed) — P8 测试反馈

- 向量库只提供 Milvus Lite
- Embedding 线上 API 入口
- 顶栏：`components/ds/Button.tsx` 的 tab 选中态去掉光圈

### 改进 (Changed) — P7 界面打磨

- 默认界面语言改为英语
- 英文默认字体 PT Sans
- 落地页沿用 v1 登录页视觉

### 新增 (Added)

- 界面缩放：设置项 `prefs.appearance.ui_scale`（0.8–1.5）

## [v2.0.0] — 2026-10-25

### 新增 (Added) — P6 发布

- `.github/workflows/desktop-release.yml`：macOS（Apple Silicon / Intel）、Windows（`.msi`）、Linux（`.deb` / `.AppImage`）矩阵出包
- 自动更新：`desktop/src-tauri/src/updater.rs`（Cargo feature `updater`，tauri-plugin-updater）提供`check_update` / `install_update` 命令，`prefs.general.check_updates` 开启时启动 10 秒后检查，有新版本发`update://available` 事件并弹系统通知
- `desktop/scripts/configure_release.py`：CI 中按 `TAURI_UPDATER_PUBKEY`、`WINDOWS_CERTIFICATE_THUMBPRINT`补全 `tauri.conf.json`（updater 公钥与端点、`createUpdaterArtifacts`、Windows 签名）并输出构建 feature
- macOS 公证准备
- `docs/RELEASE.md`：产物矩阵、所需 secret / variable 清单、发版步骤

### 改进 (Changed)

- 版本统一为 2.0.0
- README 按实际功能重写
- `.github/workflows/tests.yml`

### 测试 (Tests)

- `tests/backend/test_release_config.py`
