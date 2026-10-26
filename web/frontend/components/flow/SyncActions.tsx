"use client";
/**
 * Notion 与 R2 节点的操作区（原「设置 › 同步/备份」页的动作，迁到各自的数据流节点里）。
 *
 * - Notion：初始化数据库（按父页面建库并回填 ID）、立即推送、推送账本状态；
 * - R2：存储用量与最近快照、立即备份 / 强制全量、从最新快照恢复（确认后重启服务换入）。
 * 后台任务的进度仍由左下角统一进度条呈现。
 */
import React, { useEffect, useState } from "react";
import { Button } from "@/components/ds/Button";
import { Modal } from "@/components/ds/Modal";
import {
  backupNow,
  getNotionPushStatus,
  getR2Job,
  getR2Status,
  notionInit,
  restoreBackup,
  syncDocuments,
  type NotionPushStatus,
  type PipelineStage,
  type R2BackupJob,
  type R2Status,
} from "@/lib/api";
import type { TFunc } from "@/lib/i18n";

type Notify = (message: string, kind: "ok" | "error" | "info") => void;

export function formatBytes(value: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let amount = Math.max(0, value);
  let index = 0;
  while (amount >= 1024 && index < units.length - 1) {
    amount /= 1024;
    index += 1;
  }
  return `${index === 0 ? amount.toFixed(0) : amount.toFixed(2)} ${units[index]}`;
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof Error ? err.message : fallback;
}

export function NotionActions({
  stage, t, onNotify, onChanged,
}: { stage: PipelineStage; t: TFunc; onNotify: Notify; onChanged: () => void }) {
  const enabled = stage.current === "on";
  const ready = stage.detail.database_ready === true;
  const [busy, setBusy] = useState<"init" | "push" | null>(null);
  const [status, setStatus] = useState<NotionPushStatus | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const refresh = () => getNotionPushStatus().then((s) => { if (alive) setStatus(s); }).catch(() => undefined);
    void refresh();
    const timer = window.setInterval(refresh, 8000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [enabled]);

  const init = async () => {
    setBusy("init");
    try {
      const res = await notionInit("", "");
      if (res.status === "success") {
        const recreated = res.recreated_articles || res.recreated_qa;
        onNotify(t(recreated ? "toast_notion_init_recreated" : "toast_notion_init_done"), recreated ? "info" : "ok");
        onChanged();
      }
      else onNotify(res.message || t("toast_init_failed"), "error");
    } catch (err) {
      onNotify(errorText(err, t("toast_init_failed")), "error");
    } finally {
      setBusy(null);
    }
  };

  const push = async () => {
    setBusy("push");
    try {
      const res = await syncDocuments("notion");
      if ("reserved" in res || res.status === "error") onNotify(t("toast_notion_push_start_failed"), "error");
      else onNotify(t("toast_notion_push_started"), "ok");
    } catch (err) {
      onNotify(errorText(err, t("toast_push_failed")), "error");
    } finally {
      setBusy(null);
    }
  };

  if (!enabled) return null;
  return (
    <div className="flow-node-actions" onClick={(e) => e.stopPropagation()}>
      {stage.detail.token_present !== true && <div className="flow-quick-hint">{t("notion_token_missing")}</div>}
      <div className="flow-provider-actions">
        <button type="button" className="flow-dep-button" disabled={busy !== null} onClick={() => void init()}>
          {busy === "init" ? t("prov_working") : t("notion_btn_init")}
        </button>
        <button type="button" className="flow-dep-button" disabled={busy !== null || !ready} onClick={() => void push()}>
          {busy === "push" ? t("prov_working") : t("sync_btn_sync_now")}
        </button>
      </div>
      {status && (
        <div className="flow-meta-strip">
          <span className="flow-meta-chip">{t("notion_documents_failed")}: {status.documents.failed ?? 0}</span>
          <span className="flow-meta-chip">{t("notion_qa_pending")}: {status.outbox_pending}</span>
          {status.errors.slice(0, 2).map((e) => (
            <span key={e.message} className="flow-meta-chip is-error" title={e.message}>{e.count} × {e.message}</span>
          ))}
        </div>
      )}
    </div>
  );
}

export function R2Actions({
  stage, t, onNotify,
}: { stage: PipelineStage; t: TFunc; onNotify: Notify }) {
  const enabled = stage.current === "on";
  const [status, setStatus] = useState<R2Status | null>(null);
  const [job, setJob] = useState<R2BackupJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmRestore, setConfirmRestore] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const refresh = async () => {
      const s = await getR2Status().catch(() => null);
      if (alive && s) { setStatus(s); setJob(s.job ?? null); }
    };
    void refresh();
    const timer = window.setInterval(async () => {
      const j = await getR2Job().catch(() => null);
      if (!alive) return;
      setJob(j);
      if (!j || j.status !== "running") void refresh();
    }, 3000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [enabled]);

  const backup = async (force: boolean) => {
    setBusy(true);
    try {
      await backupNow(force);
      onNotify(force ? t("toast_backup_forced_started") : t("toast_backup_incremental_started"), "ok");
      setJob(await getR2Job().catch(() => null));
    } catch (err) {
      onNotify(errorText(err, t("toast_backup_failed")), "error");
    } finally {
      setBusy(false);
    }
  };

  const restore = async () => {
    setConfirmRestore(false);
    setBusy(true);
    try {
      await restoreBackup(true);
      onNotify(t("toast_restore_started"), "ok");
    } catch (err) {
      onNotify(errorText(err, t("toast_restore_failed")), "error");
    } finally {
      setBusy(false);
    }
  };

  if (!enabled) return null;
  const running = job?.status === "running";
  return (
    <div className="flow-node-actions" onClick={(e) => e.stopPropagation()}>
      <div className="flow-meta-strip">
        <span className="flow-meta-chip">
          {formatBytes(status?.plugin_used_bytes ?? 0)} · {status?.plugin_object_count ?? 0} objects
        </span>
        <span className="flow-meta-chip" title={status?.snapshot?.snapshot_id}>
          {status?.snapshot ? `${status.snapshot.file_count} files · ${status.snapshot.updated_at}` : t("r2_no_snapshot")}
        </span>
        {running && <span className="flow-meta-chip">{job?.stage} {job?.progress}%</span>}
        {job?.status === "error" && <span className="flow-meta-chip is-error">{job.error || t("r2_job_failed")}</span>}
      </div>
      <div className="flow-provider-actions">
        <button type="button" className="flow-dep-button" disabled={busy || running} onClick={() => void backup(false)}>
          {t("r2_btn_backup_now")}
        </button>
        <button type="button" className="flow-quick-advanced-toggle" disabled={busy || running} onClick={() => void backup(true)}>
          {t("r2_btn_backup_force")}
        </button>
        <button type="button" className="flow-quick-advanced-toggle" disabled={busy || running} onClick={() => setConfirmRestore(true)}>
          {t("r2_btn_restore")}
        </button>
      </div>
      {confirmRestore && (
        <Modal
          title={t("r2_restore_title")}
          icon="cloud"
          width={500}
          height="auto"
          onClose={() => setConfirmRestore(false)}
          footer={<><Button variant="outline" onClick={() => setConfirmRestore(false)}>{t("btn_cancel")}</Button><Button variant="danger" onClick={() => void restore()}>{t("r2_restore_confirm")}</Button></>}
        >
          <div style={{ padding: 22, color: "var(--fg)", fontSize: 13, lineHeight: 1.7 }}>{t("r2_restore_warning")}</div>
        </Modal>
      )}
    </div>
  );
}
