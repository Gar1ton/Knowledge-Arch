"use client";
/**
 * 后端退出 / 启动失败时的整窗提示：显示最后几行日志，提供「重启服务」与「打开日志目录」。
 */
import React, { useEffect, useRef, useState } from "react";
import { inDesktopShell, openLogDir } from "@/lib/backend";
import { useI18n } from "@/lib/i18n";
import { landing, LandingShell } from "./LandingShell";

export function CrashScreen({
  message,
  logTail,
  onRestart,
}: {
  message: string;
  logTail: string[];
  onRestart: () => Promise<void>;
}) {
  const { t } = useI18n();
  const [restarting, setRestarting] = useState(false);
  const logRef = useRef<HTMLPreElement>(null);
  // 最有用的是最后几行：日志框默认滚到底部。
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logTail]);
  return (
    <LandingShell subtitle={t("landing_sub_stopped")} wide role="alert">
      <section className={landing.card}>
        <div className={landing.body}>
          <h2 className={landing.heading}>{t("crash_title")}</h2>
          <p className={landing.text}>{t("crash_body")}</p>
          {message && <code className={landing.error}>{message}</code>}
          {logTail.length > 0 && (
            <>
              <div className={landing.fieldLabel}>{t("crash_log_tail")}</div>
              <pre ref={logRef} className={landing.log}>{logTail.join("\n")}</pre>
            </>
          )}
          <div className={landing.divider} aria-hidden="true" />
          <div className={landing.actions}>
            <button
              type="button"
              className={landing.primary}
              disabled={restarting}
              onClick={async () => {
                setRestarting(true);
                try { await onRestart(); } finally { setRestarting(false); }
              }}
            >
              {restarting ? "…" : t("flow_restart_service")}
            </button>
            {inDesktopShell() && (
              <button type="button" className={landing.ghost} onClick={() => void openLogDir()}>{t("crash_open_logs")}</button>
            )}
          </div>
        </div>
      </section>
    </LandingShell>
  );
}
