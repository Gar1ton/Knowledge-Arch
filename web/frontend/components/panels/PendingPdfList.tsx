"use client";
/**
 * 3.0.0 文档栏顶部的「待补 PDF」清单：入库时没找到可下载 PDF 的外部论文。
 * 每条给出能在浏览器打开的链接（DOI 链接、原文页、试过的链接），用户自己下载后
 * 选文件或拖进这一行补上，按入库时定好的文件名与集合入库。没有待补的就不显示。
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ds/Button";
import { IconButton } from "@/components/ds/IconButton";
import { useToast } from "@/components/ui/Toast";
import { useConsole } from "@/lib/ConsoleContext";
import { openExternalUrl } from "@/lib/backend";
import { DOCUMENTS_CHANGED_EVENT, notifyDocumentsChanged } from "@/lib/documentEvents";
import { useI18n } from "@/lib/i18n";
import {
  IMPORTS_CHANGED_EVENT,
  dismissImport,
  listImports,
  notifyImportsChanged,
  uploadImportPdf,
  type ImportRecord,
} from "@/lib/libraryIoApi";

export function PendingPdfList() {
  const { t } = useI18n();
  const [rows, setRows] = useState<ImportRecord[]>([]);
  const [open, setOpen] = useState(true);

  const reload = useCallback(() => {
    listImports("pending_pdf").then(setRows).catch(() => setRows([]));
  }, []);

  useEffect(() => {
    reload();
    window.addEventListener(IMPORTS_CHANGED_EVENT, reload);
    window.addEventListener(DOCUMENTS_CHANGED_EVENT, reload);
    return () => {
      window.removeEventListener(IMPORTS_CHANGED_EVENT, reload);
      window.removeEventListener(DOCUMENTS_CHANGED_EVENT, reload);
    };
  }, [reload]);

  if (!rows.length) return null;
  return (
    <section className="imp-list" aria-label={t("imp_pending_title", { n: rows.length })}>
      <button type="button" className="imp-list-head" onClick={() => setOpen((v) => !v)}
        aria-expanded={open}>
        {open ? "▾" : "▸"} {t("imp_pending_title", { n: rows.length })}
        <span className="src-meta">{t("imp_pending_hint")}</span>
      </button>
      {open && rows.map((r) => <PendingRow key={r.import_id} r={r} onChanged={reload} />)}
    </section>
  );
}

function PendingRow({ r, onChanged }: { r: ImportRecord; onChanged: () => void }) {
  const { t } = useI18n();
  const { toast } = useToast();
  const { setSourcePanel } = useConsole();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);

  const attach = async (file: File | undefined) => {
    if (!file || busy) return;
    setBusy(true);
    try {
      const done = await uploadImportPdf(r.import_id, file);
      toast(t("imp_done", { name: done.filename }), "ok");
      notifyDocumentsChanged();
      notifyImportsChanged();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  };
  const dismiss = async () => {
    try {
      await dismissImport(r.import_id);
      onChanged();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    }
  };

  return (
    <div className={`imp-row ${over ? "over" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); void attach(e.dataTransfer.files[0]); }}>
      <div className="imp-row-main">
        <b>{r.filename}</b>
        <span className="src-meta">
          {" "}→ {r.target_name || r.target}{" · "}
          <button type="button" className="src-link"
            onClick={() => setSourcePanel({ captureId: r.capture_id, focus: r.source_id })}>
            {t("imp_open_sources", { id: r.source_id })}
          </button>
        </span>
      </div>
      <div className="imp-row-links">
        {r.links.map((url) => (
          <button key={url} type="button" className="src-link imp-url" title={url}
            onClick={() => void openExternalUrl(url)}>{url}</button>
        ))}
      </div>
      <div className="imp-row-acts">
        <input ref={input} type="file" accept="application/pdf,.pdf" hidden
          onChange={(e) => { void attach(e.target.files?.[0]); e.target.value = ""; }} />
        <Button size="sm" variant="outline" loading={busy} onClick={() => input.current?.click()}>
          {t("imp_pick_pdf")}
        </Button>
        <span className="src-meta">{t("imp_drop_hint")}</span>
        <span style={{ flex: 1 }} />
        <IconButton name="trash" label={t("imp_dismiss")} onClick={() => void dismiss()} />
      </div>
    </div>
  );
}
