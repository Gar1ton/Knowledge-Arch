"use client";
/**
 * 偏好设置「备份与迁移」（3.0.0）：一键导出 / 导入 `.kaarch`（设置、口令加密的密钥、集合与构筑）。
 * 导入先只读文件清单；本机库不空时选「完全覆盖」或「合并」，覆盖前在页内再确认一次。
 * 导出与导入在后台跑，这里每秒查一次进度。
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ds/Button";
import { Card, Field } from "@/components/ds/Card";
import { DirPickerDialog } from "@/components/flow/DirPickerDialog";
import { formatBytes } from "@/components/flow/SyncActions";
import { useToast } from "@/components/ui/Toast";
import { inDesktopShell, pickFile, pickFolder } from "@/lib/backend";
import { useI18n, type I18nKey } from "@/lib/i18n";
import {
  getKaarchJob,
  inspectKaarch,
  startKaarchExport,
  startKaarchImport,
  type KaarchInfo,
  type KaarchJob,
  type KaarchMode,
  type KaarchPart,
} from "@/lib/kaarchApi";

const PARTS: { id: KaarchPart; label: I18nKey; hint: I18nKey }[] = [
  { id: "settings", label: "bk_part_settings", hint: "bk_part_settings_hint" },
  { id: "secrets", label: "bk_part_secrets", hint: "bk_part_secrets_hint" },
  { id: "library", label: "bk_part_library", hint: "bk_part_library_hint" },
];

/** 后台任务轮询：有任务在跑时每秒取一次，跑完停。 */
function useKaarchJob() {
  const [job, setJob] = useState<KaarchJob | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const poll = useCallback(() => {
    if (timer.current) clearInterval(timer.current);
    timer.current = setInterval(() => {
      getKaarchJob()
        .then((j) => {
          setJob(j);
          if (!j || j.status !== "running") {
            if (timer.current) clearInterval(timer.current);
            timer.current = null;
          }
        })
        .catch(() => undefined);
    }, 1000);
  }, []);
  useEffect(() => () => { if (timer.current) clearInterval(timer.current); }, []);
  return { job, setJob, poll };
}

export function BackupGroup() {
  return (
    <>
      <ExportCard />
      <ImportCard />
    </>
  );
}

