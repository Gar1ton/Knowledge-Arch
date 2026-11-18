"use client";
/**
 * 论文列表：中栏用两行（标题 / 作者 · 年份），阅读时左栏用一行。
 * 数据、查找词、排序、折叠的组都来自 `useCollectionDocs`，两处显示的是同一份。
 */
import React, { useEffect, useRef, useState } from "react";
import { Icon } from "@/components/ds/Icon";
import { IconButton } from "@/components/ds/IconButton";
import { Select } from "@/components/ds/Select";
import type { KrDocument } from "@/lib/api";
import { bylineOf, docIssue, type DocIssue, type SortKey } from "@/lib/docList";
import { useI18n, type I18nKey } from "@/lib/i18n";
import type { CollectionDocsModel } from "@/lib/useCollectionDocs";

export type DocListDensity = "two-line" | "one-line";

const ISSUE_TEXT: Record<Exclude<DocIssue, null>, I18nKey> = {
  no_text: "doclist_issue_no_text",
  pending_index: "doclist_issue_pending_index",
};

const SORT_LABEL: Record<SortKey, I18nKey> = {
  title: "doclist_sort_title",
  author: "doclist_sort_author",
  year: "doclist_sort_year",
  added: "doclist_sort_added",
};

// ─── Toolbar ──────────────────────────────────────────────────

export function DocListToolbar({
  model,
  compact = false,
}: {
  model: CollectionDocsModel;
  compact?: boolean;
}) {
  const { t } = useI18n();
  return (
    <div
      style={{
        position: "sticky",
        top: 0,
        zIndex: 1,
        display: "flex",
        alignItems: "center",
        gap: 6,
        padding: compact ? "6px 8px" : "8px 12px",
        background: "var(--surface)",
        borderBottom: "1px solid var(--border)",
      }}
    >
      <label
        style={{
          flex: 1,
          minWidth: 0,
          display: "flex",
          alignItems: "center",
          gap: 6,
          height: 28,
          padding: "0 8px",
          borderRadius: "var(--radius-md)",
          border: "1px solid var(--border-strong)",
          background: "var(--bg-inset)",
          color: "var(--fg-subtle)",
        }}
      >
        <Icon name="search" size={13} />
        <input
          value={model.query}
          onChange={(e) => model.setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") model.setQuery(""); }}
          placeholder={t(compact ? "doclist_search_short" : "doclist_search_placeholder")}
          aria-label={t("doclist_search_placeholder")}
          style={{
            flex: 1,
            minWidth: 0,
            border: "none",
            outline: "none",
            background: "transparent",
            color: "var(--fg)",
            fontSize: 12,
            fontFamily: "var(--font-sans)",
          }}
        />
        {model.query && (
          <span
            role="button"
            aria-label={t("doclist_search_clear")}
            onClick={() => model.setQuery("")}
            style={{ display: "inline-flex", cursor: "pointer" }}
          >
            <Icon name="x" size={12} />
          </span>
        )}
      </label>
      {!compact && (
        <Select
          value={model.sort}
          onChange={(v) => model.setSort(v as SortKey)}
          options={(Object.keys(SORT_LABEL) as SortKey[]).map((k) => ({ value: k, label: t(SORT_LABEL[k]) }))}
          style={{ width: 132, flexShrink: 0 }}
        />
      )}
      {model.issueCount > 0 && (
        <button
          type="button"
          onClick={() => model.setOnlyIssues(!model.onlyIssues)}
          title={t("doclist_issues_hint")}
          style={{
            flexShrink: 0,
            height: 26,
            padding: "0 9px",
            borderRadius: 999,
            border: `1px solid ${model.onlyIssues ? "var(--warn)" : "color-mix(in srgb, var(--warn) 45%, transparent)"}`,
            background: model.onlyIssues ? "color-mix(in srgb, var(--warn) 16%, transparent)" : "transparent",
            color: "var(--warn)",
            fontSize: 11.5,
            fontWeight: 600,
            cursor: "pointer",
            fontFamily: "var(--font-sans)",
            whiteSpace: "nowrap",
          }}
        >
          {t("doclist_issues", { n: model.issueCount })}
        </button>
      )}
    </div>
  );
}

// ─── Rows ─────────────────────────────────────────────────────

function DocRowTwoLine({
  doc,
  active,
  cursor,
  onOpen,
  onNote,
}: {
  doc: KrDocument;
  active: boolean;
  cursor: boolean;
  onOpen: () => void;
  onNote?: () => void;
}) {
  const { t } = useI18n();
  const [hover, setHover] = useState(false);
  const byline = bylineOf(doc);
  const issue = docIssue(doc);
  const title = doc.title || doc.filename || t("panel_untitled");
  return (
    <div
      role="option"
      aria-selected={active}
      data-doc-id={doc.doc_id}
      onClick={onOpen}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        padding: "7px 12px",
        cursor: "pointer",
        borderBottom: "1px solid color-mix(in srgb, var(--border) 60%, transparent)",
        background: active ? "var(--select-bg)" : hover || cursor ? "var(--surface-hover)" : "transparent",
        boxShadow: cursor && !active ? "inset 2px 0 0 var(--accent)" : undefined,
      }}
    >
      <div
        title={title}
        style={{
          fontSize: 13,
          fontWeight: 600,
          lineHeight: 1.35,
          color: active ? "var(--select-fg)" : "var(--heading)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {title}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 2, minHeight: 17, fontSize: 11.5 }}>
        <span
          style={{
            flex: 1,
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            color: active ? "var(--select-muted)" : byline ? "var(--fg-muted)" : "var(--fg-subtle)",
          }}
        >
          {byline ?? t("doclist_unknown_author")}
        </span>
        {issue && (
          <span style={{ flexShrink: 0, color: "var(--warn)", display: "inline-flex", alignItems: "center", gap: 4 }}>
            <span aria-hidden>⚠</span>
            {t(ISSUE_TEXT[issue])}
          </span>
        )}
        {onNote && hover && (
          <span
            role="button"
            title={t("documents_open_note")}
            onClick={(e) => { e.stopPropagation(); onNote(); }}
            style={{
              flexShrink: 0,
              display: "inline-flex",
              alignItems: "center",
              gap: 3,
              color: active ? "var(--select-muted)" : "var(--fg-subtle)",
              cursor: "pointer",
            }}
          >
            <Icon name="note" size={12} />
            {t("panel_note")}
          </span>
        )}
      </div>
    </div>
  );
}

function DocRowOneLine({
  doc,
  active,
  cursor,
  onOpen,
}: {
  doc: KrDocument;
  active: boolean;
  cursor: boolean;
  onOpen: () => void;
}) {
  const { t } = useI18n();
  const [hover, setHover] = useState(false);
  const byline = bylineOf(doc);
  const issue = docIssue(doc);
  const title = doc.title || doc.filename || t("panel_untitled");
  return (
    <div
      role="option"
      aria-selected={active}
      data-doc-id={doc.doc_id}
      onClick={onOpen}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title={[title, byline, issue ? t(ISSUE_TEXT[issue]) : null].filter(Boolean).join("\n")}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        margin: "1px 6px",
        padding: "4px 8px",
        borderRadius: "var(--radius-md)",
        cursor: "pointer",
        fontSize: 12.5,
        fontWeight: active ? 600 : 400,
        background: active ? "var(--select-bg)" : hover || cursor ? "var(--bg-inset)" : "transparent",
        color: active ? "var(--select-fg)" : "var(--fg)",
      }}
    >
      <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {title}
      </span>
      {issue && <span aria-hidden style={{ color: "var(--warn)", flexShrink: 0 }}>⚠</span>}
    </div>
  );
}

