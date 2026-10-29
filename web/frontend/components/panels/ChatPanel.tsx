"use client";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Badge } from "@/components/ds/Badge";
import { Icon } from "@/components/ds/Icon";
import { IconButton } from "@/components/ds/IconButton";
import { Button } from "@/components/ds/Button";
import { Tooltip } from "@/components/ds/Tooltip";
import { Popover } from "@/components/ds/Popover";
import { Z } from "@/lib/zLayers";
import { useConsole } from "@/lib/ConsoleContext";
import { useToast } from "@/components/ui/Toast";
import { useI18n, type I18nKey } from "@/lib/i18n";
import { notifyAskSent } from "@/lib/tour";
import { AnswerMarkdown } from "@/components/panels/AnswerMarkdown";
import { FullTextConfirmDialog } from "@/components/panels/FullTextConfirmDialog";
import {
  AnswerScope, AskResult, AskSource, ApiError, Collection, AskTaskTimeoutError, FullTextConfirmation, GraphBuildEstimate, ThinkingTrace,
  ChatMessage, ask, buildGraph, estimateGraphBuild,
  getChatHistory, clearChatHistory, lockChatAnswer,
  createDocumentNote, createCollectionNote,
  getAskProgress, getDocument, waitForLateAskAnswer, type LiveProgressDetail,
  getEffectiveConfig, getLightRAGReadiness, listCollections, pushNotionNote,
} from "@/lib/api";

// ─── Types ────────────────────────────────────────────────────

type RetrievalMode =
  | "default"
  | "enhanced"
  | "graph_mixed"
  | "graph_only"
  | "fulltext"
  | "deep_thinking";

// 默认档位：enhanced 不需要集合；模式失效时的回退与设置键高亮都以它为基准。
const DEFAULT_MODE: RetrievalMode = "enhanced";

interface Message {
  id?: number;
  role: "user" | "assistant";
  content: string;
  sources?: AskSource[];
  actualRetrievalMode?: string;
  thinkingTrace?: ThinkingTrace;
  answerNotice?: string;
  standaloneQuestion?: string;
  pinned?: boolean;
  /** 这一轮实际检索的范围；之后切换选中不改变它（旧记录没有）。 */
  scope?: AnswerScope;
}

interface GraphBuildDialogState {
  question: string;
  collection: string;
  scope: AnswerScope;
  reason: string;
  estimate?: GraphBuildEstimate;
  canBuild: boolean;
  building?: boolean;
}

interface FullTextConfirmDialogState {
  question: string;
  info: FullTextConfirmation;
  running?: boolean;
}

// ─── Helpers ──────────────────────────────────────────────────

function retrievalModeLabel(
  mode: string | undefined,
  t: (key: I18nKey, vars?: Record<string, string | number>) => string,
): string {
  if (mode === "lightrag_only") return t("chat_retrieval_lightrag");
  if (mode === "milvus_lightrag") return t("chat_retrieval_milvus_lightrag");
  if (mode?.includes("lightrag")) return t("chat_retrieval_lightrag_mixed");
  if (mode === "sqlite_lexical") return t("chat_retrieval_lexical");
  if (mode === "none") return t("chat_retrieval_none");
  if (mode === "milvus_deep" || mode === "lexical_deep_fallback") return t("chat_retrieval_deep_mode");
  if (mode === "deep_degraded_to_default") return t("chat_retrieval_deep_degraded");
  if (mode === "enhanced_recall") return t("chat_retrieval_enhanced_mode");
  if (mode === "enhanced_degraded_to_default") return t("chat_retrieval_enhanced_degraded");
  // 缺这条会让全文检索的答案（以及历史回放）被兜底标成「Milvus 语义检索」。
  if (mode === "fulltext") return t("chat_retrieval_fulltext");
  return t("chat_retrieval_milvus");
}

function scopeLabel(
  scope: AnswerScope,
  t: (key: I18nKey, vars?: Record<string, string | number>) => string,
): string {
  if (scope.doc_id) return scope.doc_title || t("panel_scope_document");
  return scope.collection || t("panel_all_collections");
}

/** 集合按树序展开（父在前、子缩进），供范围选择器列出。 */
function collectionTree(list: Collection[]): { name: string; depth: number }[] {
  const keys = new Set(list.map((c) => c.coll_key || ""));
  const byParent: Record<string, Collection[]> = {};
  for (const c of list) {
    const parent = c.parent_key && keys.has(c.parent_key) ? c.parent_key : "";
    (byParent[parent] ||= []).push(c);
  }
  const out: { name: string; depth: number }[] = [];
  const seen = new Set<string>();
  const walk = (parent: string, depth: number) => {
    for (const c of (byParent[parent] ?? []).sort((a, b) => a.name.localeCompare(b.name))) {
      if (seen.has(c.name)) continue;
      seen.add(c.name);
      out.push({ name: c.name, depth });
      if (c.coll_key) walk(c.coll_key, depth + 1);
    }
  };
  walk("", 0);
  return out;
}

/** 范围菜单里的单篇文档项：小字说明（当前浏览 / 已选定）+ 文档标题。 */
function ScopeDocOption({ label, title, selected, onClick }: {
  label: string;
  title: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      onClick={onClick}
      title={title}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        width: "100%",
        padding: "6px 8px",
        borderRadius: "var(--radius-md)",
        background: selected ? "var(--accent-soft)" : "transparent",
        border: "none",
        cursor: "pointer",
        textAlign: "left",
        fontSize: 12,
        color: "var(--fg)",
      }}
    >
      <Icon name="file" size={11} />
      <span style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
        <span style={{ fontSize: 10, color: "var(--fg-subtle)" }}>{label}</span>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</span>
      </span>
    </button>
  );
}

// 思考过程：默认收起、可点击展开的可折叠区块（与 sources 一致的轻量样式）。
/** 答案下方的引用来源：与思考过程一样可折叠，默认收起（正文里点 [n] 仍可直接打开）。 */
function SourcesSection({
  sources,
  onCite,
  t,
}: {
  sources: AskSource[];
  onCite: (n: number) => void;
  t: (key: I18nKey, vars?: Record<string, string | number>) => string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--border)" }}>
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          width: "100%",
          background: "transparent",
          border: "none",
          padding: 0,
          cursor: "pointer",
          textAlign: "left",
        }}
      >
        <Icon name="quote" size={11} />
        <span
          style={{
            fontSize: 9.5,
            fontWeight: 700,
            letterSpacing: ".06em",
            textTransform: "uppercase",
            color: "var(--fg-subtle)",
          }}
        >
          {t("chat_sources_title", { n: sources.length })}
        </span>
        <span
          style={{
            marginLeft: "auto",
            fontSize: 11,
            color: "var(--fg-subtle)",
            transform: open ? "rotate(90deg)" : "none",
            transition: "transform .15s",
          }}
        >
          ▸
        </span>
      </button>
      {open && (
        <>
          <div style={{ fontSize: 10, color: "var(--fg-subtle)", marginTop: 4 }}>{t("chat_sources_open")}</div>
          {sources.map((s) => (
            <SourceMini key={s.n} s={s} onClick={() => onCite(s.n)} />
          ))}
        </>
      )}
    </div>
  );
}

