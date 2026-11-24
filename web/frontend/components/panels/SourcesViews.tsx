"use client";
/**
 * 来源栏左侧的三种视图（`SourcesPanel` 组合）。每种只讲一层关系：
 * - 按来源：回答引用的 / 登记了但正文没用到，两组；卡片三层（编号 + 标题 / 作者 · 年份 · 期刊 / 出处）。
 * - 按检索过程：调用按顺序排，每次调用下面是它被引用的块。
 * - 按我的集合：完整的集合树，每个集合下列出归到这里的论文与模型给的理由；「新方向」单独一组。
 */
import React from "react";
import { Icon } from "@/components/ds/Icon";
import { authorsLabel, type ExternalCapture, type ExternalSourceDetail, type ToolCallRecord } from "@/lib/externalApi";
import type { I18nKey } from "@/lib/i18n";
import type { CollectionNode } from "@/lib/sourceBlocks";

export type T = (key: I18nKey, vars?: Record<string, string | number>) => string;

function byline(s: ExternalSourceDetail): string {
  return [authorsLabel(s), s.fields.year || "n.d.", s.fields.venue].filter(Boolean).join(" · ");
}

export function SourceCard({ s, selected, onSelect, t }: {
  s: ExternalSourceDetail; selected: boolean; onSelect: () => void; t: T;
}) {
  return (
    <div className={`src-card ${selected ? "is-on" : ""}`} role="button" tabIndex={0} aria-pressed={selected}
      onClick={onSelect} onKeyDown={(e) => { if (e.key === "Enter") onSelect(); }}>
      <div className="src-card-title">
        <span className={`src-id ${s.cited ? "is-cited" : ""}`}>{s.source_id}</span>
        <span className="src-card-name">{s.fields.title || s.source_id}</span>
      </div>
      <div className="src-card-by">{byline(s)}</div>
      <div className="src-card-origin">
        <Icon name="link" size={11} />
        {t("src_origin_line", { tool: s.tool, call: s.call_label, n: s.block + 1 })}
        {s.import && <span className={`src-pill ${s.import.status === "imported" ? "is-info" : "is-warn"}`}>
          {s.import.status === "imported" ? t("imp_state_imported") : t("imp_state_pending")}
        </span>}
      </div>
    </div>
  );
}

function Group({ label, n }: { label: string; n: number }) {
  return <div className="src-group"><span>{label}</span><span>{n}</span></div>;
}

export function SourceList({ data, selected, onSelect, t }: {
  data: ExternalCapture; selected: string | null; onSelect: (id: string) => void; t: T;
}) {
  const cited = data.sources.filter((s) => s.cited);
  const rest = data.sources.filter((s) => !s.cited);
  const cards = (list: ExternalSourceDetail[]) => list.map((s) => (
    <SourceCard key={s.source_id} s={s} t={t} selected={selected === s.source_id} onSelect={() => onSelect(s.source_id)} />
  ));
  return (
    <>
      <Group label={t("src_group_cited")} n={cited.length} />
      {cards(cited)}
      {rest.length > 0 && <Group label={t("src_group_uncited")} n={rest.length} />}
      {cards(rest)}
    </>
  );
}

// 模型登记引用用的工具（后端 kacore/domain/tool_calls.py 的 CITE_TOOL）：不是检索，单独成组。
export const CITE_TOOL = "cite";

export function callArgument(c: ToolCallRecord): string {
  const a = c.arguments;
  const main = a.query ?? a.url ?? a.id ?? Object.values(a)[0];
  return typeof main === "string" ? main : JSON.stringify(main ?? "");
}

