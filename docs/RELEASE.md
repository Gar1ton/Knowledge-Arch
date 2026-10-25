# 发布流程（桌面安装包）

> 本文说明 `.github/workflows/desktop-release.yml` 如何出三平台安装包、需要哪些仓库 secret，
> 以及一次正式发布的人工步骤。**流水线和仓库里都不会生成或保存任何证书、密钥**：
> 缺哪一项就跳过对应的签名 / 公证 / updater 步骤，照常产出未签名的安装包。

## 1. 产物矩阵

| Runner | 目标 | 产物 |
|--------|------|------|
| `macos-15` | `aarch64-apple-darwin` | `.app`、`.dmg` |
| `macos-15-intel` | `x86_64-apple-darwin` | `.app`、`.dmg` |
| `windows-latest` | `x86_64-pc-windows-msvc` | NSIS `setup.exe`（当前用户安装） |
| `ubuntu-22.04` | `x86_64-unknown-linux-gnu` | `.deb`、`.AppImage`（后注入 Python） |

每个平台都在本机 runner 上执行：前端静态导出 → `desktop/scripts/bundle_python.py`（内置 CPython 3.12
与依赖，平台专属）→（macOS）逐个签名内置 Mach-O → `tauri build`。推送标签时，先由 publish-main 作业生成干净的
`main` 提交（见 `docs/GIT_WORKFLOW.md` §1），四个平台都从该提交出包。

出包后的平台专属处理：

- **macOS**：tauri 的 DMG 是 zlib 压缩，`hdiutil convert -format ULMO` 改为 LZMA 后替换 Release 上的 DMG
  （rc.2 的 aarch64 DMG 为 139 MB；同一 `.app` 用 xz 估算约 80 MB，以 CI 实测为准）。updater 用的 `.app.tar.gz` 格式固定为 gzip。
- **Linux**：linuxdeploy 解析不了 wheel 里 auditwheel 改名的 `.so`，内置 Python 在 AppDir 里时必然失败。
  AppImage 那一遍用 `desktop/src-tauri/tauri.appimage.conf.json` 去掉 python 资源，之后由
  `desktop/scripts/appimage_inject_python.sh` 解包、注入、冒烟、用 appimagetool（zstd 19 级）重打包；
  启用 updater 时重签 `.AppImage.sig`。tauri-action 那一遍只出 `.deb`，它生成的 `latest.json` 没有
  AppImage 条目：所有平台出包后，`updater-manifest` 作业用 `desktop/scripts/updater_add_appimage.py` 写入
  `linux-x86_64-appimage`（通用的 `linux-x86_64` 只在缺失时补上，不覆盖 deb 条目）。
- workflow artifact 不上传 AppImage 的中间目录 `*.AppDir`。

触发方式：

- **手动**（Actions › Desktop build › Run workflow）：只构建并把产物作为 workflow artifact 上传（保留 14 天）。
- **推送 `v*` 标签**：另外创建**草稿** Release 并上传安装包；启用 updater 时附带 `latest.json`。
  草稿需要人工检查后再发布。

## 2. 需要的仓库 secret / variable

在 GitHub 仓库 Settings › Secrets and variables › Actions 中配置。全部可选；不配置时对应能力关闭。

### macOS 签名与公证

| 名称 | 类型 | 内容 |
|------|------|------|
| `APPLE_CERTIFICATE` | secret | 「Developer ID Application」证书导出的 `.p12`，base64 编码 |
| `APPLE_CERTIFICATE_PASSWORD` | secret | 导出 `.p12` 时设的密码 |
| `APPLE_SIGNING_IDENTITY` | secret | 证书名，如 `Developer ID Application: Name (TEAMID)` |
| `APPLE_ID` | secret | 用于公证的 Apple ID 邮箱 |
| `APPLE_PASSWORD` | secret | 该 Apple ID 的 App 专用密码（appleid.apple.com 生成） |
| `APPLE_TEAM_ID` | secret | 开发者团队 ID |

只有证书三项时：签名但不公证（用户首次打开需在「隐私与安全性」放行）。再加 Apple ID 三项时：
tauri 自动提交公证并钉上票据。内置 Python 的每个 Mach-O 由 `desktop/scripts/macos_sign_resources.sh`
以 hardened runtime 签名，权限见 `desktop/src-tauri/entitlements.plist`。

### Windows 签名

| 名称 | 类型 | 内容 |
|------|------|------|
| `WINDOWS_CERTIFICATE` | secret | 代码签名证书 `.pfx`，base64 编码 |
| `WINDOWS_CERTIFICATE_PASSWORD` | secret | `.pfx` 密码 |

流水线把证书导入 runner 的用户证书库，取得指纹后由 `desktop/scripts/configure_release.py` 写进
`bundle.windows`（SHA-256 + DigiCert 时间戳）。未配置时 `setup.exe` 不签名（SmartScreen 会提示）。

### 自动更新（Tauri updater）

| 名称 | 类型 | 内容 |
|------|------|------|
| `TAURI_UPDATER_PUBKEY` | **variable** | updater 公钥（`tauri signer generate` 输出的 `.pub` 内容） |
| `TAURI_SIGNING_PRIVATE_KEY` | secret | 对应私钥 |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | secret | 私钥密码（无密码可不设） |

配置了公钥时，`configure_release.py` 在 CI 的工作副本里：写入 `plugins.updater`（公钥 + 端点
`https://github.com/<repo>/releases/latest/download/latest.json`，可用变量 `KA_UPDATER_ENDPOINT` 覆盖）、
打开 `bundle.createUpdaterArtifacts`，并以 Cargo feature `updater` 构建。应用启动 10 秒后检查更新
（偏好设置 › 通用 › 自动检查更新），有新版本时在顶部横幅提示「安装并重启」。

**密钥对必须由维护者自己生成并保管**；私钥一旦丢失，已安装的版本就再也无法验证后续更新。
没有公钥的构建里，「检查更新」入口显示「此版本未启用自动更新」。

## 3. 发版步骤

1. 同步版本号：`ka_server/__init__.py` 的 `APP_VERSION`、
   `desktop/src-tauri/tauri.conf.json`、`desktop/src-tauri/Cargo.toml`、`desktop/package.json`、
   `web/frontend/package.json`。`tests/backend/test_release_config.py` 会检查它们一致。
2. 在 `CHANGELOG.md` 顶部写好该版本条目（`## [vX.Y.Z] — 日期`；没写时 main 的精简版把 `[Unreleased]` 记为该标签），
   确认 developer 的 Developer CI 全绿，并按 `docs/GIT_WORKFLOW.md` §3 预演 main。
3. 先手动触发一次 Desktop build，下载四个平台的产物做冒烟（安装、首次启动、欢迎页、退出后无残留进程）。
4. **经维护者批准后**在 developer 的提交上推送标签（如 `v2.0.0`）。流水线先更新 `main`，再创建草稿 Release。
5. 检查草稿里的安装包与 `latest.json`（启用 updater 时），补写发布说明后点 Publish。

> 推送标签、发布 Release 都是远端操作，按 `CLAUDE.md` §1.1 须事先单独取得批准。

## 4. 本地出包

见 `desktop/README.md`「本地出包」。本地构建默认不签名、不启用 updater。
