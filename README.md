<div align="center">

<img src="./logo.svg" width="96" alt="Knowledge Arch Logo" />

# Knowledge Arch

**本地优先的学术知识库桌面应用（Windows / macOS / Linux）**

*PDF 原件库 · Zotero 同步 · 六档检索 · Harvard 引用 · LightRAG 图谱 · Notion / R2*

</div>

---

> **状态：v2.0.0 开发中。** 本仓库正把 Knowledge Arch 从聊天机器人插件改造成独立桌面应用，
> 路线图见 [`TODO.md`](./TODO.md)。安装包、首次启动向导与完整使用手册在 P5–P6 落地。

## 这是什么

Knowledge Arch 以 PDF 等原件为中心管理资料：上传与清洗、集合与标签、Zotero 镜像、
多档检索问答（答案自带由代码生成的 Harvard 引用）、LightRAG 知识图谱，以及 Notion / R2
同步备份。安装一个桌面应用即可使用，数据全部保存在本机。

- **原件优先**：保留 PDF 原件、逐页抽取与清洗后的 clean markdown，脚注单独保留并可回读。
- **六档检索模式**：从快速查证、整篇精读到多轮深度推演，按问题复杂度选择成本档位。
- **Ask 页即对话入口**：在应用内提问，点击短引即可跳到来源片段。
- **Zotero 同步**：本地 Zotero 与 Zotero Web API 双模式，collection 树镜像为集合树。
- **LightRAG 图谱**：按集合构建 workspace，支持纯图谱与「证据 + 图谱上下文」混合检索。
- **同步与备份**：Cloudflare R2 保存可跨设备恢复的完整快照；Notion 镜像文章元数据与问答。
- **索引自动维护**：新文档入库、换 embedding 后，Milvus 向量索引按需自动重建。

### 六档检索模式

| 模式 | 适用场景 | 成本 | 范围要求 |
|------|----------|------|----------|
| `default` | 查存 / 单点事实，最快（保留 6 条证据） | ★ | 可全局 |
| `enhanced` | 分析、对比、机制类问题：一次拆解 + 宽召回 + 自检纠偏 | ★★ | 可全局 |
| `deep_thinking` | 综述、系统梳理级任务：多轮迭代推演 + 校验 | ★★★★ | 需绑定集合 |
| `graph_mixed` | 语义/词法证据 + LightRAG 图谱上下文混合 | ★★★ | 需绑定集合 |
| `graph_only` | 纯图谱检索，只用实体关系上下文 | ★★ | 需绑定集合 |
| `fulltext` | 单篇精读：整篇正文直接送入 LLM，不截断 | 随篇幅 | 需选定一篇文档 |

## 开发

```bash
uv venv .venv --python 3.12 && uv pip install --python .venv/bin/python -r requirements-dev.txt
.venv/bin/python -m pytest

# 本地后端：token 只经环境变量传入，端口 0 = 系统分配
KA_TOKEN=dev-token .venv/bin/python -m ka_server serve --port 26618 --data-dir .dev_data

# 前端
cd web/frontend && npm ci && npm run dev
```

架构与约定见 [`ARCHITECTURE.md`](./ARCHITECTURE.md)、[`CONVENTIONS.md`](./CONVENTIONS.md)、
[`docs/PROJECT_STRUCTURE.md`](./docs/PROJECT_STRUCTURE.md)。

## 许可证

本项目以 [AGPL-3.0](./LICENSE) 授权。若你在网络服务中分发或提供本项目的修改版本，须同样以 AGPL-3.0
公开对应的完整源码。

## 致谢

- [LightRAG](https://github.com/HKUDS/LightRAG)：图谱增强检索能力。
- [Milvus Lite](https://github.com/milvus-io/milvus-lite)：本地向量库。
- Zotero、Notion、Cloudflare R2：资料管理与同步备份生态。