export function CallList({ data, selected, onSelect, onSelectSource, t }: {
  data: ExternalCapture; selected: string | null; onSelect: (id: string) => void;
  onSelectSource: (id: string) => void; t: T;
}) {
  const searches = data.calls.filter((c) => c.tool !== CITE_TOOL);
  const cites = data.calls.filter((c) => c.tool === CITE_TOOL);
  const select = (c: ToolCallRecord) => () => onSelect(c.call_id);
  return (
    <>
      <div className="src-hint">{t("src_calls_hint")}</div>
      <Group label={t("src_group_search_calls")} n={searches.length} />
      {searches.map((c) => {
        const hits = data.sources.filter((s) => s.call_id === c.call_id).sort((a, b) => a.block - b.block);
        return (
          <div key={c.call_id} className={`src-card ${selected === c.call_id ? "is-on" : ""}`} role="button" tabIndex={0}
            aria-pressed={selected === c.call_id}
            onClick={select(c)} onKeyDown={(e) => { if (e.key === "Enter") onSelect(c.call_id); }}>
            <div className="src-card-title">
              <span className={`src-id ${c.is_error ? "is-warn" : ""}`}>{c.label}</span>
              <span className="src-card-name mono">{c.tool}</span>
              <span className="src-card-side">{c.is_error ? t("src_call_error") : t("src_call_meta", { n: c.blocks.length, secs: (c.elapsed_ms / 1000).toFixed(1) })}</span>
            </div>
            <div className="src-card-by">“{callArgument(c)}”</div>
            <div className="src-card-origin">
              {hits.length === 0 ? t("src_call_no_hits") : hits.map((h) => (
                <button key={h.source_id} type="button" className="src-hit"
                  onClick={(e) => { e.stopPropagation(); onSelectSource(h.source_id); }}>
                  <span className="src-id is-cited">{h.source_id}</span>{t("src_block_n", { n: h.block + 1 })}
                </button>
              ))}
            </div>
          </div>
        );
      })}
      {cites.length > 0 && <Group label={t("src_group_cite_calls")} n={cites.length} />}
      {cites.map((c) => {
        const title = typeof c.arguments.title === "string" ? c.arguments.title : "";
        const passage = typeof c.arguments.passage === "string" ? c.arguments.passage : "";
        return (
          <div key={c.call_id} className={`src-card ${selected === c.call_id ? "is-on" : ""}`} role="button" tabIndex={0}
            aria-pressed={selected === c.call_id}
            onClick={select(c)} onKeyDown={(e) => { if (e.key === "Enter") onSelect(c.call_id); }}>
            <div className="src-card-title">
              <span className={`src-id ${c.is_error ? "is-warn" : "is-cited"}`}>{c.label}</span>
              <span className="src-card-name">{title || passage.slice(0, 90) || String(c.arguments.call ?? "")}</span>
            </div>
            {/* 登记结果是账本写下的原话（接受为 E 几 / 没通过及原因）。 */}
            <div className={`src-card-by ${c.is_error ? "is-warn" : ""}`}>{c.blocks[0]?.text ?? ""}</div>
          </div>
        );
      })}
    </>
  );
}

function placedUnder(s: ExternalSourceDetail, path: string): boolean {
  const p = s.placement?.path ?? "";
  return p === path || p.startsWith(`${path} › `);
}

export function CollectionView({ data, nodes, placing, onSelectSource, t }: {
  data: ExternalCapture; nodes: CollectionNode[] | null; placing: boolean;
  onSelectSource: (id: string) => void; t: T;
}) {
  const known = new Set((nodes ?? []).map((n) => n.path));
  // 归到的集合后来改名或删掉了：路径对不上树，单独列出，不丢。
  const orphanPaths = [...new Set(data.sources.map((s) => s.placement?.path ?? "").filter((p) => p && !known.has(p)))];
  const news = [...new Set(data.sources.map((s) => s.placement?.new_direction ?? "").filter(Boolean))];
  const unplaced = data.sources.filter((s) => !s.placement?.path && !s.placement?.new_direction);
  const papers = (list: ExternalSourceDetail[], indent: number) => list.map((s) => (
    <div key={s.source_id} className="src-tree-paper" style={{ paddingLeft: indent }}>
      <button type="button" className="src-tree-link" onClick={() => onSelectSource(s.source_id)}>
        <span className={`src-id ${s.cited ? "is-cited" : ""}`}>{s.source_id}</span>{s.fields.title || s.source_id}
      </button>
      {s.placement?.reason && <div className="src-tree-reason">{s.placement.reason}</div>}
    </div>
  ));
  const node = (key: string, label: string, depth: number, count: number, here: ExternalSourceDetail[], icon: "folder" | "sparkle") => (
    <React.Fragment key={key}>
      <div className={`src-tree-node ${count ? "" : "is-empty"}`} style={{ paddingLeft: 12 + depth * 16 }}>
        <Icon name={icon} size={13} />
        <span className="src-tree-name">{label}</span>
        <span className="src-tree-count">{count || "—"}</span>
      </div>
      {papers(here, 34 + depth * 16)}
    </React.Fragment>
  );
  return (
    <div className="src-tree">
      <div className="src-hint">{t("src_tree_hint")}</div>
      {placing && <div className="src-note">{t("src_tree_placing")}</div>}
      {!nodes && !placing && <div className="src-note">{t("src_loading")}</div>}
      {(nodes ?? []).map((n) => node(n.key, n.name, n.depth,
        data.sources.filter((s) => placedUnder(s, n.path)).length,
        data.sources.filter((s) => s.placement?.path === n.path), "folder"))}
      {orphanPaths.map((p) => node(`orphan:${p}`, p, 0,
        data.sources.filter((s) => s.placement?.path === p).length,
        data.sources.filter((s) => s.placement?.path === p), "folder"))}
      <div className="src-group"><span>{t("src_tree_new")}</span><span>{news.length}</span></div>
      {news.map((name) => node(`new:${name}`, name, 0,
        data.sources.filter((s) => s.placement?.new_direction === name).length,
        data.sources.filter((s) => s.placement?.new_direction === name), "sparkle"))}
      {!placing && unplaced.length > 0 && (
        <>
          <div className="src-group"><span>{t("src_tree_none")}</span><span>{unplaced.length}</span></div>
          {papers(unplaced, 12)}
        </>
      )}
    </div>
  );
}
