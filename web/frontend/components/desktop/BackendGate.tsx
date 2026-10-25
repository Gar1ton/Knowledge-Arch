"use client";
/**
 * 后端连接闸门：等桌面壳报告后端就绪（或开发期直接放行）后再渲染控制台；
 * 后端启动失败或运行中退出时切到崩溃页，重启成功后回到控制台。
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  BACKEND_EXITED_EVENT,
  BACKEND_READY_EVENT,
  backendInfo,
  backendState,
  onShellEvent,
  resetBackendInfo,
  restartBackendProcess,
  type BackendInfo,
  type BackendState,
} from "@/lib/backend";
import { useI18n } from "@/lib/i18n";
import { CrashScreen } from "./CrashScreen";
import { LandingSplash } from "./LandingShell";

type GateState =
  | { phase: "connecting" }
  | { phase: "ready"; epoch: number }
  | { phase: "crashed"; message: string; logTail: string[] };

export function BackendGate({ children }: { children: (epoch: number) => React.ReactNode }) {
  const { t } = useI18n();
  const [state, setState] = useState<GateState>({ phase: "connecting" });

  /** 向壳要后端地址；成功 → ready，失败 → 带壳给的原因与日志尾进入崩溃页。 */
  const attach = useCallback(
    () =>
      backendInfo()
        .then(() => setState({ phase: "ready", epoch: Date.now() }))
        .catch(async (err) => {
          const detail = await backendState().catch((): BackendState => ({ state: "crashed" }));
          setState({
            phase: "crashed",
            message: detail.error || (err instanceof Error ? err.message : String(err)),
            logTail: detail.log_tail ?? [],
          });
        }),
    [],
  );

  const connect = useCallback(async () => {
    setState({ phase: "connecting" });
    await attach();
  }, [attach]);

  useEffect(() => {
    // 首次连接：只在异步结果回来后才 setState（初始状态已是 connecting）。
    backendInfo()
      .then(() => setState({ phase: "ready", epoch: Date.now() }))
      .catch(() => void attach());
    let offExit: (() => void) | undefined;
    let offReady: (() => void) | undefined;
    void onShellEvent<BackendState>(BACKEND_EXITED_EVENT, (payload) => {
      resetBackendInfo();
      setState({
        phase: "crashed",
        message: payload?.error || t("crash_exited", { code: String(payload?.exit_code ?? "?") }),
        logTail: payload?.log_tail ?? [],
      });
    }).then((off) => { offExit = off; });
    void onShellEvent<BackendInfo>(BACKEND_READY_EVENT, () => {
      setState({ phase: "ready", epoch: Date.now() });
    }).then((off) => { offReady = off; });
    return () => { offExit?.(); offReady?.(); };
  }, [attach, t]);

  if (state.phase === "connecting") {
    return <LandingSplash subtitle={t("landing_sub_starting")} status={t("backend_connecting")} />;
  }
  if (state.phase === "crashed") {
    return (
      <CrashScreen
        message={state.message}
        logTail={state.logTail}
        onRestart={async () => {
          try {
            await restartBackendProcess();
            setState({ phase: "ready", epoch: Date.now() });
          } catch {
            await connect();
          }
        }}
      />
    );
  }
  return <>{children(state.epoch)}</>;
}
