"use client";
/**
 * 阅读时的左栏：「‹ 集合名」返回浏览，下面两个页签——
 * 「论文」：同一集合的论文（与中栏同一份列表、同一个查找词和排序），点一篇直接换；
 * 「笔记」：原来的笔记面板（元数据、标签、批注、笔记）。
 * 页签记在本机；从列表行上的笔记键进来（只开笔记、不开阅读器）时先显示「笔记」。
 */
import React, { useState } from "react";
import { Icon } from "@/components/ds/Icon";
import { NotePanel } from "@/components/panels/NotePanel";
import { DocList, DocListToolbar } from "@/components/panels/DocList";
import { useConsole } from "@/lib/ConsoleContext";
import { useI18n } from "@/lib/i18n";
import { scopeName, useCollectionDocs } from "@/lib/useCollectionDocs";

type SidebarTab = "papers" | "notes";
const TAB_STORAGE_KEY = "ka-reading-sidebar-tab";

function loadTab(): SidebarTab {
  try {
    return localStorage.getItem(TAB_STORAGE_KEY) === "notes" ? "notes" : "papers";
  } catch {
    return "papers";
  }
}

export function ReadingSidebar({ docId, openNotes }: { docId: string; openNotes: boolean }) {
  const { t } = useI18n();
  const { selectedCollection, setSelectedDocId, setNoteDocId, setHighlightedChunk } = useConsole();
  const model = useCollectionDocs(selectedCollection);

  // 换论文或换进入方式时重新决定页签：只开笔记 → 笔记；否则沿用上次的选择。
  const tabKey = `${docId}|${openNotes}`;
  const [tabState, setTabState] = useState<{ key: string; tab: SidebarTab }>(() => ({
    key: tabKey,
    tab: openNotes ? "notes" : loadTab(),
  }));
  if (tabState.key !== tabKey) {
    setTabState({ key: tabKey, tab: openNotes ? "notes" : tabState.tab });
  }
  const tab = tabState.tab;

  function chooseTab(next: SidebarTab) {
    setTabState({ key: tabKey, tab: next });
    try {
      localStorage.setItem(TAB_STORAGE_KEY, next);
    } catch {
      // 本机存储不可用：本次会话内仍然生效。
    }
  }

  function back() {
    setSelectedDocId(null);
    setNoteDocId(null);
    setHighlightedChunk(null);
  }

  const backLabel = scopeName(selectedCollection) ?? t("file_all_documents");

  return (
    <section
      style={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        minHeight: 0,
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-2xl)",
        boxShadow: "var(--shadow-card)",
        overflow: "hidden",
      }}
    >
      <header style={{ flexShrink: 0, borderBottom: "1px solid var(--border)" }}>
        <button
          type="button"
          onClick={back}
          title={t("sidebar_back")}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            width: "100%",
            height: 34,
            padding: "0 12px",
            border: "none",
            background: "transparent",
            cursor: "pointer",
            color: "var(--accent)",
            fontSize: 12,
            fontWeight: 600,
            fontFamily: "var(--font-sans)",
            textAlign: "left",
          }}
        >
          <Icon name="chevL" size={12} style={{ flexShrink: 0 }} />
          <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {backLabel}
          </span>
        </button>
        <div role="tablist" style={{ display: "flex", gap: 2, padding: "0 8px 6px" }}>
          {(["papers", "notes"] as const).map((k) => {
            const on = tab === k;
            return (
              <button
                key={k}
                role="tab"
                aria-selected={on}
                type="button"
                onClick={() => chooseTab(k)}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 5,
                  padding: "4px 10px",
                  borderRadius: "var(--radius-sm)",
                  border: "none",
                  cursor: "pointer",
                  fontFamily: "var(--font-sans)",
                  fontSize: 12,
                  fontWeight: 600,
                  background: on ? "var(--bg-inset)" : "transparent",
                  color: on ? "var(--heading)" : "var(--fg-muted)",
                }}
              >
                {t(k === "papers" ? "sidebar_tab_papers" : "sidebar_tab_notes")}
                {k === "papers" && !model.loading && !model.failed && (
                  <span style={{ fontSize: 10.5, fontFamily: "var(--font-mono)", color: "var(--fg-subtle)" }}>
                    {model.docs.length}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </header>

      {tab === "papers" ? (
        <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
          {!model.loading && !model.failed && model.docs.length > 0 && <DocListToolbar model={model} compact />}
          <DocList
            model={model}
            density="one-line"
            activeDocId={docId}
            onOpen={(d) => {
              setNoteDocId(null);
              setSelectedDocId(d.doc_id);
            }}
          />
        </div>
      ) : (
        <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
          <NotePanel docId={docId} embedded />
        </div>
      )}
    </section>
  );
}
