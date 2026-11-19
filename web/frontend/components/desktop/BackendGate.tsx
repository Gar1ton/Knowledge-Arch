"use client";
/**
 * 后端连接闸门：等桌面壳报告后端就绪（或开发期直接放行）后再渲染控制台；
 * 后端启动失败或运行中退出时切到崩溃页，重启成功后回到控制台。
 * 进程活着却不再接受连接时壳收不到退出事件，所以桌面端另外定时探活，连续失败也进崩溃页。
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  BACKEND_EXITED_EVENT,
  BACKEND_READY_EVENT,
  backendInfo,
  backendReachable,
  backendState,
  inDesktopShell,
  onShellEvent,
  resetBackendInfo,
  restartBackendProcess,
  type BackendInfo,
  type BackendState,
} from "@/lib/backend";
import { useI18n } from "@/lib/i18n";
import { CrashScreen } from "./CrashScreen";
import { LandingSplash } from "./LandingShell";

/** 探活间隔与判定不可达所需的连续失败次数（约 15 秒）。 */
const PROBE_INTERVAL_MS = 5_000;
const PROBE_FAILURES = 3;

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

  const readyEpoch = state.phase === "ready" ? state.epoch : null;
  useEffect(() => {
    if (readyEpoch === null || !inDesktopShell()) return;
    let failures = 0;
    let probing = false;
    let cancelled = false;
    const timer = setInterval(() => {
      if (probing) return;
      probing = true;
      void backendReachable()
        .then(async (ok) => {
          if (ok) { failures = 0; return; }
          // 壳正在重启或已报告崩溃时连不上是预期的（由 ready / exited 事件处理），不计数。
          const detail = await backendState().catch((): BackendState => ({ state: "crashed" }));
          failures = detail.state === "ready" ? failures + 1 : 0;
          if (cancelled || failures < PROBE_FAILURES) return;
          setState({ phase: "crashed", message: t("crash_unreachable"), logTail: detail.log_tail ?? [] });
        })
        .finally(() => { probing = false; });
    }, PROBE_INTERVAL_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [readyEpoch, t]);

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
