"use client";

import React, { useEffect, useState } from "react";
import { useConsole } from "@/lib/ConsoleContext";
import { openExternalUrl } from "@/lib/backend";
import { useI18n } from "@/lib/i18n";
import type { ZoteroMeta } from "@/lib/api";
import { getImportOrigin, type ImportRecord } from "@/lib/libraryIoApi";
import { clip } from "@/components/panels/ImportDialog";

export type MetaDraft = Partial<ZoteroMeta> & { title?: string };

export function MetaRow({
  k,
  v,
  mono,
  link,
}: {
  k: string;
  v?: string | null;
  mono?: boolean;
  link?: boolean;
}) {
  if (!v) return null;
  return (
    <div style={{ display: "flex", gap: 10, padding: "3px 0" }}>
      <span style={{ width: 58, flexShrink: 0, fontSize: 11, color: "var(--fg-subtle)" }}>{k}</span>
      <span
        style={{
          flex: 1,
          fontSize: 11.5,
          color: link ? "var(--accent)" : "var(--fg)",
          fontFamily: mono ? "var(--font-mono)" : "var(--font-sans)",
          wordBreak: "break-word",
          lineHeight: 1.45,
        }}
      >
        {v}
      </span>
    </div>
  );
}

export function MetaEditForm({
  draft,
  saving,
  onChange,
  onSave,
  onCancel,
}: {
  draft: MetaDraft;
  saving: boolean;
  onChange: (d: MetaDraft) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const { t } = useI18n();
  const fieldStyle: React.CSSProperties = {
    width: "100%",
    fontSize: 12,
    fontFamily: "var(--font-sans)",
    color: "var(--fg)",
    background: "var(--bg-inset)",
    border: "1px solid var(--border-strong)",
    borderRadius: "var(--radius-sm)",
    padding: "4px 7px",
    outline: "none",
    boxSizing: "border-box",
  };
  const labelStyle: React.CSSProperties = {
    fontSize: 10.5,
    color: "var(--fg-subtle)",
    marginBottom: 3,
    display: "block",
  };
  const rowStyle: React.CSSProperties = { marginBottom: 8 };
  const creatorsValue = Array.isArray(draft.creators)
    ? draft.creators.join("\n")
    : (draft.creators as string | undefined) ?? "";

  return (
    <div
      style={{
        background: "var(--bg-inset)",
        border: "1px solid var(--border-strong)",
        borderRadius: "var(--radius-md)",
        padding: "10px 12px",
        marginBottom: 12,
      }}
    >
      <div style={rowStyle}>
        <label style={labelStyle}>{t("note_meta_title")}</label>
        <input
          style={fieldStyle}
          value={draft.title ?? ""}
          onChange={(e) => onChange({ ...draft, title: e.target.value })}
        />
      </div>
      <div style={rowStyle}>
        <label style={labelStyle}>{t("note_meta_authors")} ({t("note_meta_authors_hint")})</label>
        <textarea
          style={{ ...fieldStyle, resize: "none", lineHeight: 1.5 }}
          rows={3}
          value={creatorsValue}
          onChange={(e) => onChange({ ...draft, creators: e.target.value as unknown as string[] })}
        />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 8 }}>
        <div>
          <label style={labelStyle}>{t("note_meta_year")}</label>
          <input style={fieldStyle} value={draft.year ?? ""} onChange={(e) => onChange({ ...draft, year: e.target.value })} />
        </div>
        <div>
          <label style={labelStyle}>{t("note_meta_journal")}</label>
          <input style={fieldStyle} value={draft.venue ?? ""} onChange={(e) => onChange({ ...draft, venue: e.target.value })} />
        </div>
      </div>
      <div style={rowStyle}>
        <label style={labelStyle}>DOI</label>
        <input style={{ ...fieldStyle, fontFamily: "var(--font-mono)" }} value={draft.doi ?? ""} onChange={(e) => onChange({ ...draft, doi: e.target.value })} />
      </div>
      <div style={rowStyle}>
        <label style={labelStyle}>{t("documents_abstract")}</label>
        <textarea
          style={{ ...fieldStyle, resize: "none", lineHeight: 1.55 }}
          rows={4}
          value={draft.abstract ?? ""}
          onChange={(e) => onChange({ ...draft, abstract: e.target.value })}
        />
      </div>
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 6 }}>
        <button
          onClick={onCancel}
          style={{
            fontSize: 11, padding: "4px 10px", borderRadius: "var(--radius-pill)",
            border: "1px solid var(--border)", background: "transparent",
            color: "var(--fg-muted)", cursor: "pointer", fontFamily: "var(--font-sans)",
          }}
        >
          {t("btn_cancel")}
        </button>
        <button
          onClick={onSave}
          disabled={saving}
          style={{
            fontSize: 11, padding: "4px 10px", borderRadius: "var(--radius-pill)",
            border: "none", background: "var(--accent)", color: "var(--accent-fg)",
            cursor: saving ? "wait" : "pointer", fontWeight: 600, fontFamily: "var(--font-sans)",
            opacity: saving ? 0.7 : 1,
          }}
        >
          {t("btn_save")}
        </button>
      </div>
    </div>
  );
}

/**
 * 3.0.0：从联网补充入库的文档，显示出处（哪个回答、哪条来源、哪次工具调用）与下载 PDF 的链接；
 * 「打开来源栏」回到当时的来源栏。不是入库来的文档什么也不显示。
 */
export function ImportOriginRows({ docId }: { docId: string }) {
  const { t } = useI18n();
  const { setSourcePanel } = useConsole();
  const [origin, setOrigin] = useState<ImportRecord | null>(null);

  useEffect(() => {
    let alive = true;
    getImportOrigin(docId).then((r) => alive && setOrigin(r)).catch(() => alive && setOrigin(null));
    return () => { alive = false; };
  }, [docId]);

  if (!origin) return null;
  const s = origin.source;
  const where = [s.tool, s.call_label, s.locator].filter(Boolean).join(" · ");
  return (
    <>
      <div style={{ display: "flex", gap: 10, padding: "3px 0" }}>
        <span style={{ width: 58, flexShrink: 0, fontSize: 11, color: "var(--fg-subtle)" }}>{t("imp_origin")}</span>
        <span style={{ flex: 1, fontSize: 11.5, lineHeight: 1.45, color: "var(--fg)", wordBreak: "break-word" }}>
          {t("imp_origin_doc", { q: clip(s.question), id: origin.source_id, where })}{" "}
          <button type="button" className="src-link"
            onClick={() => setSourcePanel({ captureId: origin.capture_id, focus: origin.source_id })}>
            {t("imp_open_sources", { id: origin.source_id })}
          </button>
        </span>
      </div>
      {origin.pdf_url && (
        <div style={{ display: "flex", gap: 10, padding: "3px 0" }}>
          <span style={{ width: 58, flexShrink: 0, fontSize: 11, color: "var(--fg-subtle)" }}>PDF</span>
          <button type="button" className="src-link imp-url" title={origin.pdf_url}
            onClick={() => void openExternalUrl(origin.pdf_url)}>{origin.pdf_url}</button>
        </div>
      )}
    </>
  );
}
