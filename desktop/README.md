# desktop/ — Knowledge Arch 桌面壳（Tauri 2）

壳只管进程与窗口：单实例、菜单 / 托盘、原生对话框、系统通知、壳偏好，以及后端 sidecar 的启停。
业务全部在 Python 后端（`ka_server` → `kacore`），界面是 `web/frontend` 的静态导出。

```
desktop/
  scripts/bundle_python.py   # 打包后端：CPython 3.12 + 依赖 + 源码 + uv → src-tauri/resources/
  src-tauri/
    tauri.conf.json          # 窗口、CSP、打包资源（python/、ka/）
    src/lib.rs               # 菜单、托盘、单实例、命令（backend_info / pick_folder / notify …）
    src/sidecar.rs           # 后端进程：token、KA_READY、崩溃检测、有序停止
    src/prefs.rs             # 壳偏好 shell.json 与平台目录
    icons/                   # 由 ../logo.svg 生成（npm run icons）
```

## 后端 sidecar

1. 每次启动生成 256 位随机 token，只经环境变量 `KA_TOKEN` 交给后端；
2. 启动 `python -I -m ka_server serve --port 0 --parent-pid <壳 pid> --uv <内置 uv> [--data-dir <覆盖>]`；
3. 读 stdout 的 `KA_READY port=N`，前端经 `invoke("backend_info")` 取 `{base_url, token}`；
   60 秒内未就绪或运行中退出 → `backend://exited` 事件 → 崩溃页（最后几行日志、重启服务、打开日志目录）；
4. 退出应用时 `POST /api/shutdown`，10 秒内没退出再强制结束；壳被强杀时后端靠 `--parent-pid` 自行退出。

`-I` 会忽略 PYTHONPATH，所以打包脚本在内置解释器的 site-packages 里写 `knowledge_arch.pth`
（相对路径指向 `ka/`），后端才可导入。数据目录覆盖（偏好设置 › 存储）保存在壳自己的
`<系统配置目录>/Knowledge Arch/shell.json`，通过 `--data-dir` 传给后端。

## 本地出包

前置：Rust（stable）、Node.js 22、uv；Linux 另需 `libwebkit2gtk-4.1-dev libgtk-3-dev
libayatana-appindicator3-dev librsvg2-dev patchelf`。

```bash
cd web/frontend && npm ci && npm run build && cd -     # 1. 界面静态导出
python desktop/scripts/bundle_python.py                # 2. 后端资源（约 550 MB，未压缩）
cd desktop && npm ci && npx tauri build                # 3. 出包
```

产物在 `desktop/src-tauri/target/release/bundle/`：macOS `.app` / `.dmg`，Windows NSIS `setup.exe`，
Linux `.deb` / `.AppImage`。每个平台必须在该平台本机（或对应的 CI runner）上打包——内置的
CPython 与依赖都是平台专属的二进制。

开发期可以 `npx tauri dev`：界面来自 `next dev`（26619 端口，需先在 web/frontend 里启动），
壳会给后端加上 `--dev-origin http://localhost:26619`。环境变量 `KA_SIDECAR_PYTHON` 可指定别的解释器。

## 体积

内置运行时约 550 MB（未压缩），大头是核心依赖：milvus-lite 带来的 pyarrow / faiss、pymilvus 带来的
pandas、PyMuPDF。torch / sentence-transformers、lightrag、boto3 不进安装包，由用户在
「偏好设置 › 组件」按需安装到数据目录。

打包脚本的裁剪（`prune_runtime`）：
- 不带字节码：运行时由 `ka_server/bytecode_cache.py` 把 `sys.pycache_prefix` 指到用户缓存目录
  （应用目录只读），首次启动编译一次；
- 删去 site-packages 里的 `tests/` 与 `include/`；
- 删去 pyarrow 的 Flight / S3 扩展与 C++ 自测库（`libarrow_substrait` 被核心库直接链接，必须保留）；
  打包冒烟测试会真实跑一遍 Milvus Lite 建库-写入-检索。
