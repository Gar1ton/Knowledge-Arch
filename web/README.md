# web/ — HTTP 路由 + 前端源码

## 职责

后端只做**传输适配**：解析请求 → 委派给 `kacore/api` 纯函数 → 包装响应。
**业务逻辑不在 web，在 `kacore`。**

```
web/
  server.py          # aiohttp 路由（build_app）：token 鉴权 + CORS + 路由 → kacore/api
  desktop_routes.py  # 桌面版新增路由：/api/components*（组件）、/api/legacy-import*（旧数据导入）
  frontend/          # Next.js 前端源码（静态导出到 frontend/out/，由桌面壳加载）
```

## 安全边界

- 服务由 `ka_server` 起在 127.0.0.1，端口由系统分配。
- 每次启动随机 token，只经环境变量 `KA_TOKEN` 交给后端、经桌面壳交给自己的窗口；
  请求带 `Authorization: Bearer <token>`（下载类 GET 可用 `?access_token=`）。
- CORS 只放行 `tauri://localhost` 与 `http://tauri.localhost`；开发期用 `KA_DEV_ORIGINS` 追加。
- 不托管静态文件，没有登录页。

## 约定

- 路由薄、`kacore/api` 厚：路由函数 = 取参 + 调 api + 序列化。
- 后端不 import `managers` 的内部实现细节，经 `kacore/api` 门面交互。
