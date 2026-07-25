"use client";

import React from "react";
import { Icon } from "@/components/ds/Icon";
import type { RetrievalScopeSnapshot } from "@/lib/api";

export function ChatScopeFooter({ scope }: { scope?: RetrievalScopeSnapshot }) {
  let label = "范围未知（旧记录）";
  if (scope?.kind === "global") label = `全局 · ${scope.collection_names.length} 个 active collections`;
  if (scope?.kind === "collection") {
    label = scope.requested_collection || scope.collection_names[0] || "指定集合";
    if (scope.include_descendants) label += "（含子集合）";
  }
  if (scope?.kind === "document") label = scope.requested_collection || "当前文档";
  if (scope?.kind === "none") label = "未执行知识库召回";
  return (
    <div style={{ marginTop: 8, paddingTop: 7, borderTop: "1px solid var(--border)", display: "flex", gap: 5, alignItems: "center", color: "var(--fg-subtle)", fontSize: 10 }}>
      <Icon name="folder" size={10} /> <span>{label}</span>
    </div>
  );
}