function ThinkingTraceView({
  trace,
  t,
}: {
  trace: ThinkingTrace;
  t: (key: I18nKey, vars?: Record<string, string | number>) => string;
}) {
  const [open, setOpen] = useState(false);
  const statusLabel = trace.degraded
    ? t("chat_thinking_degraded")
    : trace.verified
      ? t("chat_thinking_verified")
      : t("chat_thinking_unverified");
  return (
    <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--border)" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          width: "100%",
          background: "transparent",
          border: "none",
          padding: 0,
          cursor: "pointer",
          textAlign: "left",
        }}
      >
        <Icon name="sparkle" size={11} />
        <span
          style={{
            fontSize: 9.5,
            fontWeight: 700,
            letterSpacing: ".06em",
            textTransform: "uppercase",
            color: "var(--fg-subtle)",
          }}
        >
          {t("chat_thinking_title")}
        </span>
        <Badge tone={trace.degraded ? "warn" : trace.verified ? "ok" : "neutral"}>
          {statusLabel}
        </Badge>
        {!trace.verified && trace.verify_missing.length > 0 && (
          <span style={{ fontSize: 9.5, color: "var(--warn)" }}>
            {t("chat_thinking_gap_count", { n: trace.verify_missing.length })}
          </span>
        )}
        <span
          style={{
            marginLeft: "auto",
            fontSize: 11,
            color: "var(--fg-subtle)",
            transform: open ? "rotate(90deg)" : "none",
            transition: "transform .15s",
          }}
        >
          ▸
        </span>
      </button>
      {trace.degraded && trace.degraded_reason && (
        <div
          style={{
            fontSize: 10,
            color: "var(--warn)",
            marginTop: 3,
            marginLeft: 2,
            lineHeight: 1.5,
            wordBreak: "break-all",
          }}
        >
          {trace.degraded_reason}
        </div>
      )}
      {open && (
        <div style={{ marginTop: 7, fontSize: 11, lineHeight: 1.65, color: "var(--fg-muted)" }}>
          {trace.checklist.length > 0 && (
            <div style={{ marginBottom: 7 }}>
              <div style={{ fontWeight: 600, color: "var(--fg-subtle)", marginBottom: 2 }}>
                {t("chat_thinking_checklist")}
              </div>
              {trace.checklist.map((item) => (
                <div key={item.id} style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
                  <span style={{ color: item.satisfied ? "var(--ok)" : "var(--fg-subtle)" }}>
                    {item.satisfied ? "✓" : "○"}
                  </span>
                  <span style={{ color: "var(--fg)" }}>
                    {item.text}
                    {item.critical && <span style={{ color: "var(--warn)", marginLeft: 4 }}>*</span>}
                  </span>
                </div>
              ))}
            </div>
          )}
          {trace.rounds.map((r) => (
            <div key={r.round} style={{ marginBottom: 5 }}>
              <div style={{ fontWeight: 600, color: "var(--fg-subtle)" }}>
                {t("chat_thinking_round", { n: r.round })}
              </div>
              {r.queries.length > 0 && (
                <div>
                  {t("chat_thinking_queries")}: {r.queries.join(" · ")}
                </div>
              )}
              {r.gaps.length > 0 && (
                <div>
                  {t("chat_thinking_gaps")}: {r.gaps.join(" · ")}
                </div>
              )}
              {r.discovered && r.discovered.length > 0 && (
                <div style={{ color: "var(--ok)" }}>
                  ✦ {t("chat_thinking_discovered")}: {r.discovered.join(" · ")}
                </div>
              )}
            </div>
          ))}
          {trace.verify_missing.length > 0 && (
            <div style={{ color: "var(--warn)", marginBottom: 4 }}>
              {t("chat_thinking_missing")}: {trace.verify_missing.join(" · ")}
            </div>
          )}
          {trace.verify_notes && trace.verify_notes.length > 0 && (
            <div style={{ color: "var(--fg-muted)", marginBottom: 4 }}>
              {t("chat_thinking_notes")}: {trace.verify_notes.join(" · ")}
            </div>
          )}
          <div style={{ color: "var(--fg-subtle)" }}>
            {t("chat_thinking_tokens", { n: trace.est_total_tokens })}
          </div>
        </div>
      )}
    </div>
  );
}