function GroupHeader({
  path,
  rootLabel,
  count,
  collapsed,
  compact,
  onToggle,
}: {
  path: string[];
  rootLabel: string;
  count: number;
  collapsed: boolean;
  compact: boolean;
  onToggle: () => void;
}) {
  const label = path.length ? path.join(" › ") : rootLabel;
  return (
    <div
      role="button"
      onClick={onToggle}
      title={label}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 5,
        padding: compact ? "6px 10px 3px" : "8px 12px 5px",
        cursor: "pointer",
        userSelect: "none",
        fontSize: 11,
        fontWeight: 700,
        color: "var(--fg-muted)",
        background: compact ? "transparent" : "var(--bg-inset)",
        borderBottom: compact ? "none" : "1px solid var(--border)",
      }}
    >
      <Icon
        name="chevR"
        size={12}
        style={{ transform: collapsed ? undefined : "rotate(90deg)", transition: "transform .15s", flexShrink: 0 }}
      />
      <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
      <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--fg-subtle)", flexShrink: 0 }}>
        {count}
      </span>
    </div>
  );
}

// ─── List ─────────────────────────────────────────────────────

/**
 * 列表主体。↑ / ↓ 移动光标、Enter 打开（焦点在列表上时）；当前打开的论文高亮并滚到可见处。
 */
