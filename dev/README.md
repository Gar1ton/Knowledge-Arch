# dev/ — 本地开发运行器

> 放**本地调试/联调脚本**，与生产运行解耦，不进入安装包。

## 本地联调

```bash
# 后端：数据放在仓库内被 git 忽略的 .dev_data/，token 只经环境变量传入；
# 浏览器经 next dev 代理访问时带 Origin，需要把开发页地址加进 CORS 白名单。
KA_TOKEN=dev-token KA_DEV_ORIGINS=http://localhost:26619,http://127.0.0.1:26619 \
  python -m ka_server serve --port 26618 --data-dir .dev_data

# 前端：next dev 把 /api/* 代理到后端（见 web/frontend/next.config.ts，可用 KA_API_HOST /
# KA_API_PORT 改目标）；NEXT_PUBLIC_KA_TOKEN 与后端的 KA_TOKEN 相同。
cd web/frontend && NEXT_PUBLIC_KA_TOKEN=dev-token npm run dev -- -p 26619
```

浏览器开发模式没有桌面壳：原生对话框、托盘、系统通知、数据目录切换不可用（界面会提示），
欢迎页的导入路径改为手动输入；其余功能与桌面窗口一致。

## 约定

- dev 脚本**可 import `kacore`**（把根加入 `sys.path`）以复用真实业务逻辑。
- 本地敏感/机器相关配置走 `run_config.py`（由 `run_config.example.py` 复制而来），并在 `.gitignore` 忽略真实文件。
- 重置/清库类脚本默认安全、需显式确认参数。
