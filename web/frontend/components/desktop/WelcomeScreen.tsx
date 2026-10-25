"use client";
/**
 * 欢迎页：首次启动（空库）时选择「导入旧版插件数据」或「从空库开始」。
 *
 * 导入流程：选旧安装的根目录 → 预览（文档数 / 大小 / 是否有配置）→ 开始 → 后台复制并准备，
 * 完成后服务自动重启换入 → 显示结果与「需要你确认」的事项。导入状态由后端结果文件接续，
 * 服务重启期间轮询失败属正常，继续等即可。
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ds/Button";
import { formatBytes } from "@/components/flow/SyncActions";
import { inDesktopShell, pickFolder } from "@/lib/backend";
import { useI18n } from "@/lib/i18n";
import { DS_INPUT_STYLE } from "@/components/settings/SettingInput";
import {
  getLegacyImportState,
  inspectLegacyData,
  startLegacyImport,
  type LegacyImportResult,
  type LegacyImportState,
  type LegacyPreview,
} from "@/lib/settingsApi";

const WELCOME_DONE_KEY = "ka-welcome-done";
const RESULT_SEEN_KEY = "ka-import-result-seen";

function readFlag(key: string): string {
  try { return localStorage.getItem(key) ?? ""; } catch { return ""; }
}

function writeFlag(key: string, value: string): void {
  try { localStorage.setItem(key, value); } catch { /* 隐私模式等：忽略 */ }
}

/** 是否需要显示欢迎页；null = 还在判断。 */
export function useWelcomeNeeded(epoch: number): [boolean | null, () => void] {
  const [needed, setNeeded] = useState<boolean | null>(null);
  useEffect(() => {
    let alive = true;
    getLegacyImportState()
      .then((s) => {
        if (!alive) return;
        const busy = s.job?.status === "running" || s.job?.status === "restarting" || Boolean(s.pending);
        const unseenResult = Boolean(s.result && readFlag(RESULT_SEEN_KEY) !== (s.result.finished_at ?? ""));
        setNeeded(busy || unseenResult || (s.library_empty && readFlag(WELCOME_DONE_KEY) !== "1"));
      })
      .catch(() => { if (alive) setNeeded(false); });
    return () => { alive = false; };
  }, [epoch]);
  const dismiss = useCallback(() => setNeeded(false), []);
  return [needed, dismiss];
}

type Step =
  | { kind: "choose" }
  | { kind: "select"; path: string; preview: LegacyPreview | null; error: string; busy: boolean }
  | { kind: "importing"; stage: string; done: number; total: number }
  | { kind: "result"; result: LegacyImportResult };

