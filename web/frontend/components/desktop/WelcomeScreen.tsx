"use client";
/**
 * 欢迎页：首次启动（空库）时依次「选择语言 → 导入旧版插件数据 / 从空库开始 → 用 AI 助手完成配置」。
 * 导入在 AI 配置之前：导入会迁移并覆盖设置，放在后面会冲掉助手刚配好的内容。
 *
 * 导入流程：选旧安装的根目录 → 预览（文档数 / 大小 / 是否有配置）→ 开始 → 后台复制并准备，
 * 完成后服务自动重启换入 → 显示结果与「需要你确认」的事项。导入状态由后端结果文件接续，
 * 服务重启期间轮询失败属正常，继续等即可。
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { formatBytes } from "@/components/flow/SyncActions";
import { inDesktopShell, pickFolder } from "@/lib/backend";
import { useI18n, type Lang } from "@/lib/i18n";
import {
  patchSetting,
  getLegacyImportState,
  inspectLegacyData,
  startLegacyImport,
  type LegacyImportResult,
  type LegacyImportState,
  type LegacyPreview,
} from "@/lib/settingsApi";
import { AgentSetupStep } from "./AgentSetup";
import { landing, LandingShell } from "./LandingShell";

const WELCOME_DONE_KEY = "ka-welcome-done";
const RESULT_SEEN_KEY = "ka-import-result-seen";
const LANG_CHOSEN_KEY = "ka-lang-chosen";
const LANGS: { value: Lang; label: string; desc: "welcome_lang_zh_desc" | "welcome_lang_en_desc" }[] = [
  { value: "zh", label: "中文", desc: "welcome_lang_zh_desc" },
  { value: "en", label: "English", desc: "welcome_lang_en_desc" },
];

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

function Arrow() {
  return (
    <svg className={landing.optionArrow} width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="5" y1="12" x2="19" y2="12" />
      <polyline points="12 5 19 12 12 19" />
    </svg>
  );
}

type Step =
  | { kind: "language" }
  | { kind: "choose" }
  | { kind: "select"; path: string; preview: LegacyPreview | null; error: string; busy: boolean }
  | { kind: "importing"; stage: string; done: number; total: number }
  | { kind: "result"; result: LegacyImportResult }
  | { kind: "assistant" };

export function WelcomeScreen({ onDone }: { onDone: () => void }) {
  const { t, setLang } = useI18n();
  const [step, setStep] = useState<Step>(() => (
    readFlag(LANG_CHOSEN_KEY) === "1" ? { kind: "choose" } : { kind: "language" }
  ));
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

  const chooseLanguage = (value: Lang) => {
    setLang(value);
    writeFlag(LANG_CHOSEN_KEY, "1");
    // 以后端 settings.json 为准（随 R2 快照换机）；写失败不挡住欢迎页，本机界面已切换。
    void patchSetting("prefs.general.language", value).catch(() => undefined);
    setStep({ kind: "choose" });
  };

  const toAssistant = (result?: LegacyImportResult) => {
    if (result) writeFlag(RESULT_SEEN_KEY, result.finished_at ?? "");
    setStep({ kind: "assistant" });
  };

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
    <LandingShell subtitle={t("landing_sub_welcome")} wide={step.kind === "assistant"}>
      <section className={landing.card}>
        <div className={landing.body}>
          {step.kind === "assistant" && <AgentSetupStep onDone={() => finish()} />}

          {step.kind === "language" && (
            <>
              <h2 className={landing.heading}>{t("welcome_lang_title")}</h2>
              <p className={landing.text}>{t("welcome_lang_body")}</p>
              <div className={landing.langOptions}>
                {LANGS.map((item) => (
                  <button key={item.value} type="button" className={landing.option} lang={item.value}
                    onClick={() => chooseLanguage(item.value)}>
                    <span className={landing.optionText}>
                      <strong>{item.label}</strong>
                      <span>{t(item.desc)}</span>
                    </span>
                    <Arrow />
                  </button>
                ))}
              </div>
            </>
          )}

          {step.kind !== "assistant" && step.kind !== "language" && (
            <h2 className={landing.heading}>{t("welcome_title")}</h2>
          )}

          {step.kind === "choose" && (
            <>
              <p className={landing.text}>{t("welcome_body")}</p>
              <div className={landing.options}>
                <button type="button" className={landing.option}
                  onClick={() => (inDesktopShell() ? void choosePath() : setStep({ kind: "select", path: "", preview: null, error: "", busy: false }))}>
                  <span className={landing.optionText}>
                    <strong>{t("welcome_import")}</strong>
                    <span>{t("welcome_import_desc")}</span>
                  </span>
                  <Arrow />
                </button>
                <button type="button" className={landing.option} onClick={() => toAssistant()}>
                  <span className={landing.optionText}>
                    <strong>{t("welcome_empty")}</strong>
                    <span>{t("welcome_empty_desc")}</span>
                  </span>
                  <Arrow />
                </button>
              </div>
            </>
          )}

          {step.kind === "select" && (
            <>
              <p className={landing.text}>{t("welcome_select_body")}</p>
              <div className={landing.row}>
                <input
                  className={landing.input}
                  value={step.path}
                  placeholder={t("welcome_path_placeholder")}
                  autoFocus
                  onChange={(e) => setStep({ ...step, path: e.target.value, preview: null, error: "" })}
                  onKeyDown={(e) => { if (e.key === "Enter" && step.path) void inspect(step.path); }}
                />
                {inDesktopShell() && (
                  <button type="button" className={landing.ghost} onClick={() => void choosePath()}>{t("welcome_browse")}</button>
                )}
                <button type="button" className={landing.ghost} disabled={!step.path || step.busy} onClick={() => void inspect(step.path)}>
                  {step.busy ? "…" : t("welcome_check")}
                </button>
              </div>
              {step.error && <div role="alert" className={landing.error}>{step.error}</div>}
              {step.preview && (
                <ul className={landing.list}>
                  <li>{t("welcome_preview_docs", { n: step.preview.document_count })}</li>
                  <li>{t("welcome_preview_size", { size: formatBytes(step.preview.size_bytes), n: step.preview.file_count })}</li>
                  <li>{step.preview.config_found ? t("welcome_preview_config") : t("welcome_preview_no_config")}</li>
                  {step.preview.has_vector_store && <li>{t("welcome_preview_vectors")}</li>}
                  {step.preview.has_graph && <li>{t("welcome_preview_graph")}</li>}
                </ul>
              )}
              <p className={landing.hint}>{t("welcome_copy_note")}</p>
              <div className={landing.divider} aria-hidden="true" />
              <div className={landing.actions}>
                <button type="button" className={landing.primary} disabled={!step.preview} onClick={() => void start(step.path)}>
                  {t("welcome_start")}
                </button>
                <button type="button" className={landing.ghost} onClick={() => setStep({ kind: "choose" })}>{t("btn_cancel")}</button>
              </div>
            </>
          )}

          {step.kind === "importing" && (
            <>
              <p className={landing.text}>{t(`welcome_stage_${step.stage}` as Parameters<typeof t>[0])}</p>
              <div className={landing.progress}>
                <div className={landing.progressBar} style={{ width: step.total ? `${Math.round((step.done / step.total) * 100)}%` : "35%" }} />
              </div>
              {step.total > 0 && <p className={landing.hint}>{step.done} / {step.total}</p>}
            </>
          )}

          {step.kind === "result" && (
            <>
              <p className={landing.text}>
                {step.result.status === "ok"
                  ? t("welcome_done", { n: step.result.summary.document_count ?? 0 })
                  : t("welcome_failed", { error: step.result.error ?? "" })}
              </p>
              {step.result.attention.length > 0 && (
                <>
                  <div className={landing.fieldLabel}>{t("welcome_attention")}</div>
                  <ul className={landing.list}>
                    {step.result.attention.map((note) => <li key={note}>{note}</li>)}
                  </ul>
                </>
              )}
              {step.result.dropped.length > 0 && (
                <details className={landing.details}>
                  <summary>{t("welcome_dropped", { n: step.result.dropped.length })}</summary>
                  <code>{step.result.dropped.join(", ")}</code>
                </details>
              )}
              <div className={landing.divider} aria-hidden="true" />
              <div className={landing.actions}>
                <button type="button" className={landing.primary}
                  onClick={() => (step.result.status === "ok" ? toAssistant(step.result) : finish(step.result))}>
                  {step.result.status === "ok" ? t("welcome_next") : t("welcome_enter")}
                </button>
              </div>
            </>
          )}
        </div>
      </section>
    </LandingShell>
  );
}
