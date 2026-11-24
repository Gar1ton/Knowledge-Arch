"use client";
/**
 * 3.0.0 推送到 Zotero：文档信息栏的「Zotero」一行（推送状态 + 「推送到 Zotero…」）与推送框。
 * 推送 = 移交：KA 把 PDF 移进 ZotMoov 的目录，再在 Zotero 里建一条独立附件；之后文件归 Zotero 管，
 * KA 的切块、向量和笔记保留，下次拉取时这篇变成 Zotero 镜像。
 */
import React, { useCallback, useEffect, useState } from "react";
import { Modal } from "@/components/ds/Modal";
import { Button } from "@/components/ds/Button";
import { Select } from "@/components/ds/Select";
import { useToast } from "@/components/ui/Toast";
import { useI18n } from "@/lib/i18n";
import { getPushPreview, pushDocument, type PushPreview, type PushRecord } from "@/lib/libraryIoApi";

type T = ReturnType<typeof useI18n>["t"];

function statusText(p: PushRecord, t: T): string {
  if (p.status === "claimed") return t("push_state_claimed", { key: p.attachment_key });
  if (p.status === "pushed") return t("push_state_pushed", { key: p.attachment_key });
  return t("push_state_failed", { msg: p.message });
}

/** 文档信息栏的一行：本地文档可推送；推送过的显示状态（失败可重试）。 */
export function ZoteroPushRow({ docId, local }: { docId: string; local: boolean }) {
  const { t } = useI18n();
  const [push, setPush] = useState<PushRecord | null>(null);
  const [open, setOpen] = useState(false);

  const reload = useCallback(() => {
    getPushPreview(docId).then((p) => setPush(p.push)).catch(() => setPush(null));
  }, [docId]);
  useEffect(reload, [reload]);

  if (!local && !push) return null;
  const canPush = local && (!push || push.status === "failed");
  return (
    <div style={{ display: "flex", gap: 10, padding: "3px 0", alignItems: "baseline" }}>
      <span style={{ width: 58, flexShrink: 0, fontSize: 11, color: "var(--fg-subtle)" }}>Zotero</span>
      <span style={{ flex: 1, fontSize: 11.5, lineHeight: 1.45, color: push?.status === "failed" ? "var(--danger)" : "var(--fg)", wordBreak: "break-word" }}>
        {push ? statusText(push, t) : t("push_state_none")}{" "}
        {canPush && (
          <button type="button" className="src-link" onClick={() => setOpen(true)}>
            {push ? t("push_retry") : t("push_open")}
          </button>
        )}
      </span>
      {open && <PushDialog docId={docId} onClose={(changed) => { setOpen(false); if (changed) reload(); }} />}
    </div>
  );
}

export function PushDialog({ docId, onClose }: { docId: string; onClose: (changed: boolean) => void }) {
  const { t } = useI18n();
  const { toast } = useToast();
  const [preview, setPreview] = useState<PushPreview | null>(null);
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    getPushPreview(docId, target)
      .then((p) => {
        if (!alive) return;
        setPreview(p);
        if (!target && p.targets.length) setTarget(p.push?.status === "failed"
          ? p.targets.find((x) => x.coll_key.endsWith(`:${p.push?.collection_key}`))?.coll_key ?? p.targets[0].coll_key
          : p.targets[0].coll_key);
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message : String(e)));
    return () => { alive = false; };
  }, [docId, target]);

  const run = async () => {
    if (!preview?.plan || busy) return;
    setBusy(true);
    try {
      const rec = await pushDocument(docId, target);
      if (rec.status === "failed") {
        toast(t("push_failed", { msg: rec.message }), "error");
      } else {
        toast(t("push_done", { key: rec.attachment_key }), "ok");
      }
      onClose(true);
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(false);
    }
  };

  const footer = (
    <>
      <Button variant="outline" size="sm" onClick={() => onClose(false)}>{t("btn_cancel")}</Button>
      <Button size="sm" onClick={() => void run()} disabled={!preview?.plan || busy} loading={busy}>
        {busy ? t("push_working") : t("push_go")}
      </Button>
    </>
  );
  return (
    <Modal title={t("push_title")} icon="cloud" width={600} height="auto" onClose={() => onClose(false)} footer={footer}>
      <div className="imp-body">
        {error && <div className="src-note src-note--error">{error}</div>}
        {!preview && !error && <div className="src-note">{t("panel_loading")}</div>}
        {preview && (
          <>
            <div className="imp-kv"><span>{t("push_collection")}</span>
              {preview.targets.length ? (
                <Select value={target} onChange={setTarget} size="md" style={{ width: "100%" }} disabled={busy}
                  options={preview.targets.map((c) => ({ value: c.coll_key, label: c.path }))} />
              ) : <span className="src-meta">{t("push_no_targets")}</span>}</div>
            {preview.plan && (
              <>
                <div className="imp-kv"><span>{t("push_dest")}</span><span className="mono imp-wrap">{preview.plan.dest}</span></div>
                <div className="imp-kv"><span>Zotero</span><span className="mono imp-wrap">{t("push_item", { path: preview.plan.path })}</span></div>
              </>
            )}
            {preview.problem && <div className="imp-warn">{preview.problem}</div>}
            <div className="imp-warn">{t("push_handoff")}</div>
          </>
        )}
      </div>
    </Modal>
  );
}
