"use client";
/**
 * 3.0.0 来源栏的「入库」框：把一条外部来源放进本地集合。
 * PDF 只从这次联网补充记下的链接里试（不调用模型）；找不到时记进文档栏的「待补 PDF」，
 * 并列出能在浏览器打开的链接，用户自己下载后在清单里补上。
 */
import React, { useEffect, useState } from "react";
import { Modal } from "@/components/ds/Modal";
import { Button } from "@/components/ds/Button";
import { Select } from "@/components/ds/Select";
import { useToast } from "@/components/ui/Toast";
import { openExternalUrl } from "@/lib/backend";
import { notifyDocumentsChanged } from "@/lib/documentEvents";
import type { ExternalSourceDetail } from "@/lib/externalApi";
import { useI18n } from "@/lib/i18n";
import {
  getImportPreview,
  importSource,
  notifyImportsChanged,
  type ImportPreview,
  type ImportRecord,
} from "@/lib/libraryIoApi";

export function ImportDialog({
  captureId,
  source,
  onClose,
}: {
  captureId: string;
  source: ExternalSourceDetail;
  onClose: (changed: boolean) => void;
}) {
  const { t } = useI18n();
  const { toast } = useToast();
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [error, setError] = useState("");
  const [target, setTarget] = useState("");
  const [filename, setFilename] = useState("");
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<ImportRecord | null>(null);

  useEffect(() => {
    let alive = true;
    getImportPreview(captureId, source.ord)
      .then((p) => {
        if (!alive) return;
        setPreview(p);
        setFilename(p.filename);
        setTarget(p.existing?.target || p.targets[0]?.coll_key || "");
        if (p.existing?.status === "pending_pdf") setPending(p.existing);
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => { alive = false; };
  }, [captureId, source.ord]);

  const imported = preview?.existing?.status === "imported" ? preview.existing : null;
  const canImport = !!preview && !busy && !!target && !!filename.trim() && !preview.in_library && !imported;

  const run = async () => {
    if (!canImport) return;
    setBusy(true);
    try {
      const record = await importSource(captureId, source.ord, target, filename);
      notifyImportsChanged();
      if (record.status === "imported") {
        notifyDocumentsChanged();
        toast(t("imp_done", { name: record.filename }), "ok");
        onClose(true);
        return;
      }
      setPending(record);
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  };

  const f = source.fields;
  const biblio = [(f.creators ?? []).join("; "), f.year || "n.d.", f.venue, f.identifier].filter(Boolean).join(" · ");
  const footer = (
    <>
      <Button variant="outline" size="sm" onClick={() => onClose(!!pending)}>
        {pending || imported ? t("imp_close") : t("btn_cancel")}
      </Button>
      {!imported && (
        <Button size="sm" onClick={() => void run()} disabled={!canImport} loading={busy}>
          {busy ? t("imp_working") : pending ? t("imp_retry") : t("imp_go")}
        </Button>
      )}
    </>
  );

  return (
    <Modal title={t("imp_title", { id: source.source_id })} icon="download" width={560} height="auto"
      onClose={() => onClose(!!pending)} footer={footer}>
      <div className="imp-body">
        {error && <div className="src-note src-note--error">{error}</div>}
        {!preview && !error && <div className="src-note">{t("panel_loading")}</div>}
        {preview && (
          <>
            <div className="imp-kv"><span>{t("imp_biblio")}</span>
              <span><b>{f.title || source.source_id}</b><br /><span className="src-meta">{biblio}</span></span></div>
            <div className="imp-kv"><span>{t("imp_origin")}</span>
              <span className="src-meta">{t("imp_origin_text", {
                q: clip(preview.source.question), id: source.source_id,
                tool: preview.source.tool, call: preview.source.call_label,
                where: preview.source.locator || "—",
              })}</span></div>
            {preview.in_library && (
              <div className="imp-warn">{t("imp_in_library", { title: preview.in_library.title })}</div>
            )}
            {imported && <div className="imp-ok">{t("imp_already", { name: imported.filename })}</div>}
            {!imported && !preview.in_library && (
              <>
                <div className="imp-kv"><span>PDF</span>
                  <span className="src-meta">{preview.candidates
                    ? t("imp_pdf_tries", { n: preview.candidates })
                    : t("imp_pdf_none")}</span></div>
                <div className="imp-kv"><span>{t("imp_target")}</span>
                  {preview.targets.length ? (
                    <Select value={target} onChange={setTarget} size="md" style={{ width: "100%" }}
                      disabled={busy}
                      options={preview.targets.map((c) => ({ value: c.coll_key, label: c.path }))} />
                  ) : <span className="src-meta">{t("imp_no_target")}</span>}</div>
                <div className="imp-kv"><span>{t("imp_filename")}</span>
                  <input className="imp-input" value={filename} disabled={busy}
                    onChange={(e) => setFilename(e.target.value)} aria-label={t("imp_filename")} /></div>
              </>
            )}
            {pending && <PendingLinks record={pending} t={t} />}
          </>
        )}
      </div>
    </Modal>
  );
}

type T = ReturnType<typeof useI18n>["t"];

/** 出处里的问题只显示开头（完整问题在来源栏里）。 */
export function clip(text: string, max = 48): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

/** 找不到 PDF 时：说明、能在浏览器打开的链接、试过的链接各自的失败原因。 */
export function PendingLinks({ record, t }: { record: ImportRecord; t: T }) {
  return (
    <div className="imp-pending">
      <div className="imp-warn">{t("imp_pending", { n: record.tried.length })}</div>
      {record.links.length > 0 && <div className="src-h">{t("imp_links")}</div>}
      {record.links.map((url) => (
        <button key={url} type="button" className="src-link imp-url" onClick={() => void openExternalUrl(url)}>
          {url}
        </button>
      ))}
      {record.tried.some((x) => x.error) && (
        <details className="imp-tried">
          <summary>{t("imp_tried", { n: record.tried.length })}</summary>
          {record.tried.map((x) => (
            <div key={x.url} className="src-meta"><span className="mono">{x.url}</span> — {x.error || "OK"}</div>
          ))}
        </details>
      )}
    </div>
  );
}
