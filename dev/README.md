# dev/ — 本地开发运行器

> 放**本地调试/联调脚本**，与生产运行解耦，不进入安装包。

## 本地联调

```bash
# 后端：数据放在仓库内被 git 忽略的 .dev_data/，token 只经环境变量传入
KA_TOKEN=dev-token python -m ka_server serve --port 26618 --data-dir .dev_data

# 前端：next dev 把 /api/* 代理到后端（见 web/frontend/next.config.ts）
cd web/frontend && npm run dev
```

## 约定

- dev 脚本**可 import `kacore`**（把根加入 `sys.path`）以复用真实业务逻辑。
- 本地敏感/机器相关配置走 `run_config.py`（由 `run_config.example.py` 复制而来），并在 `.gitignore` 忽略真实文件。
- 重置/清库类脚本默认安全、需显式确认参数。
