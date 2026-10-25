请帮我完成 Knowledge Arch 的初始配置。它是一个本地优先的学术知识库桌面应用（PDF 管理、Zotero 同步、带引用的问答、知识图谱），现在正在这台电脑上运行。

1. 先阅读 skill 说明 {skill_md}，然后按同目录下 references/setup.md 的流程操作。
2. 用 Knowledge Arch 自带的 Python 运行它的客户端（无需安装任何东西）：
   {command} setup-status
3. 带我逐个配置数据流节点：模型连接、模型分配、向量化、检索与重排，以及可选部分（Zotero、知识图谱、Notion、R2）。一次只问我一件事，不要猜。

规则：
- 不要让我把 API Key 或 token 粘贴到对话里。需要密钥时运行 `request-secret`，Knowledge Arch 会弹出输入框让我填写。
- 每项改动先给我看，我确认后才用 --apply 执行。
- 只访问这台电脑上的 Knowledge Arch 和 skill 要求探测的本机服务；未经我同意，不要上网，也不要安装任何东西。
- 在 PowerShell 里，命令前要加 &。
- 请用中文和我交流。
