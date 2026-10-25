"use client";
/**
 * lib/uiScale.ts — 界面缩放（偏好 `prefs.appearance.ui_scale`）。
 *
 * 桌面壳里用 Tauri 原生 webview 缩放（不用 CSS zoom：会让 PDF.js 与数据流图的指针坐标错位）；
 * 浏览器开发期不接管，交给浏览器自带缩放。本机缓存一份，启动时不等后端就先应用。
 */
import { useEffect } from "react";
import { inDesktopShell } from "./backend";
import { patchSetting } from "./settingsApi";

export const UI_SCALE_KEY = "prefs.appearance.ui_scale";
export const UI_SCALES = [0.8, 0.9, 1, 1.1, 1.25, 1.5] as const;

const CACHE_KEY = "ka-ui-scale";
const SCALE_EVENT = "ka-ui-scale-change";

/** 夹到允许范围并就近取档位。 */
export function normalizeScale(value: unknown): number {
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return 1;
  return UI_SCALES.reduce((best, s) => (Math.abs(s - n) < Math.abs(best - n) ? s : best), 1);
}

export function cachedScale(): number {
  try { return normalizeScale(localStorage.getItem(CACHE_KEY) ?? 1); } catch { return 1; }
}

/** 应用到当前窗口（不写后端）。 */
export async function applyUiScale(value: unknown): Promise<number> {
  const scale = normalizeScale(value);
  try { localStorage.setItem(CACHE_KEY, String(scale)); } catch { /* 隐私模式等：忽略 */ }
  window.dispatchEvent(new CustomEvent<number>(SCALE_EVENT, { detail: scale }));
  if (inDesktopShell()) {
    const { getCurrentWebview } = await import("@tauri-apps/api/webview");
    await getCurrentWebview().setZoom(scale);
  }
  return scale;
}

/** 应用并保存到后端 settings（随 R2 快照换机）。 */
export async function setUiScale(value: unknown): Promise<number> {
  const scale = await applyUiScale(value);
  await patchSetting(UI_SCALE_KEY, scale).catch(() => undefined);
  return scale;
}

/** 当前档位的相邻档位；step=0 回到 100%。 */
export function steppedScale(current: number, step: -1 | 0 | 1): number {
  if (step === 0) return 1;
  const i = UI_SCALES.indexOf(normalizeScale(current) as (typeof UI_SCALES)[number]);
  return UI_SCALES[Math.min(UI_SCALES.length - 1, Math.max(0, i + step))];
}

/** 订阅缩放变化（偏好面板与快捷键保持同步）。 */
export function onUiScaleChange(handler: (scale: number) => void): () => void {
  const listener = (e: Event) => handler((e as CustomEvent<number>).detail);
  window.addEventListener(SCALE_EVENT, listener);
  return () => window.removeEventListener(SCALE_EVENT, listener);
}

/** 桌面壳内：启动时套用缓存值，并接管 Ctrl/⌘ + / − / 0。 */
export function useUiScaleShortcuts(): void {
  useEffect(() => {
    if (!inDesktopShell()) return undefined;
    void applyUiScale(cachedScale()).catch(() => undefined);
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
      const step = e.key === "=" || e.key === "+" ? 1 : e.key === "-" || e.key === "_" ? -1 : e.key === "0" ? 0 : null;
      if (step === null) return;
      e.preventDefault();
      void setUiScale(steppedScale(cachedScale(), step)).catch(() => undefined);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