// 深度思考进行中的实时推演视图：随轮询逐轮出现，复用与最终 ThinkingTraceView 一致的行布局。
function LiveThinkingView({
  detail,
  t,
}: {
  detail: LiveProgressDetail;
  t: (key: I18nKey, vars?: Record<string, string | number>) => string;
}) {
  return (
    <div style={{ fontSize: 11, lineHeight: 1.65, color: "var(--fg-muted)", flex: 1 }}>
      {detail.checklist.length > 0 && (
        <div style={{ marginBottom: 6 }}>
          <div style={{ fontWeight: 600, color: "var(--fg-subtle)", marginBottom: 2 }}>
            {t("chat_thinking_checklist")}
          </div>
          {detail.checklist.map((item) => (
            <div key={item.id} style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
              <span style={{ color: item.satisfied ? "var(--ok)" : "var(--fg-subtle)" }}>
                {item.satisfied ? "✓" : "○"}
              </span>
              <span style={{ color: "var(--fg)" }}>
                {item.text}
                {item.critical && <span style={{ color: "var(--warn)", marginLeft: 4 }}>*</span>}
              </span>
            </div>
          ))}
        </div>
      )}
      {detail.rounds.map((r) => (
        <div key={r.round} style={{ marginBottom: 5 }}>
          <div style={{ fontWeight: 600, color: "var(--fg-subtle)" }}>
            {t("chat_thinking_round", { n: r.round })}
          </div>
          {r.queries.length > 0 && (
            <div>
              {t("chat_thinking_queries")}: {r.queries.join(" · ")}
            </div>
          )}
          {r.gaps.length > 0 && (
            <div>
              {t("chat_thinking_gaps")}: {r.gaps.join(" · ")}
            </div>
          )}
          {r.discovered && r.discovered.length > 0 && (
            <div style={{ color: "var(--ok)" }}>
              ✦ {t("chat_thinking_discovered")}: {r.discovered.join(" · ")}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function formatDuration(seconds?: number | null): string {
  const total = Math.max(0, Math.round(seconds ?? 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

// ─── SourceMini card ──────────────────────────────────────────

function SourceMini({ s, onClick }: { s: AskSource; onClick: () => void }) {
  return (
    <div
      onClick={onClick}
      style={{
        display: "flex",
        gap: 7,
        padding: "7px 9px",
        borderRadius: "var(--radius-md)",
        border: "1px solid var(--border)",
        background: "var(--surface)",
        cursor: "pointer",
        marginTop: 6,
      }}
    >
      <span
        style={{
          width: 16,
          height: 16,
          flexShrink: 0,
          borderRadius: "50%",
          background: "var(--accent)",
          color: "#fff",
          fontSize: 9.5,
          fontWeight: 700,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {s.n}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span
            title={s.title}
            style={{
              flex: 1,
              minWidth: 0,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              fontSize: 11,
              fontWeight: 600,
              color: "var(--heading)",
            }}
          >
            {s.title}
          </span>
          {s.rrf_score != null && (
            <span style={{ fontSize: 9.5, fontFamily: "var(--font-mono)", color: "var(--accent)" }}>
              RRF {s.rrf_score.toFixed(4)}
            </span>
          )}
        </div>
        <p
          style={{
            margin: "3px 0 0",
            fontSize: 10.5,
            lineHeight: 1.5,
            color: "var(--fg-muted)",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}
        >
          {s.text}
        </p>
      </div>
    </div>
  );
}

// ─── MessageBubble ────────────────────────────────────────────

const ACTION_BTN: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  fontSize: 11,
  fontWeight: 500,
  color: "var(--fg-muted)",
  background: "transparent",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-pill)",
  padding: "3px 9px",
  cursor: "pointer",
  fontFamily: "var(--font-sans)",
};

function MessageBubble({
  msg,
  onCite,
  onPin,
  onSaveNote,
  onPushNotion,
  notionEnabled,
  showRewrite,
}: {
  msg: Message;
  onCite: (n: number) => void;
  onPin: () => void;
  onSaveNote: () => void;
  onPushNotion?: () => void;
  notionEnabled?: boolean;
  showRewrite?: boolean;
}) {
  const [hover, setHover] = useState(false);
  const { t } = useI18n();
  const isUser = msg.role === "user";

  if (isUser) {
    return (
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 14 }}>
        <div
          style={{
            maxWidth: "86%",
            background: "var(--accent)",
            color: "var(--accent-fg)",
            borderRadius: "10px 10px 3px 10px",
            padding: "9px 12px",
            fontSize: 12.5,
            lineHeight: 1.6,
          }}
        >
          {msg.content}
        </div>
      </div>
    );
  }

  return (
    <div
      style={{ marginBottom: 16 }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <div
        style={{
          maxWidth: "94%",
          background: "var(--surface)",
          border: `1px solid ${msg.pinned ? "var(--accent-border)" : "var(--border)"}`,
          borderRadius: "10px 10px 10px 3px",
          padding: "10px 13px",
          boxShadow: msg.pinned ? "0 0 0 3px var(--ring)" : "var(--shadow-card)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 7 }}>
          <Badge tone="accent">{retrievalModeLabel(msg.actualRetrievalMode, t)}</Badge>
          {msg.scope && (
            <Badge tone="neutral">
              <Icon name={msg.scope.doc_id ? "file" : "folder"} size={9} />{" "}
              <span
                title={t("chat_scope_answered", { scope: scopeLabel(msg.scope, t) })}
                style={{
                  maxWidth: 160,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  display: "inline-block",
                  verticalAlign: "bottom",
                }}
              >
                {scopeLabel(msg.scope, t)}
              </span>
            </Badge>
          )}
          {msg.pinned && (
            <Badge tone="warn">
              <Icon name="pin" size={9} /> {t("chat_locked")}
            </Badge>
          )}
        </div>
        {/* v1.1.0：校验告警前置到正文之前——读完整篇才发现「未通过证据校验」为时已晚。
            与存库回放（stored_answer）同序。 */}
        {msg.answerNotice && (
          <div
            style={{
              marginBottom: 8,
              paddingBottom: 8,
              borderBottom: "1px solid var(--border)",
              fontSize: 11,
              lineHeight: 1.6,
              color: "var(--warn)",
            }}
          >
            ⚠️ {msg.answerNotice.replace(/\*\*/g, "")}
          </div>
        )}
        <AnswerMarkdown text={msg.content} sources={msg.sources} onCite={onCite} />
        {showRewrite && msg.standaloneQuestion && (
          <div style={{ marginTop: 8, fontSize: 11, lineHeight: 1.5, color: "var(--fg-subtle)" }}>
            {t("chat_understood_as", { question: msg.standaloneQuestion })}
          </div>
        )}
        {msg.sources && msg.sources.length > 0 && (
          <SourcesSection sources={msg.sources} onCite={onCite} t={t} />
        )}
        {msg.thinkingTrace && <ThinkingTraceView trace={msg.thinkingTrace} t={t} />}
      </div>
      <div
        style={{
          display: "flex",
          gap: 4,
          marginTop: 6,
          opacity: hover || msg.pinned ? 1 : 0.4,
          transition: "opacity .15s",
        }}
      >
        <Tooltip label={t("chat_add_linked_note_tip")} side="top">
          <button onClick={onSaveNote} style={ACTION_BTN}>
            <Icon name="link" size={12} /> {t("chat_add_linked_note")}
          </button>
        </Tooltip>
        <Tooltip label={msg.pinned ? t("chat_unlock_answer") : t("chat_lock_answer")} side="top">
          <button
            onClick={onPin}
            style={{ ...ACTION_BTN, color: msg.pinned ? "var(--accent)" : "var(--fg-muted)" }}
          >
            <Icon name="pin" size={12} /> {msg.pinned ? t("chat_answer_locked") : t("chat_lock_answer_short")}
          </button>
        </Tooltip>
        {notionEnabled && onPushNotion && (
          <Tooltip label={t("chat_push_notion_tip")} side="top">
            <button onClick={onPushNotion} style={ACTION_BTN}>
              <Icon name="layers" size={12} /> {t("chat_push_notion")}
            </button>
          </Tooltip>
        )}
      </div>
    </div>
  );
}

// ─── GraphBuildDialog ─────────────────────────────────────────

function GraphBuildDialog({
  state,
  onBuild,
  onFallback,
  onCancel,
}: {
  state: GraphBuildDialogState;
  onBuild: () => void;
  onFallback: () => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const rows: [string, string][] = state.estimate
    ? [
        [t("chat_est_collection"), state.estimate.collection],
        [t("chat_est_docs"), String(state.estimate.docs_count)],
        [t("chat_est_chunks"), String(state.estimate.chunks_count)],
        [t("chat_est_llm_calls"), `${state.estimate.estimated_llm_calls_min} – ${state.estimate.estimated_llm_calls_max}`],
        [
          t("chat_est_duration"),
          `${formatDuration(state.estimate.estimated_duration_seconds_min)} – ${formatDuration(state.estimate.estimated_duration_seconds_max)}`,
        ],
      ]
    : [[t("chat_est_collection"), state.collection]];

  if (typeof document === "undefined") return null;

  return createPortal(
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,.35)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: Z.dialog,
      }}
      onClick={(e) => e.target === e.currentTarget && !state.building && onCancel()}
    >
      <div
        style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-3xl)",
          padding: 24,
          width: 410,
          boxShadow: "var(--shadow-pop)",
          display: "flex",
          flexDirection: "column",
          gap: 16,
        }}
      >
        <div>
          <h3 style={{ margin: "0 0 5px", fontSize: 15, fontWeight: 700, color: "var(--heading)" }}>
            {t("chat_graph_mixed_title")}
          </h3>
          <p style={{ margin: 0, fontSize: 11, lineHeight: 1.6, color: "var(--fg-muted)" }}>
            {state.reason}
          </p>
        </div>
        <div
          style={{
            background: "var(--bg-inset)",
            border: "1px solid var(--border)",
            borderRadius: 10,
            overflow: "hidden",
          }}
        >
          {rows.map(([label, value], i) => (
            <div
              key={label}
              style={{
                display: "flex",
                justifyContent: "space-between",
                gap: 12,
                padding: "8px 14px",
                borderTop: i > 0 ? "1px solid var(--border)" : undefined,
              }}
            >
              <span style={{ fontSize: 12, color: "var(--fg-muted)" }}>{label}</span>
              <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--fg)", fontWeight: 600 }}>
                {value}
              </span>
            </div>
          ))}
        </div>
        {state.estimate?.estimate_notice && (
          <div
            style={{
              fontSize: 11,
              lineHeight: 1.6,
              color: "var(--warn)",
              background: "color-mix(in srgb, var(--warn) 10%, transparent)",
              border: "1px solid color-mix(in srgb, var(--warn) 30%, transparent)",
              borderRadius: 8,
              padding: "8px 12px",
            }}
          >
            {state.estimate.estimate_notice}
          </div>
        )}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", flexWrap: "wrap" }}>
          <Button variant="ghost" size="sm" disabled={state.building} onClick={onCancel}>
            {t("btn_cancel")}
          </Button>
          <Button variant="outline" size="sm" disabled={state.building} onClick={onFallback}>
            {t("chat_use_milvus_once")}
          </Button>
          {state.canBuild && state.estimate && (
            <Button size="sm" loading={state.building} onClick={onBuild}>
              {t("chat_start_build")}
            </Button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

// ─── ChatPanel ────────────────────────────────────────────────

const CONV_KEY = "kr_conversation_id";

/** 聊天设置里记在本机的开关（重启应用后保持上次选择）。 */
function useStoredToggle(key: string, fallback: boolean): [boolean, () => void] {
  const [value, setValue] = useState(() => {
    if (typeof window === "undefined") return fallback;
    try {
      const stored = localStorage.getItem(key);
      return stored === null ? fallback : stored === "1";
    } catch {
      return fallback;
    }
  });
  const toggle = useCallback(() => {
    setValue((current) => {
      const next = !current;
      try {
        localStorage.setItem(key, next ? "1" : "0");
      } catch {
        /* 存储不可用时只在本次会话生效 */
      }
      return next;
    });
  }, [key]);
  return [value, toggle];
}

function ToggleRow({ label, hint, on, onToggle }: { label: string; hint?: string; on: boolean; onToggle: () => void }) {
  return (
    <div
      role="switch"
      aria-checked={on}
      title={hint}
      onClick={onToggle}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "6px 8px",
        cursor: "pointer",
        borderTop: "1px solid var(--border)",
        fontSize: 12,
        color: "var(--fg)",
      }}
    >
      <span>{label}</span>
      <div
        style={{
          width: 28,
          height: 16,
          borderRadius: 8,
          background: on ? "var(--accent)" : "var(--border-strong)",
          position: "relative",
          transition: "background .15s",
          flexShrink: 0,
        }}
      >
        <div
          style={{
            position: "absolute",
            top: 2,
            left: on ? 12 : 2,
            width: 12,
            height: 12,
            borderRadius: "50%",
            background: "white",
            transition: "left .15s",
          }}
        />
      </div>
    </div>
  );
}

function getOrCreateConvId(): string {
  if (typeof window === "undefined") return crypto.randomUUID();
  const stored = localStorage.getItem(CONV_KEY);
  if (stored) return stored;
  const id = crypto.randomUUID();
  localStorage.setItem(CONV_KEY, id);
  return id;
}

/** ChatMessage（后端存库形态）→ Message（气泡渲染形态）；挂载加载历史与超时后补回答案共用。 */
function chatMessagesToMessages(msgs: ChatMessage[]): Message[] {
  return msgs.map((m) => ({
    role: m.role as "user" | "assistant",
    content: m.content,
    sources: m.sources,
    actualRetrievalMode: m.retrieval_mode || undefined,
    id: m.id,
    pinned: Boolean(m.locked),
    thinkingTrace: m.meta?.thinking_trace ?? undefined,
    standaloneQuestion: m.meta?.standalone_question || undefined,
    scope: m.meta?.scope ?? undefined,
  }));
}

export function ChatPanel({ width }: { width?: number }) {
  const { selectedCollection, selectedDocId, setSelectedDocId, setHighlightedChunk } = useConsole();
  const { t } = useI18n();
  const { toast } = useToast();
  const scrollRef = useRef<HTMLDivElement>(null);
  const historyLoadedRef = useRef<string | null>(null);
  const settingsRef = useRef<HTMLDivElement>(null);
  const scopeRef = useRef<HTMLButtonElement>(null);

  // Collection name (strip prefix)
  const collectionName = selectedCollection
    ? selectedCollection.replace(/^(z:|l:|lr:)/, "")
    : null;

  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // 聊天自己的知识范围（集合名，null = 全部集合）：范围标签里可直接切换；
  // 空闲时跟随左侧点选的集合，回答进行中不跟——那一轮的范围已锁定在 pendingScope。
  const [chatCollection, setChatCollection] = useState<string | null>(null);
  const [pendingScope, setPendingScope] = useState<AnswerScope | null>(null);
  const [showScopeMenu, setShowScopeMenu] = useState(false);
  const [scopeCollections, setScopeCollections] = useState<{ name: string; depth: number }[]>([]);
  // 聊天所选集合的图谱是否可用于检索：图谱档位只在就绪时可选（以前靠左侧 LightRAG 区的 lr: 前缀）。
  const [graphReadiness, setGraphReadiness] = useState<{ collection: string; ready: boolean } | null>(null);
  const loadingRef = useRef(false);
  useEffect(() => {
    loadingRef.current = loading;
  }, [loading]);
  useEffect(() => {
    if (!loadingRef.current) setChatCollection(collectionName);
  }, [collectionName]);
  // 深度思考进行中的实时推演详情（轮询 /api/ask/progress 增量更新）。
  const [liveProgress, setLiveProgress] = useState<LiveProgressDetail | null>(null);
  const liveTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // 进行中的提问（请求本身 + 超时后的补轮询）→ 所属会话；清空聊天时全部中止。
  const askAbortsRef = useRef(new Map<AbortController, string>());
  const [retrievalMode, setRetrievalMode] = useState<RetrievalMode>(DEFAULT_MODE);
  const personaEnabled = false;
  const [useEnglishRetrieval, toggleEnglishRetrieval] = useStoredToggle("ka-chat-english-retrieval", true);
  const [useContext, toggleUseContext] = useStoredToggle("ka-chat-use-context", true);
  const [showRewrite, toggleShowRewrite] = useStoredToggle("ka-chat-show-rewrite", true);
  const [modelThinking, toggleModelThinking] = useStoredToggle("ka-chat-model-thinking", true);
  const [answerLanguage, setAnswerLanguage] = useState<"auto" | "zh" | "en">("auto");
  const [showSettings, setShowSettings] = useState(false);
  const [graphBuildDialog, setGraphBuildDialog] = useState<GraphBuildDialogState | null>(null);
  const [fullTextDialog, setFullTextDialog] = useState<FullTextConfirmDialogState | null>(null);
  const [notionEnabled, setNotionEnabled] = useState(false);
  const [docInfo, setDocInfo] = useState<{ id: string; title: string } | null>(null);
  useEffect(() => {
    if (!selectedDocId) return;
    let alive = true;
    getDocument(selectedDocId)
      .then((doc) => {
        if (alive) setDocInfo({ id: doc.doc_id, title: doc.title || doc.filename || "" });
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [selectedDocId]);
  // 选中文档时显示标题（内部 ID 形如 LOCAL_<item>_<attachment>，对用户没有意义）。
  const docTitle = docInfo && docInfo.id === selectedDocId ? docInfo.title : "";
  // 范围菜单里点「当前浏览的文档」时钉住的那一篇：是一次性的切换，之后中间换文档、左侧点集合都不改它，
  // 直到在范围菜单里另选（集合，或再点一次换成新打开的文档）。
  const [pinnedDoc, setPinnedDoc] = useState<{ id: string; title: string } | null>(null);
  // 卸载时兜底清除轮询定时器，避免组件被销毁后仍在轮询。
  useEffect(
    () => () => {
      if (liveTimerRef.current) clearInterval(liveTimerRef.current);
    },
    [],
  );

  useEffect(() => {
    const cid = getOrCreateConvId();
    setConversationId(cid);
  }, []);

  // askAI 的中英文开关与后端 ask.answer_language 是同一参数：挂载时以配置项为初值，保持前后端一致。
  useEffect(() => {
    let cancelled = false;
    getEffectiveConfig()
      .then((cfg) => {
        const lang = (cfg.ask as { answer_language?: string } | undefined)?.answer_language;
        if (!cancelled && (lang === "auto" || lang === "zh" || lang === "en")) {
          setAnswerLanguage(lang);
        }
        const ns = (cfg.notion_sync as { enabled?: boolean } | undefined)?.enabled;
        if (!cancelled) setNotionEnabled(Boolean(ns));
      })
      .catch(() => {
        /* 配置不可用时保持默认 auto，不打断聊天 */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!conversationId || historyLoadedRef.current === conversationId) return;
    historyLoadedRef.current = conversationId;
    getChatHistory(conversationId)
      .then((msgs: ChatMessage[]) => {
        if (msgs.length === 0) return;
        setMessages(chatMessagesToMessages(msgs));
      })
      .catch(() => {});
  }, [conversationId]);


  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // 换集合或打开查询设置时现查图谱就绪度（建图可能刚在左侧完成）。
  useEffect(() => {
    if (!chatCollection) return;
    let alive = true;
    getLightRAGReadiness(chatCollection)
      .then((r) => {
        if (alive) setGraphReadiness({ collection: chatCollection, ready: r.ready });
      })
      .catch(() => {
        if (alive) setGraphReadiness({ collection: chatCollection, ready: false });
      });
    return () => {
      alive = false;
    };
  }, [chatCollection, showSettings]);
  // 单篇文档范围下图谱档位不可用（图谱按集合建，无法只取一篇）。
  const graphReady = Boolean(
    !pinnedDoc && chatCollection && graphReadiness?.collection === chatCollection && graphReadiness.ready,
  );
  const graphKnownNotReady = Boolean(
    chatCollection && graphReadiness?.collection === chatCollection && !graphReadiness.ready,
  );
  // 全文模式读的文档：钉住的那篇优先，否则是中间正在打开的。
  const fulltextDocId = pinnedDoc?.id ?? selectedDocId;

  useEffect(() => {
    if (loading) return; // 进行中的那一轮已锁定范围与模式
    const graphMode = retrievalMode === "graph_mixed" || retrievalMode === "graph_only";
    if (!fulltextDocId && retrievalMode === "fulltext") setRetrievalMode(DEFAULT_MODE);
    if (!chatCollection && !pinnedDoc && (retrievalMode === "deep_thinking" || graphMode)) {
      setRetrievalMode(DEFAULT_MODE);
    }
    if ((graphKnownNotReady || pinnedDoc) && graphMode) setRetrievalMode(DEFAULT_MODE);
  }, [fulltextDocId, pinnedDoc, chatCollection, retrievalMode, loading, graphKnownNotReady]);

  // 范围选择器打开时现取集合列表（改名 / 新建后不必刷新页面）。
  useEffect(() => {
    if (!showScopeMenu) return;
    let alive = true;
    listCollections()
      .then((list) => {
        if (alive) setScopeCollections(collectionTree(list.filter((c) => !c.is_system)));
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [showScopeMenu]);

  function pickScope(name: string | null) {
    setShowScopeMenu(false);
    setPinnedDoc(null);
    setChatCollection(name);
    // 全文模式的范围是文档；选了集合就是要按集合问，切回默认档位。
    if (retrievalMode === "fulltext") setRetrievalMode(DEFAULT_MODE);
  }

  // 钉住这一刻中间打开的文档（标题未取到时按「当前文档」显示）。
  function pickViewingDoc() {
    setShowScopeMenu(false);
    if (selectedDocId) setPinnedDoc({ id: selectedDocId, title: docTitle });
  }

  function handleCite(msg: Message, n: number) {
    const src = msg.sources?.find((s) => s.n === n);
    if (!src) return;
    setHighlightedChunk({ docId: src.doc_id, chunkId: src.chunk_id });
    if (src.doc_id !== selectedDocId) setSelectedDocId(src.doc_id);
  }

  async function handlePin(index: number) {
    const current = messages[index];
    const nextPinned = !current?.pinned;
    setMessages((prev) => prev.map((m, i) => (i === index ? { ...m, pinned: nextPinned } : m)));
    if (!conversationId) return;
    try {
      const updated = await lockChatAnswer(conversationId, index, nextPinned);
      setMessages((prev) =>
        prev.map((m, i) => (i === index ? { ...m, id: updated.id, pinned: Boolean(updated.locked) } : m)),
      );
    } catch (err) {
      setMessages((prev) => prev.map((m, i) => (i === index ? { ...m, pinned: !nextPinned } : m)));
      toast(err instanceof ApiError ? err.message : t("error_generic"), "error");
    }
  }

  async function handleSaveNote(msg: Message) {
    const body = msg.content.replace(/\*\*/g, "").replace(/\[\d+\]/g, "").slice(0, 200);
    const options = {
      linked: true,
      source: "chat",
      chat_conversation_id: conversationId ?? undefined,
      chat_message_id: msg.id,
    };
    // 笔记挂到这条回答所属的范围；全部集合的回答与旧记录沿用当前选中。
    const noteDocId = msg.scope?.doc_id || (msg.scope?.collection ? null : selectedDocId);
    const noteCollection = msg.scope?.collection || collectionName;
    try {
      if (noteDocId) {
        await createDocumentNote(noteDocId, body, options);
      } else if (noteCollection) {
        await createCollectionNote(noteCollection, body, options);
      } else {
        throw new Error("no active note scope");
      }
      toast(t("chat_note_saved"), "ok");
      return;
    } catch {
      /* local fallback below */
    }
    const key = `kr_notes_${noteCollection ?? "global"}`;
    const existing = JSON.parse(localStorage.getItem(key) ?? "[]") as string[];
    localStorage.setItem(key, JSON.stringify([...existing, body]));
    toast(t("chat_note_saved"), "ok");
  }

  async function handlePushNotion(msg: Message, index: number) {
    // 取该回答对应的上一条用户提问作为标题；引用取 sources 的 doc_id（去重）。
    const prev = messages[index - 1];
    const title = prev && prev.role === "user" ? prev.content.slice(0, 60) : "";
    const citations = Array.from(
      new Set((msg.sources ?? []).map((s) => s.doc_id).filter(Boolean)),
    );
    try {
      const res = await pushNotionNote({
        content: msg.content,
        title,
        citations,
        source: "ask",
      });
      if (res.status === "success") {
        toast(t("chat_notion_pushed"), "ok");
      } else if (res.status === "disabled") {
        toast(t("chat_notion_disabled"), "error");
      } else {
        toast(res.message || t("chat_notion_push_failed"), "error");
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : t("chat_notion_push_failed"), "error");
    }
  }

  function clearChat() {
    // 进行中的回答一并打断：中止请求与补轮询、停进度轮询、恢复发送键；
    // 后端任务由 DELETE /api/chat/history 取消，不会再把问答写回。
    const pending = askAbortsRef.current;
    const interrupted = pending.size > 0;
    const cids = new Set(pending.values());
    if (conversationId) cids.add(conversationId);
    pending.forEach((_cid, controller) => controller.abort());
    pending.clear();
    if (liveTimerRef.current) {
      clearInterval(liveTimerRef.current);
      liveTimerRef.current = null;
    }
    setLiveProgress(null);
    setLoading(false);
    setPendingScope(null);
    setMessages((prev) => prev.filter((m) => m.pinned));
    Promise.all([...cids].map((c) => clearChatHistory(c, true).catch(() => false))).then((r) => {
      if (interrupted || r.some(Boolean)) toast(t("chat_ask_cancelled"), "info");
    });
  }

  async function submitQuestion(
    question: string,
    mode: RetrievalMode,
    appendUser: boolean,
    confirmed = false,
    scopeOverride?: AnswerScope,
  ) {
    if (loading) return;
    if (mode === "fulltext" && !fulltextDocId) {
      toast(t("chat_fulltext_requires_doc"), "info");
      return;
    }
    // 发送这一刻定下本轮范围：之后左侧 / 中间怎么切，这一轮都按它检索、按它标注。
    // 钉住了文档就按那一篇（各档位都是）；否则全文模式按打开的文档，其余按聊天所选集合。
    const scope: AnswerScope =
      scopeOverride ??
      (pinnedDoc
        ? { collection: "", doc_id: pinnedDoc.id, doc_title: pinnedDoc.title }
        : mode === "fulltext" && selectedDocId
          ? { collection: chatCollection ?? "", doc_id: selectedDocId, doc_title: docTitle }
          : { collection: chatCollection ?? "" });
    if (appendUser) {
      setInput("");
      setMessages((prev) => [...prev, { role: "user", content: question, scope }]);
    }
    setLoading(true);
    loadingRef.current = true;
    setPendingScope(scope);
    // 预生成 conversation_id：首条消息时前端也能在请求进行中轮询进度（否则拿不到 cid 无法轮询）。
    const cid =
      conversationId ??
      (typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : `conv-${Date.now()}`);
    // deep thinking / enhanced 有阶段 detail；进行中每 ~600ms 轮询，渲染实时推演视图。
    setLiveProgress(null);
    if (mode === "deep_thinking" || mode === "enhanced") {
      liveTimerRef.current = setInterval(async () => {
        const p = await getAskProgress(cid);
        if (p?.detail) setLiveProgress(p.detail);
      }, 600);
    }
    const stopPolling = () => {
      if (liveTimerRef.current) {
        clearInterval(liveTimerRef.current);
        liveTimerRef.current = null;
      }
    };
    const abort = new AbortController();
    askAbortsRef.current.set(abort, cid);
    let recovering = false;
    try {
      const result: AskResult = await ask({
        question,
        collection: scope.collection || null,
        // 全文模式读这篇；其他档位后端按这篇限定检索（图谱档位在单篇范围下不可选）。
        doc_id: scope.doc_id ?? null,
        top_k: 6,
        conversation_id: cid,
        persona_enabled: personaEnabled,
        retrieval_mode: mode,
        use_english_retrieval: useEnglishRetrieval,
        use_context: useContext,
        model_thinking: modelThinking,
        answer_language: answerLanguage,
        confirmed,
        signal: abort.signal,
      });
      if (abort.signal.aborted) return; // 清空聊天时答案恰好到达：已放弃，不再渲染
      setFullTextDialog(null);
      setConversationId(result.conversation_id);
      if (typeof window !== "undefined") localStorage.setItem(CONV_KEY, result.conversation_id);
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: result.answer,
          sources: result.sources,
          actualRetrievalMode: result.actual_retrieval_mode,
          thinkingTrace: result.thinking_trace ?? undefined,
          answerNotice: result.answer_notice || undefined,
          standaloneQuestion: result.standalone_question || undefined,
          scope: result.scope ?? scope,
        },
      ]);
    } catch (err) {
      // 被清空聊天中断（本页中止，或后端已取消任务）：不是错误，不提示。
      if (abort.signal.aborted || (err instanceof ApiError && err.body?.status === "ask_cancelled")) {
        return;
      }
      if (
        (mode === "graph_mixed" || mode === "graph_only") &&
        err instanceof ApiError &&
        (err.body?.status === "lightrag_not_ready" || err.body?.status === "graph_mixed_failed" || err.body?.status === "graph_only_failed") &&
        scope.collection
      ) {
        let estimate: GraphBuildEstimate | undefined;
        const canBuild = err.body.build_available === true;
        if (canBuild) {
          try { estimate = await estimateGraphBuild(scope.collection); } catch { /* ignore */ }
        }
        setGraphBuildDialog({ question, collection: scope.collection, scope, reason: err.message, estimate, canBuild });
      } else if (
        mode === "fulltext" &&
        err instanceof ApiError &&
        err.body?.status === "fulltext_confirmation_required"
      ) {
        // 409 = 后端在问「这篇有 N 字符，确定要整篇送进去吗」。此刻后端尚未调用 LLM、
        // 也没写任何 chat_history，用户确认后原样重发即可，不是续跑。
        setFullTextDialog({
          question,
          info: err.body as unknown as FullTextConfirmation,
        });
      } else if (err instanceof AskTaskTimeoutError || (err instanceof ApiError && err.timedOut)) {
        // 后端 task_timeout_seconds 到点，或前端自己先放弃等待：请求没有失败，任务仍在
        // 后台跑并会正常写库——转入补轮询找回迟到的答案，而不是弹错误后直接放弃。
        const recoverCid = err instanceof AskTaskTimeoutError ? err.conversationId : cid;
        toast(t("chat_ask_still_running"), "info");
        setConversationId(recoverCid);
        if (typeof window !== "undefined") localStorage.setItem(CONV_KEY, recoverCid);
        recovering = true;
        askAbortsRef.current.set(abort, recoverCid);
        waitForLateAskAnswer(recoverCid, { signal: abort.signal })
          .then((history) => {
            if (abort.signal.aborted) return;
            if (history) {
              setMessages(chatMessagesToMessages(history));
              toast(t("chat_ask_recovered"), "ok");
            } else {
              toast(t("chat_ask_recover_failed"), "info");
            }
          })
          .catch(() => {})
          .finally(() => askAbortsRef.current.delete(abort));
      } else {
        toast(err instanceof ApiError ? err.message : t("error_generic"), "error");
      }
    } finally {
      if (!recovering) askAbortsRef.current.delete(abort);
      // 被清空中断时 clearChat 已复位这些状态，且可能已开始下一次提问，不能再动它们。
      if (!abort.signal.aborted) {
        stopPolling();
        setLiveProgress(null);
        setLoading(false);
        loadingRef.current = false;
        setPendingScope(null);
      }
      // 已确认的那次重发无论成败都要收起确认框，否则失败时它会永远停在 loading 态。
      // 刚被 409 打开的（running 未置位）不受影响。
      setFullTextDialog((prev) => (prev?.running ? null : prev));
    }
  }

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || loading) return;
    notifyAskSent();
    await submitQuestion(input.trim(), retrievalMode, true);
  }

  async function handleGraphFallback() {
    if (!graphBuildDialog) return;
    const pending = graphBuildDialog;
    setGraphBuildDialog(null);
    await submitQuestion(pending.question, "default", false, false, pending.scope);
  }

  async function handleGraphBuild() {
    if (!graphBuildDialog?.estimate) return;
    const pending = graphBuildDialog;
    setGraphBuildDialog({ ...pending, building: true });
    try {
      await buildGraph(pending.collection);
      setGraphBuildDialog(null);
      toast(t("chat_build_started_file_panel"), "ok");
    } catch (err) {
      setGraphBuildDialog((cur) =>
        cur ? { ...cur, building: false, reason: err instanceof Error ? err.message : t("error_generic") } : cur,
      );
    }
  }

  // 范围标签显示的就是这次提问实际用的范围：进行中显示锁定的那一轮；空闲时是钉住的文档，
  // 或全文模式下的当前文档（标题，不是内部 ID），其余是聊天自己选的集合。
  const isGraphMode = retrievalMode === "graph_only" || retrievalMode === "graph_mixed";
  const shownScope: AnswerScope =
    loading && pendingScope
      ? pendingScope
      : pinnedDoc
        ? { collection: "", doc_id: pinnedDoc.id, doc_title: pinnedDoc.title }
        : retrievalMode === "fulltext" && selectedDocId
          ? { collection: chatCollection ?? "", doc_id: selectedDocId, doc_title: docTitle }
          : { collection: chatCollection ?? "" };
  const contextTone = shownScope.doc_id ? "info" : isGraphMode ? "violet" : "accent";
  const contextIcon = shownScope.doc_id ? "file" : isGraphMode ? "graph" : "folder";
  const contextLabel = scopeLabel(shownScope, t);
  const graphHint = pinnedDoc
    ? t("chat_graph_single_doc")
    : graphKnownNotReady
      ? t("chat_graph_not_ready", { collection: chatCollection ?? "" })
      : undefined;

  return (
    <section
      style={{
        width: width ?? "var(--chat-w)",
        flexShrink: 0,
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        background: "var(--surface)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-2xl)",
        boxShadow: "var(--shadow-card)",
        overflow: "hidden",
      }}
    >
      {graphBuildDialog && (
        <GraphBuildDialog
          state={graphBuildDialog}
          onBuild={handleGraphBuild}
          onFallback={handleGraphFallback}
          onCancel={() => setGraphBuildDialog(null)}
        />
      )}

      {fullTextDialog && (
        <FullTextConfirmDialog
          info={fullTextDialog.info}
          running={fullTextDialog.running}
          onConfirm={() => {
            setFullTextDialog((prev) => (prev ? { ...prev, running: true } : prev));
            void submitQuestion(fullTextDialog.question, "fulltext", false, true);
          }}
          onCancel={() => setFullTextDialog(null)}
        />
      )}

      {/* Header */}
      <header
        style={{
          height: 38,
          flexShrink: 0,
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "0 8px 0 13px",
          borderBottom: "1px solid var(--border)",
        }}
      >
        <Icon name="sparkle" size={15} style={{ color: "var(--accent)" }} />
        <span style={{ flex: 1, fontSize: 12.5, fontWeight: 650, color: "var(--heading)" }}>
          {t("panel_chat")}
        </span>
        <IconButton name="trash" label={t("chat_clear_history")} onClick={clearChat} />
      </header>

      {/* Messages */}
      <div ref={scrollRef} style={{ flex: 1, minHeight: 0, overflow: "auto", padding: "14px 12px" }}>
        {messages.length === 0 ? (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              gap: 8,
              textAlign: "center",
              color: "var(--fg-subtle)",
              fontSize: 12,
              padding: "40px 20px",
            }}
          >
            <Icon name="sparkle" size={28} style={{ color: "var(--accent)", opacity: 0.6 }} />
            <span>{t("ask_empty_title")}</span>
          </div>
        ) : (
          messages.map((msg, i) => (
            <MessageBubble
              key={i}
              msg={msg}
              onCite={(n) => handleCite(msg, n)}
              onPin={() => handlePin(i)}
              onSaveNote={() => handleSaveNote(msg)}
              onPushNotion={() => handlePushNotion(msg, i)}
              notionEnabled={notionEnabled && msg.role === "assistant"}
              showRewrite={showRewrite}
            />
          ))
        )}
        {loading && (
          <div
            style={{
              display: "flex",
              gap: 8,
              padding: "6px 0",
              color: "var(--fg-muted)",
              fontSize: 12,
              alignItems: "flex-start",
            }}
          >
            <span
              style={{
                width: 12,
                height: 12,
                border: "2px solid var(--accent)",
                borderTopColor: "transparent",
                borderRadius: "50%",
                animation: "spin 0.6s linear infinite",
                flexShrink: 0,
                marginTop: 1,
              }}
            />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ marginBottom: liveProgress ? 6 : 0 }}>{t("ask_thinking")}</div>
              {liveProgress && <LiveThinkingView detail={liveProgress} t={t} />}
            </div>
          </div>
        )}
      </div>

      {/* Composer */}
      <div
        style={{
          flexShrink: 0,
          padding: "8px 12px 12px",
          borderTop: "1px solid var(--border)",
        }}
      >
        {/* Context chip */}
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 7 }}>
          <span style={{ fontSize: 10.5, color: "var(--fg-subtle)" }}>{t("panel_scope")}</span>
          <button
            ref={scopeRef}
            type="button"
            data-testid="chat-scope"
            disabled={loading}
            aria-haspopup="listbox"
            aria-expanded={showScopeMenu}
            title={loading ? t("chat_scope_locked") : t("chat_scope_pick")}
            onClick={() => setShowScopeMenu((v) => !v)}
            style={{
              background: "transparent",
              border: "none",
              padding: 0,
              cursor: loading ? "default" : "pointer",
              minWidth: 0,
            }}
          >
            <Badge tone={contextTone}>
              <Icon name={loading ? "clock" : contextIcon} size={10} />{" "}
              <span
                title={contextLabel}
                style={{
                  maxWidth: 220,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  display: "inline-block",
                  verticalAlign: "bottom",
                }}
              >
                {contextLabel}
              </span>
              {!loading && <span style={{ marginLeft: 3, fontSize: 9 }}>▾</span>}
            </Badge>
          </button>
          <Popover
            open={showScopeMenu && !loading}
            anchorRef={scopeRef}
            side="top"
            align="start"
            gap={6}
            zIndex={Z.dropdown}
            onClose={() => setShowScopeMenu(false)}
          >
            <div
              role="listbox"
              style={{
                background: "var(--surface)",
                border: "1px solid var(--border)",
                borderRadius: 12,
                padding: 6,
                boxShadow: "var(--shadow-pop)",
                minWidth: 220,
                maxWidth: 320,
                maxHeight: 320,
                overflow: "auto",
              }}
            >
              {retrievalMode === "fulltext" && !pinnedDoc && (
                <div style={{ fontSize: 10.5, lineHeight: 1.5, color: "var(--fg-subtle)", padding: "4px 8px 6px" }}>
                  {t("chat_scope_fulltext_hint")}
                </div>
              )}
              {(selectedDocId || pinnedDoc) && (
                <div style={{ borderBottom: "1px solid var(--border)", paddingBottom: 4, marginBottom: 4 }}>
                  {pinnedDoc && pinnedDoc.id !== selectedDocId && (
                    <ScopeDocOption
                      label={t("chat_scope_pinned_doc")}
                      title={pinnedDoc.title || t("panel_scope_document")}
                      selected
                      onClick={() => setShowScopeMenu(false)}
                    />
                  )}
                  {selectedDocId && (
                    <ScopeDocOption
                      label={t("chat_scope_viewing_doc")}
                      title={docTitle || t("panel_scope_document")}
                      selected={pinnedDoc?.id === selectedDocId}
                      onClick={pickViewingDoc}
                    />
                  )}
                  <div style={{ fontSize: 10.5, lineHeight: 1.5, color: "var(--fg-subtle)", padding: "2px 8px 4px" }}>
                    {t("chat_scope_doc_hint")}
                  </div>
                </div>
              )}
              {[{ name: null as string | null, depth: 0 }, ...scopeCollections].map(({ name, depth }) => {
                const selected = !pinnedDoc && retrievalMode !== "fulltext" && chatCollection === name;
                return (
                  <button
                    key={name ?? "__all__"}
                    type="button"
                    role="option"
                    aria-selected={selected}
                    onClick={() => pickScope(name)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      width: "100%",
                      padding: "6px 8px",
                      paddingLeft: 8 + depth * 12,
                      borderRadius: "var(--radius-md)",
                      background: selected ? "var(--accent-soft)" : "transparent",
                      border: "none",
                      cursor: "pointer",
                      textAlign: "left",
                      fontSize: 12,
                      color: "var(--fg)",
                    }}
                  >
                    <Icon name="folder" size={11} />
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {name ?? t("panel_all_collections")}
                    </span>
                  </button>
                );
              })}
            </div>
          </Popover>
        </div>

        <form onSubmit={handleSend}>
          <div
            data-tour="ask"
            className={[
              "ask-card",
              chatCollection ? "ask-card--collection" : "",
              loading ? "ask-card--loading" : "",
            ].filter(Boolean).join(" ")}
            style={{ padding: 8 }}
          >
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSend(e as unknown as React.FormEvent);
                }
              }}
              placeholder={t("ask_placeholder")}
              rows={2}
              style={{
                width: "100%",
                resize: "none",
                border: "none",
                outline: "none",
                background: "transparent",
                fontSize: 12.5,
                lineHeight: 1.55,
                fontFamily: "var(--font-sans)",
                color: "var(--fg)",
                padding: "2px 4px",
                boxShadow: "none",
              }}
            />
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 4 }}>
              {/* Settings popover */}
              <div ref={settingsRef} style={{ position: "relative" }}>
                <IconButton
                  name="settings"
                  label={t("chat_settings_label")}
                  active={personaEnabled || retrievalMode !== DEFAULT_MODE || !modelThinking || showSettings}
                  onClick={() => setShowSettings((v) => !v)}
                />
                <Popover
                  open={showSettings}
                  anchorRef={settingsRef}
                  side="top"
                  align="start"
                  gap={8}
                  zIndex={Z.dropdown}
                  onClose={() => setShowSettings(false)}
                >
                  <div
                    style={{
                      background: "var(--surface)",
                      border: "1px solid var(--border)",
                      borderRadius: 12,
                      padding: "8px 6px",
                      boxShadow: "var(--shadow-pop)",
                      minWidth: 220,
                    }}
                  >
                    <div
                      style={{
                        fontSize: 10,
                        fontWeight: 700,
                        letterSpacing: ".06em",
                        textTransform: "uppercase",
                        color: "var(--fg-subtle)",
                        padding: "2px 8px 6px",
                      }}
                    >
                      {t("chat_settings_title")}
                    </div>
                    {/* Retrieval mode */}
                    <div style={{ padding: "4px 8px 6px" }}>
                      {(
                        [
                          { value: "default" as RetrievalMode,        label: t("chat_retrieval_semantic"), desc: t("chat_retrieval_semantic_desc"), disabled: false },
                          { value: "enhanced" as RetrievalMode,       label: t("chat_retrieval_enhanced"), desc: t("chat_retrieval_enhanced_desc"), disabled: false },
                          { value: "graph_only" as RetrievalMode,     label: t("chat_retrieval_graph"),    desc: t("chat_retrieval_graph_desc"),    disabled: !graphReady, hint: graphHint },
                          { value: "graph_mixed" as RetrievalMode, label: t("chat_retrieval_graph_mixed"), desc: t("chat_retrieval_graph_mixed_desc"), disabled: !graphReady, hint: graphHint },
                          { value: "deep_thinking" as RetrievalMode,  label: t("chat_retrieval_deep"),     desc: t("chat_retrieval_deep_desc"),     disabled: !chatCollection && !pinnedDoc },
                          { value: "fulltext" as RetrievalMode,       label: t("chat_retrieval_fulltext"), desc: t("chat_retrieval_fulltext_desc"), disabled: !fulltextDocId },
                        ] as { value: RetrievalMode; label: string; desc: string; disabled: boolean; hint?: string }[]
                      ).map(({ value, label, desc, disabled, hint }) => {
                        const selected = retrievalMode === value;
                        return (
                          <button
                            key={value}
                            type="button"
                            title={hint}
                            onClick={() => { if (!disabled) setRetrievalMode(value); }}
                            style={{
                              display: "flex",
                              alignItems: "center",
                              gap: 10,
                              width: "100%",
                              padding: "7px 8px",
                              borderRadius: "var(--radius-md)",
                              background: selected ? "var(--accent-soft)" : "transparent",
                              border: "none",
                              cursor: disabled ? "not-allowed" : "pointer",
                              textAlign: "left",
                              opacity: disabled ? 0.38 : 1,
                            }}
                          >
                            <span
                              style={{
                                width: 12,
                                height: 12,
                                borderRadius: "50%",
                                flexShrink: 0,
                                border: `2px solid ${selected ? "var(--accent)" : "var(--fg-subtle)"}`,
                                background: selected ? "var(--accent)" : "transparent",
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                              }}
                            >
                              {selected && (
                                <span
                                  style={{
                                    width: 4,
                                    height: 4,
                                    borderRadius: "50%",
                                    background: "white",
                                    display: "block",
                                  }}
                                />
                              )}
                            </span>
                            <span style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                              <span
                                style={{
                                  fontSize: 12,
                                  fontWeight: 600,
                                  color: selected ? "var(--accent)" : "var(--fg)",
                                  lineHeight: 1.2,
                                }}
                              >
                                {label}
                              </span>
                              <span style={{ fontSize: 10, color: "var(--fg-muted)", lineHeight: 1.3 }}>
                                {desc}
                              </span>
                            </span>
                          </button>
                        );
                      })}
                    </div>
                    <ToggleRow label={t("chat_use_english_retrieval")} on={useEnglishRetrieval} onToggle={toggleEnglishRetrieval} />
                    <ToggleRow
                      label={t("chat_use_context")}
                      hint={t("chat_use_context_hint")}
                      on={useContext}
                      onToggle={toggleUseContext}
                    />
                    {useContext && (
                      <ToggleRow label={t("chat_show_rewrite")} on={showRewrite} onToggle={toggleShowRewrite} />
                    )}
                    <ToggleRow
                      label={t("chat_model_thinking")}
                      hint={t("chat_model_thinking_hint")}
                      on={modelThinking}
                      onToggle={toggleModelThinking}
                    />
                    {/* Answer language */}
                    <div
                      style={{ padding: "6px 8px 4px", borderTop: "1px solid var(--border)" }}
                    >
                      <div
                        style={{
                          fontSize: 10,
                          fontWeight: 700,
                          color: "var(--fg-subtle)",
                          marginBottom: 5,
                          letterSpacing: ".05em",
                          textTransform: "uppercase",
                        }}
                      >
                        {t("chat_answer_language")}
                      </div>
                      <div style={{ display: "flex", gap: 4 }}>
                        {(["auto", "zh", "en"] as const).map((l) => (
                          <button
                            key={l}
                            type="button"
                            onClick={() => setAnswerLanguage(l)}
                            style={{
                              flex: 1,
                              padding: "4px 0",
                              fontSize: 11,
                              borderRadius: 6,
                              cursor: "pointer",
                              border: "1px solid var(--border)",
                              background: answerLanguage === l ? "var(--accent-soft)" : "var(--bg-inset)",
                              color: answerLanguage === l ? "var(--accent)" : "var(--fg-subtle)",
                              fontWeight: answerLanguage === l ? 600 : 400,
                              transition: "all .12s",
                            }}
                          >
                            {{ auto: t("chat_lang_auto"), zh: t("chat_lang_zh"), en: t("chat_lang_en") }[l]}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                </Popover>
              </div>

              <span style={{ flex: 1 }} />

              {/* Send */}
              <Button
                type="submit"
                variant="primary"
                size="sm"
                disabled={!input.trim() || loading}
                loading={loading}
                style={{ height: 28 }}
              >
                <Icon name="send" size={13} /> {t("ask_send")}
              </Button>
            </div>
          </div>
        </form>
      </div>
    </section>
  );
}
