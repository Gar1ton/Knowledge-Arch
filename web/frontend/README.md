# web/frontend — Knowledge Arch 界面

Next.js（App Router）静态导出，由桌面壳加载。主窗口有「工作台」（文件 / 文档 / 问答三栏）与
「数据流」两页；偏好设置（⌘, / Ctrl+,）有通用、外观、存储、本地模型、组件、诊断六组。

## Notion

Notion 设置包含本地变更自动同步开关和周期补偿间隔（默认 300 秒）。间隔设为 0
只关闭周期补偿；关闭全部自动推送请关闭自动同步开关。共享进度条显示本轮笔记
检查/标记数、调用尝试数、耗时及模型 token（0）。`npm run build` 产出静态导出
`out/`，由桌面壳打进安装包加载。运行升级和筛选视图见 [Notion 同步指南](../../docs/NOTION_SYNC.md)。

## 开发

界面通过 `lib/backend.ts` 取后端地址与 token：桌面壳里由 `invoke("backend_info")` 提供；
`next dev` 下走同源 `/api/*` 代理，token 取 `NEXT_PUBLIC_KA_TOKEN`。完整步骤见
[`dev/README.md`](../../dev/README.md)。

```bash
NEXT_PUBLIC_KA_TOKEN=dev-token npm run dev -- -p 26619
npm run lint && npx tsc --noEmit && npm run build   # 产物在 out/，由桌面壳打包
```

设置项一律由后端 SettingSpec 驱动（`GET /api/settings/schema`），文案键为
`set_<设置键中的点换成下划线>`，放在 `lib/i18nDesktop.ts`。
