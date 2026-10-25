"use client";
/**
 * 后端退出 / 启动失败时的整窗提示：显示最后几行日志，提供「重启服务」与「打开日志目录」。
 */
import React, { useState } from "react";
import { Button } from "@/components/ds/Button";
import { inDesktopShell, openLogDir } from "@/lib/backend";
import { useI18n } from "@/lib/i18n";

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
  return (
    <div className="ka-fullscreen-card" role="alert">
      <div className="ka-fullscreen-inner">
        <h1 className="ka-fullscreen-title">{t("crash_title")}</h1>
        <p className="ka-fullscreen-text">{t("crash_body")}</p>
        {message && <code className="ka-crash-reason">{message}</code>}
        {logTail.length > 0 && (
          <>
            <div className="ka-fullscreen-label">{t("crash_log_tail")}</div>
            <pre className="ka-crash-log">{logTail.join("\n")}</pre>
          </>
        )}
        <div className="ka-fullscreen-actions">
          <Button
            variant="primary"
            loading={restarting}
            onClick={async () => {
              setRestarting(true);
              try { await onRestart(); } finally { setRestarting(false); }
            }}
          >
            {t("flow_restart_service")}
          </Button>
          {inDesktopShell() && (
            <Button variant="outline" onClick={() => void openLogDir()}>{t("crash_open_logs")}</Button>
          )}
        </div>
      </div>
    </div>
  );
}