export function WelcomeScreen({ onDone }: { onDone: () => void }) {
  const { t } = useI18n();
  const [step, setStep] = useState<Step>({ kind: "choose" });
  const polling = useRef(false);

  const finish = useCallback((result?: LegacyImportResult) => {
    writeFlag(WELCOME_DONE_KEY, "1");
    if (result) writeFlag(RESULT_SEEN_KEY, result.finished_at ?? "");
    onDone();
  }, [onDone]);

  const applyState = useCallback((s: LegacyImportState) => {
    if (s.job && (s.job.status === "running" || s.job.status === "restarting")) {
      setStep({ kind: "importing", stage: s.job.stage, done: s.job.done, total: s.job.total });
      return true;
    }
    if (s.job?.status === "error") {
      setStep({ kind: "select", path: "", preview: null, error: s.job.error, busy: false });
      return false;
    }
    if (s.pending) {
      setStep({ kind: "importing", stage: "restarting", done: 0, total: 0 });
      return true;
    }
    if (s.result && readFlag(RESULT_SEEN_KEY) !== (s.result.finished_at ?? "")) {
      setStep({ kind: "result", result: s.result });
      return false;
    }
    return false;
  }, []);

  const poll = useCallback(async () => {
    if (polling.current) return;
    polling.current = true;
    try {
      for (;;) {
        await new Promise((r) => setTimeout(r, 1000));
        try {
          if (!applyState(await getLegacyImportState())) return;
        } catch {
          /* 服务重启中：继续等 */
        }
      }
    } finally {
      polling.current = false;
    }
  }, [applyState]);

  useEffect(() => {
    getLegacyImportState().then((s) => { if (applyState(s)) void poll(); }).catch(() => undefined);
  }, [applyState, poll]);

  const inspect = async (path: string) => {
    setStep({ kind: "select", path, preview: null, error: "", busy: true });
    try {
      const preview = await inspectLegacyData(path);
      setStep({ kind: "select", path, preview, error: "", busy: false });
    } catch (err) {
      setStep({ kind: "select", path, preview: null, error: err instanceof Error ? err.message : String(err), busy: false });
    }
  };

  const choosePath = async () => {
    const picked = await pickFolder(t("welcome_pick_title"));
    if (picked) await inspect(picked);
  };

  const start = async (path: string) => {
    try {
      await startLegacyImport(path);
      setStep({ kind: "importing", stage: "preparing", done: 0, total: 0 });
      void poll();
    } catch (err) {
      setStep({ kind: "select", path, preview: null, error: err instanceof Error ? err.message : String(err), busy: false });
    }
  };

  return (
    <div className="ka-fullscreen-card">
      <div className="ka-fullscreen-inner">
        <h1 className="ka-fullscreen-title">{t("welcome_title")}</h1>

        {step.kind === "choose" && (
          <>
            <p className="ka-fullscreen-text">{t("welcome_body")}</p>
            <div className="ka-welcome-options">
              <button type="button" className="ka-welcome-option"
                onClick={() => (inDesktopShell() ? void choosePath() : setStep({ kind: "select", path: "", preview: null, error: "", busy: false }))}>
                <strong>{t("welcome_import")}</strong>
                <span>{t("welcome_import_desc")}</span>
              </button>
              <button type="button" className="ka-welcome-option" onClick={() => finish()}>
                <strong>{t("welcome_empty")}</strong>
                <span>{t("welcome_empty_desc")}</span>
              </button>
            </div>
          </>
        )}

        {step.kind === "select" && (
          <>
            <p className="ka-fullscreen-text">{t("welcome_select_body")}</p>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <input
                style={{ ...DS_INPUT_STYLE, flex: 1, width: "auto" }}
                value={step.path}
                placeholder={t("welcome_path_placeholder")}
                onChange={(e) => setStep({ ...step, path: e.target.value, preview: null, error: "" })}
                onKeyDown={(e) => { if (e.key === "Enter" && step.path) void inspect(step.path); }}
              />
              {inDesktopShell() && <Button variant="outline" onClick={() => void choosePath()}>{t("welcome_browse")}</Button>}
              <Button variant="outline" disabled={!step.path || step.busy} loading={step.busy} onClick={() => void inspect(step.path)}>
                {t("welcome_check")}
              </Button>
            </div>
            {step.error && <div role="alert" className="ka-crash-reason">{step.error}</div>}
            {step.preview && (
              <ul className="ka-welcome-preview">
                <li>{t("welcome_preview_docs", { n: step.preview.document_count })}</li>
                <li>{t("welcome_preview_size", { size: formatBytes(step.preview.size_bytes), n: step.preview.file_count })}</li>
                <li>{step.preview.config_found ? t("welcome_preview_config") : t("welcome_preview_no_config")}</li>
                {step.preview.has_vector_store && <li>{t("welcome_preview_vectors")}</li>}
                {step.preview.has_graph && <li>{t("welcome_preview_graph")}</li>}
              </ul>
            )}
            <p className="ka-fullscreen-hint">{t("welcome_copy_note")}</p>
            <div className="ka-fullscreen-actions">
              <Button variant="primary" disabled={!step.preview} onClick={() => void start(step.path)}>{t("welcome_start")}</Button>
              <Button variant="outline" onClick={() => setStep({ kind: "choose" })}>{t("btn_cancel")}</Button>
            </div>
          </>
        )}

        {step.kind === "importing" && (
          <>
            <p className="ka-fullscreen-text">{t(`welcome_stage_${step.stage}` as Parameters<typeof t>[0])}</p>
            <div className="ka-progress">
              <div className="ka-progress-bar" style={{ width: step.total ? `${Math.round((step.done / step.total) * 100)}%` : "35%" }} />
            </div>
            {step.total > 0 && <p className="ka-fullscreen-hint">{step.done} / {step.total}</p>}
          </>
        )}

        {step.kind === "result" && (
          <>
            <p className="ka-fullscreen-text">
              {step.result.status === "ok"
                ? t("welcome_done", { n: step.result.summary.document_count ?? 0 })
                : t("welcome_failed", { error: step.result.error ?? "" })}
            </p>
            {step.result.attention.length > 0 && (
              <>
                <div className="ka-fullscreen-label">{t("welcome_attention")}</div>
                <ul className="ka-welcome-preview">
                  {step.result.attention.map((note) => <li key={note}>{note}</li>)}
                </ul>
              </>
            )}
            {step.result.dropped.length > 0 && (
              <details className="ka-fullscreen-hint">
                <summary>{t("welcome_dropped", { n: step.result.dropped.length })}</summary>
                <code style={{ overflowWrap: "anywhere" }}>{step.result.dropped.join(", ")}</code>
              </details>
            )}
            <div className="ka-fullscreen-actions">
              <Button variant="primary" onClick={() => finish(step.result)}>{t("welcome_enter")}</Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