export function DocList({
  model,
  density,
  activeDocId,
  revealDocId,
  onOpen,
  onNote,
}: {
  model: CollectionDocsModel;
  density: DocListDensity;
  activeDocId?: string | null;
  /** 没有打开的论文时要停在的那一篇（刚从阅读器返回时）：滚到它并画上光标。 */
  revealDocId?: string | null;
  onOpen: (doc: KrDocument) => void;
  onNote?: (docId: string) => void;
}) {
  const { t } = useI18n();
  const compact = density === "one-line";
  const listRef = useRef<HTMLDivElement>(null);
  const [cursorId, setCursorId] = useState<string | null>(null);

  const visible = model.groups.flatMap((g) => (model.grouped && model.collapsed[g.key] ? [] : g.docs));

  // 打开的论文（或刚读完返回的那篇）滚到列表中间：阅读时左栏换论文、从阅读器返回列表时。
  const focusId = activeDocId ?? revealDocId ?? null;
  useEffect(() => {
    if (!focusId || !listRef.current) return;
    const el = listRef.current.querySelector<HTMLElement>(`[data-doc-id="${CSS.escape(focusId)}"]`);
    el?.scrollIntoView({ block: "center" });
  }, [focusId, model.loading]);

  function onKeyDown(e: React.KeyboardEvent) {
    if (!visible.length) return;
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp" && e.key !== "Enter") return;
    const from = cursorId ?? focusId;
    const index = from ? visible.findIndex((d) => d.doc_id === from) : -1;
    if (e.key === "Enter") {
      if (index >= 0) { e.preventDefault(); onOpen(visible[index]); }
      return;
    }
    e.preventDefault();
    const next = e.key === "ArrowDown"
      ? Math.min(visible.length - 1, index + 1)
      : Math.max(0, index < 0 ? 0 : index - 1);
    const id = visible[next].doc_id;
    setCursorId(id);
    listRef.current
      ?.querySelector<HTMLElement>(`[data-doc-id="${CSS.escape(id)}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }

  if (model.loading) {
    return <div style={emptyStyle}>{t("panel_loading")}</div>;
  }
  if (model.failed) {
    return (
      <div style={emptyStyle}>
        <div>{t("documents_list_failed")}</div>
        <button type="button" onClick={model.reload} style={retryStyle}>{t("btn_retry")}</button>
      </div>
    );
  }
  if (model.docs.length === 0) {
    return <div style={emptyStyle}>{t("documents_empty_collection")}</div>;
  }
  if (model.ordered.length === 0) {
    return <div style={emptyStyle}>{t("doclist_no_match")}</div>;
  }

  const rootLabel = t("doclist_root_group");
  return (
    <div
      ref={listRef}
      role="listbox"
      tabIndex={0}
      onKeyDown={onKeyDown}
      onBlur={() => setCursorId(null)}
      style={{ outline: "none", paddingBottom: compact ? 8 : 24 }}
    >
      {model.groups.map((g) => {
        const collapsed = model.grouped && !!model.collapsed[g.key];
        return (
          <React.Fragment key={g.key || "_"}>
            {model.grouped && (
              <GroupHeader
                path={g.path}
                rootLabel={rootLabel}
                count={g.docs.length}
                collapsed={collapsed}
                compact={compact}
                onToggle={() => model.toggleGroup(g.key)}
              />
            )}
            {!collapsed && g.docs.map((d) =>
              compact ? (
                <DocRowOneLine
                  key={d.doc_id}
                  doc={d}
                  active={d.doc_id === activeDocId}
                  cursor={d.doc_id === cursorId}
                  onOpen={() => onOpen(d)}
                />
              ) : (
                <DocRowTwoLine
                  key={d.doc_id}
                  doc={d}
                  active={d.doc_id === activeDocId}
                  cursor={d.doc_id === (cursorId ?? revealDocId)}
                  onOpen={() => onOpen(d)}
                  onNote={onNote ? () => onNote(d.doc_id) : undefined}
                />
              ),
            )}
          </React.Fragment>
        );
      })}
    </div>
  );
}

const emptyStyle: React.CSSProperties = {
  padding: "36px 16px",
  textAlign: "center",
  color: "var(--fg-subtle)",
  fontSize: 13,
};

const retryStyle: React.CSSProperties = {
  marginTop: 10,
  padding: "4px 12px",
  borderRadius: "var(--radius-md)",
  border: "1px solid var(--border-strong)",
  background: "var(--surface)",
  color: "var(--fg)",
  cursor: "pointer",
  fontSize: 12,
};

// ─── Stepper ──────────────────────────────────────────────────

/** 阅读器栏头的「‹ n/m ›」：按当前查找与排序换上一篇 / 下一篇；论文不在列表里时不显示。 */
export function DocStepper({
  model,
  docId,
  onGo,
}: {
  model: CollectionDocsModel;
  docId: string;
  onGo: (doc: KrDocument) => void;
}) {
  const { t } = useI18n();
  const index = model.ordered.findIndex((d) => d.doc_id === docId);
  if (index < 0 || model.ordered.length < 2) return null;
  const prev = index > 0 ? model.ordered[index - 1] : null;
  const next = index < model.ordered.length - 1 ? model.ordered[index + 1] : null;
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 1, flexShrink: 0 }}>
      <IconButton
        name="chevL"
        size={13}
        label={t("doclist_prev")}
        onClick={() => prev && onGo(prev)}
        style={{ width: 22, height: 22, opacity: prev ? 1 : 0.35 }}
      />
      <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--fg-muted)", minWidth: 34, textAlign: "center" }}>
        {index + 1}/{model.ordered.length}
      </span>
      <IconButton
        name="chevR"
        size={13}
        label={t("doclist_next")}
        onClick={() => next && onGo(next)}
        style={{ width: 22, height: 22, opacity: next ? 1 : 0.35 }}
      />
    </div>
  );
}
