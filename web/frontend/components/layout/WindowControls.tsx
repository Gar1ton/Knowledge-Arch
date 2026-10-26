"use client";
/**
 * 自绘标题栏的窗口按钮（最小化 / 最大化或还原 / 关闭），放在顶栏 Preferences 右侧。
 *
 * Windows / Linux 的桌面壳去掉了系统标题栏（tauri.windows.conf.json / tauri.linux.conf.json），
 * 由这里提供窗口按钮；macOS 保留系统红绿灯（Overlay 标题栏，嵌在顶栏左侧），不渲染本组件。
 * 关闭键触发与系统关闭相同的 CloseRequested，「关闭时最小化到托盘」照常生效。
 */

import React, { useEffect, useState, useSyncExternalStore } from "react";
import { Icon } from "@/components/ds/Icon";
import { inDesktopShell } from "@/lib/backend";
import { useI18n } from "@/lib/i18n";

export type ShellPlatform = "mac" | "other" | null;

const noopSubscribe = () => () => {};

/** 桌面壳所在平台；浏览器里（及服务端预渲染时）为 null。 */
export function useShellPlatform(): ShellPlatform {
  return useSyncExternalStore(
    noopSubscribe,
    () => (inDesktopShell() ? (navigator.userAgent.includes("Mac") ? "mac" : "other") : null),
    () => null,
  );
}

async function currentWindow() {
  const { getCurrentWindow } = await import("@tauri-apps/api/window");
  return getCurrentWindow();
}

const BUTTON: React.CSSProperties = {
  width: 40,
  height: 30,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  border: "none",
  borderRadius: "var(--radius-md)",
  background: "transparent",
  color: "var(--fg-muted)",
  cursor: "pointer",
  transition: "background .12s, color .12s",
};

function WindowButton({
  icon,
  label,
  danger,
  onClick,
}: {
  icon: string;
  label: string;
  danger?: boolean;
  onClick: () => void;
}) {
  const [hover, setHover] = useState(false);
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        ...BUTTON,
        ...(hover
          ? danger
            ? { background: "var(--danger)", color: "var(--danger-fg, #fff)" }
            : { background: "var(--bg-inset)", color: "var(--fg)" }
          : null),
      }}
    >
      <Icon name={icon} size={14} />
    </button>
  );
}

export function WindowControls() {
  const { t } = useI18n();
  const platform = useShellPlatform();
  const [maximized, setMaximized] = useState(false);

  useEffect(() => {
    if (platform !== "other") return;
    let unlisten: (() => void) | undefined;
    let alive = true;
    void currentWindow().then(async (win) => {
      const sync = () => {
        void win.isMaximized().then((value) => {
          if (alive) setMaximized(value);
        });
      };
      sync();
      unlisten = await win.onResized(sync);
    });
    return () => {
      alive = false;
      unlisten?.();
    };
  }, [platform]);

  if (platform !== "other") return null;
  return (
    <span style={{ display: "inline-flex", gap: 2, marginLeft: 4 }}>
      <WindowButton
        icon="winMinimize"
        label={t("window_minimize")}
        onClick={() => void currentWindow().then((w) => w.minimize())}
      />
      <WindowButton
        icon={maximized ? "winRestore" : "winMaximize"}
        label={maximized ? t("window_restore") : t("window_maximize")}
        onClick={() => void currentWindow().then((w) => w.toggleMaximize())}
      />
      <WindowButton
        icon="x"
        label={t("window_close")}
        danger
        onClick={() => void currentWindow().then((w) => w.close())}
      />
    </span>
  );
}