function ExportCard() {
  const { t } = useI18n();
  const { toast } = useToast();
  const [parts, setParts] = useState<Set<KaarchPart>>(new Set(["settings", "library"]));
  const [passphrase, setPassphrase] = useState("");
  const [folder, setFolder] = useState("");
  const [picker, setPicker] = useState(false);
  const { job, setJob, poll } = useKaarchJob();
  const running = job?.status === "running" && job.action === "export";

  const toggle = (id: KaarchPart) => setParts((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const choose = async () => {
    if (inDesktopShell()) {
      const picked = await pickFolder(t("bk_export_folder"));
      if (picked) setFolder(picked);
    } else {
      setPicker(true);
    }
  };
  const needsPass = parts.has("secrets") && !passphrase;
  const run = async () => {
    try {
      setJob(await startKaarchExport(folder, [...parts], parts.has("secrets") ? passphrase : ""));
      poll();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    }
  };
  const done = job?.action === "export" && job.status === "success" ? job.result : null;

  return (
    <Card title={t("bk_export_title")} icon="download">
      {PARTS.map((p) => (
        <label key={p.id} className="bk-part">
          <input type="checkbox" checked={parts.has(p.id)} onChange={() => toggle(p.id)} disabled={running} />
          <span><b>{t(p.label)}</b><small>{t(p.hint)}</small></span>
          {p.id === "secrets" && parts.has("secrets") && (
            <input className="imp-input bk-pass" type="password" value={passphrase} disabled={running}
              placeholder={t("bk_passphrase")} aria-label={t("bk_passphrase")}
              onChange={(e) => setPassphrase(e.target.value)} />
          )}
        </label>
      ))}
      <Field label={t("bk_export_folder")} hint={folder || t("bk_export_folder_hint")}>
        <Button size="sm" variant="outline" onClick={() => void choose()} disabled={running}>{t("bk_choose")}</Button>
      </Field>
      <div className="bk-actions">
        {needsPass && <span className="src-meta">{t("bk_need_passphrase")}</span>}
        <span style={{ flex: 1 }} />
        <Button size="sm" onClick={() => void run()} loading={running}
          disabled={running || !folder || parts.size === 0 || needsPass}>
          {t("bk_export_go")}
        </Button>
      </div>
      {job?.action === "export" && <JobLine job={job} />}
      {done && (
        <div className="imp-ok">{t("bk_export_done", { path: String(done.path ?? ""), size: formatBytes(Number(done.size ?? 0)) })}</div>
      )}
      {picker && <DirPickerDialog t={t} onClose={() => setPicker(false)} onSelect={(p) => { setPicker(false); setFolder(p); }} />}
    </Card>
  );
}

function ImportCard() {
  const { t } = useI18n();
  const { toast } = useToast();
  const [path, setPath] = useState("");
  const [info, setInfo] = useState<KaarchInfo | null>(null);
  const [mode, setMode] = useState<KaarchMode>("merge");
  const [passphrase, setPassphrase] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [reading, setReading] = useState(false);
  const { job, setJob, poll } = useKaarchJob();
  const running = job?.status === "running" && job.action !== "export";

  const read = async (p: string) => {
    if (!p.trim()) return;
    setReading(true);
    setInfo(null);
    setConfirming(false);
    try {
      const i = await inspectKaarch(p.trim());
      setInfo(i);
      setMode(i.local_empty ? "overwrite" : "merge");
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setReading(false);
    }
  };
  const choose = async () => {
    const picked = await pickFile(t("bk_import_pick"), ["kaarch"]);
    if (picked) {
      setPath(picked);
      void read(picked);
    }
  };
  const start = async () => {
    try {
      setJob(await startKaarchImport(info!.path, mode, passphrase));
      setConfirming(false);
      poll();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    }
  };
  const go = () => (mode === "overwrite" && !info?.local_empty ? setConfirming(true) : void start());
  const hasKeys = !!info?.parts.includes("secrets");
  const c = info?.counts ?? {};

  return (
    <Card title={t("bk_import_title")} icon="upload">
      <Field label={t("bk_import_file")} hint={path || t("bk_import_file_hint")}>
        {inDesktopShell() ? (
          <Button size="sm" variant="outline" onClick={() => void choose()} disabled={running || reading}>{t("bk_choose")}</Button>
        ) : (
          <span className="bk-path">
            <input className="imp-input" value={path} placeholder="/path/to/file.kaarch" aria-label={t("bk_import_file")}
              onChange={(e) => setPath(e.target.value)} />
            <Button size="sm" variant="outline" onClick={() => void read(path)} loading={reading}>{t("bk_read")}</Button>
          </span>
        )}
      </Field>
      {info && (
        <>
          <div className="bk-summary">
            <span>{t("bk_made", { time: new Date(info.created_at).toLocaleString(), version: info.app_version || "?" })}</span>
            {info.parts.includes("settings") && <span>{t("bk_has_settings", { n: info.settings_diff })}</span>}
            {hasKeys && <span>{t("bk_has_keys", { n: info.secret_count })}</span>}
            {info.parts.includes("library") && (
              <span>{t("bk_has_library", { docs: c.documents ?? 0, colls: c.collections ?? 0, notes: c.notes ?? 0 })}</span>
            )}
            {info.parts.includes("library") && info.embedding_same !== null && (
              <span className={info.embedding_same ? "bk-good" : "bk-warn"}>
                {info.embedding_same ? t("bk_embedding_same") : t("bk_embedding_diff")}
              </span>
            )}
          </div>
          {hasKeys && (
            <Field label={t("bk_passphrase")} hint={t("bk_passphrase_import_hint")}>
              <input className="imp-input bk-pass" type="password" value={passphrase} disabled={running}
                aria-label={t("bk_passphrase")} onChange={(e) => setPassphrase(e.target.value)} />
            </Field>
          )}
          {info.local_empty ? (
            <div className="src-meta bk-note">{t("bk_local_empty")}</div>
          ) : (
            <div className="bk-modes" role="radiogroup">
              <div className="src-h">{t("bk_local_not_empty", { n: info.local_documents })}</div>
              {(["merge", "overwrite"] as KaarchMode[]).map((m) => (
                <label key={m} className={`imp-opt ${mode === m ? "on" : ""}`}>
                  <input type="radio" name="bk-mode" checked={mode === m} onChange={() => setMode(m)} disabled={running} />
                  <span><b>{t(m === "merge" ? "bk_mode_merge" : "bk_mode_overwrite")}</b>
                    <small>{t(m === "merge" ? "bk_mode_merge_hint" : "bk_mode_overwrite_hint")}</small></span>
                </label>
              ))}
            </div>
          )}
          {confirming ? (
            <div className="imp-warn bk-confirm">
              <span>{t("bk_confirm_overwrite")}</span>
              <span className="bk-actions">
                <Button size="sm" variant="outline" onClick={() => setConfirming(false)}>{t("btn_cancel")}</Button>
                <Button size="sm" variant="danger" onClick={() => void start()}>{t("bk_confirm_go")}</Button>
              </span>
            </div>
          ) : (
            <div className="bk-actions">
              {hasKeys && !passphrase && <span className="src-meta">{t("bk_keys_skipped")}</span>}
              <span style={{ flex: 1 }} />
              <Button size="sm" onClick={go} loading={running} disabled={running}>{t("bk_import_go")}</Button>
            </div>
          )}
        </>
      )}
      {job && job.action !== "export" && <JobLine job={job} />}
      {job && job.action !== "export" && job.status === "success" && <ImportResult job={job} t={t} />}
    </Card>
  );
}

const STAGES: Record<string, I18nKey> = {
  prepare: "bk_stage_prepare", inventory: "bk_stage_inventory", hash: "bk_stage_hash",
  write: "bk_stage_write", extract: "bk_stage_extract", read: "bk_stage_read",
};

function JobLine({ job }: { job: KaarchJob }) {
  const { t } = useI18n();
  const stage = STAGES[job.stage] ? t(STAGES[job.stage]) : job.stage;
  if (job.status === "error") return <div className="imp-warn">{t("bk_failed", { msg: job.error })}</div>;
  if (job.status !== "running") return null;
  return (
    <div className="bk-progress" aria-live="polite">
      <div className="bk-bar"><span style={{ width: `${job.progress}%` }} /></div>
      <span className="src-meta">{t("bk_stage", { stage, pct: job.progress })}{job.detail ? ` · ${job.detail}` : ""}</span>
    </div>
  );
}

function ImportResult({ job, t }: { job: KaarchJob; t: ReturnType<typeof useI18n>["t"] }) {
  const r = job.result as Record<string, number | boolean | unknown[]>;
  if (job.action === "import_overwrite") {
    return <div className="imp-ok">{r.restart ? t("bk_overwrite_restarting") : t("bk_overwrite_done")}</div>;
  }
  const failed = Array.isArray(r.failed) ? r.failed.length : 0;
  return (
    <div className={failed ? "imp-warn" : "imp-ok"}>
      {t("bk_merge_done", {
        docs: Number(r.documents_added ?? 0), present: Number(r.documents_present ?? 0),
        colls: Number(r.collections_added ?? 0), notes: Number(r.notes_added ?? 0),
        zotero: Number(r.zotero_skipped ?? 0), settings: Number(r.settings_added ?? 0),
        keys: Number(r.secrets_added ?? 0), failed,
      })}
    </div>
  );
}
