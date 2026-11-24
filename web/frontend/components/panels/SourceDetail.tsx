"use client";
/**
 * 来源栏右侧详情（`SourcesPanel` 组合）：选中的来源或调用。
 *
 * 原文块按我们自己工具的「标签: 值」格式排成字段表（`lib/sourceBlocks.parseRecordBlock`），摘要单独成段；
 * 被引用的那块默认展开、引用的句子高亮，其余块收起只显示标题。「看原文」显示工具返回的原始文字，逐字核对用。
 */
import React, { useState } from "react";
import { Button } from "@/components/ds/Button";
import { openExternalUrl } from "@/lib/backend";
import { authorsLabel, type ExternalSourceDetail, type ToolBlock, type ToolCallRecord } from "@/lib/externalApi";
import type { I18nKey } from "@/lib/i18n";
import { blockTitle, findPassage, parseRecordBlock } from "@/lib/sourceBlocks";
import { callArgument, type T } from "./SourcesViews";

// 字段标签是我们工具里写死的英文短词；界面上换成当前语言（没登记的照原样显示）。
const FIELD_KEYS: Record<string, I18nKey> = {
  Title: "src_field_title",
  Authors: "src_field_authors",
  Year: "src_field_year",
  Venue: "src_field_venue",
  Type: "src_field_type",
  Publisher: "src_field_publisher",
  "Journal reference": "src_field_journal_reference",
  URL: "src_field_url",
  "Open-access PDF": "src_field_open_access_pdf",
  "Cited by": "src_field_cited_by",
  Retracted: "src_field_retracted",
};

function linkOf(label: string, value: string): string | null {
  if (label === "DOI") return `https://doi.org/${value}`;
  if (/^https?:\/\//.test(value)) return value;
  return null;
}

function Highlighted({ text, passage }: { text: string; passage: string }) {
  const hit = passage ? findPassage(text, passage) : null;
  if (!hit) return <>{text}</>;
  return <>{hit[0]}<mark className="src-mark">{hit[1]}</mark>{hit[2]}</>;
}

function BlockBody({ block, passage, t }: { block: ToolBlock; passage: string; t: T }) {
  const [raw, setRaw] = useState(false);
  const rec = parseRecordBlock(block.text);
  return (
    <div className="src-block-body">
      {rec ? (
        <>
          <div className="src-kv">
            {rec.fields.map((f) => {
              const href = linkOf(f.label, f.value);
              return (
                <React.Fragment key={f.label}>
                  <span className="src-kv-k">{FIELD_KEYS[f.label] ? t(FIELD_KEYS[f.label]) : f.label}</span>
                  {href ? (
                    <button type="button" className="src-link src-kv-v" onClick={() => void openExternalUrl(href)}>{f.value}</button>
                  ) : <span className="src-kv-v">{f.value}</span>}
                </React.Fragment>
              );
            })}
          </div>
          {rec.abstract && <p className="src-abstract"><Highlighted text={rec.abstract} passage={passage} /></p>}
        </>
      ) : (
        <p className="src-abstract"><Highlighted text={block.text} passage={passage} /></p>
      )}
      {raw && <pre className="src-raw">{block.text}</pre>}
      <button type="button" className="src-link src-raw-toggle" onClick={() => setRaw((v) => !v)}>
        {raw ? t("src_raw_hide") : t("src_raw_show")}
      </button>
    </div>
  );
}

function BlockList({ call, hit, t }: {
  call: ToolCallRecord; hit: { index: number; passage: string; id: string } | null; t: T;
}) {
  const [open, setOpen] = useState<Record<number, boolean>>({});
  if (call.is_error) {
    return <div className="src-block is-error"><p className="src-abstract">{call.blocks[0]?.text ?? ""}</p></div>;
  }
  return (
    <>
      {call.blocks.map((b, i) => {
        const isHit = hit?.index === i;
        const expanded = open[i] ?? isHit;
        return (
          <div key={i} className={`src-block ${isHit ? "is-hit" : ""}`}>
            <button type="button" className="src-block-head" aria-expanded={expanded}
              onClick={() => setOpen((cur) => ({ ...cur, [i]: !expanded }))}>
              <span className={`src-chev ${expanded ? "is-open" : ""}`} aria-hidden="true">›</span>
              <span className="src-block-n">{b.locator || t("src_block_n", { n: i + 1 })}</span>
              <span className="src-block-title">{blockTitle(b.text)}</span>
              {isHit && <span className="src-id is-cited">{hit.id}</span>}
            </button>
            {expanded && <BlockBody block={b} passage={isHit ? hit.passage : ""} t={t} />}
          </div>
        );
      })}
    </>
  );
}

export function SourceDetail({ s, call, t, onImport }: {
  s: ExternalSourceDetail; call?: ToolCallRecord; t: T; onImport: () => void;
}) {
  const meta = [authorsLabel(s), s.fields.year || "n.d.", s.fields.venue].filter(Boolean).join(" · ");
  const placed = s.placement?.path || (s.placement?.new_direction ? t("src_placement_new", { name: s.placement.new_direction }) : "");
  return (
    <div className="src-det">
      <h3 className="src-det-title">{s.fields.title || s.source_id}</h3>
      <div className="src-det-meta">{meta}</div>
      {(s.fields.creators ?? []).length > 3 && <div className="src-det-meta">{(s.fields.creators ?? []).join("; ")}</div>}
      <div className="src-det-actions">
        <Button size="sm" variant={s.import?.status === "imported" ? "outline" : "primary"} onClick={onImport}>
          {s.import?.status === "imported" ? t("imp_view") : t("src_import")}
        </Button>
        {s.fields.url && (
          <Button size="sm" variant="outline" onClick={() => void openExternalUrl(s.fields.url ?? "")}>{t("src_open_link")}</Button>
        )}
      </div>

      <div className="src-sec">{t("src_section_passage")}</div>
      <blockquote className="src-quote">{s.passage}</blockquote>

      <div className="src-sec">{t("src_section_placement")}</div>
      {placed ? (
        <div className="src-place">
          <span className="src-chip">{placed}</span>
          {s.placement?.reason && <span className="src-det-meta">{s.placement.reason}</span>}
        </div>
      ) : <div className="src-det-meta">{t("src_not_placed")}</div>}

      {call && (
        <>
          <div className="src-sec">{t("src_section_origin", { call: call.label, tool: call.tool, arg: callArgument(call) })}</div>
          <BlockList key={s.source_id} call={call} hit={{ index: s.block, passage: s.passage, id: s.source_id }} t={t} />
        </>
      )}
    </div>
  );
}

export function CallDetail({ c, t }: { c: ToolCallRecord; t: T }) {
  return (
    <div className="src-det">
      <h3 className="src-det-title mono">{c.label} · {c.tool}</h3>
      <div className="src-det-meta">
        {new Date(c.started_at).toLocaleString()} · {c.is_error ? t("src_call_error") : t("src_call_meta", { n: c.blocks.length, secs: (c.elapsed_ms / 1000).toFixed(1) })}
      </div>
      <div className="src-sec">{t("src_section_args")}</div>
      <div className="src-kv">
        {Object.entries(c.arguments).map(([k, v]) => (
          <React.Fragment key={k}>
            <span className="src-kv-k mono">{k}</span>
            <span className="src-kv-v">{typeof v === "string" ? v : JSON.stringify(v)}</span>
          </React.Fragment>
        ))}
      </div>
      <div className="src-sec">{t("src_section_blocks")}</div>
      <BlockList key={c.call_id} call={c} hit={null} t={t} />
    </div>
  );
}
