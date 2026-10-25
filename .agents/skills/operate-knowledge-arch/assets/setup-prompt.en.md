Help me set up Knowledge Arch, a local-first desktop app for my academic library (PDFs, Zotero sync, cited Q&A, knowledge graph). It is running on this computer right now.

1. Read the skill instructions in {skill_md}, then follow references/setup.md in the same folder.
2. Run its client with the Python that ships with Knowledge Arch (nothing to install):
   {command} setup-status
3. Guide me through every data-flow node: model connections, model assignment, embedding, retrieval and rerank, and the optional parts (Zotero, knowledge graph, Notion, R2). Ask me one thing at a time; do not guess.

Rules:
- Never ask me to paste API keys or tokens into this chat. When a key is needed, run `request-secret`; Knowledge Arch will show me a field to type it in.
- Show me each change first, and run it with --apply only after I confirm.
- Only contact this local Knowledge Arch and the local services the skill tells you to detect. Do not browse the web or install anything unless I agree.
- In PowerShell, put & before the command.
- Reply to me in English.
