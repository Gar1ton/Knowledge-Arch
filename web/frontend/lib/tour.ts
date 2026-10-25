/**
 * lib/tour.ts — 首次使用引导（components/desktop/FirstRunTour.tsx）的本机状态与事件。
 *
 * 欢迎页选「跳过，自己配置」或偏好设置里「重新查看引导」时 requestTour()；引导走完或被跳过时
 * finishTour()。状态只放本机 localStorage：引导是这台电脑上的界面教学，不随设置同步。
 */

const PENDING_KEY = "ka-tour-pending";
const SKIPPED_KEY = "ka-tour-skipped";
export const TOUR_START_EVENT = "ka:tour-start";
/** 引导请求数据流画布把某个节点移到中央（FlowDiagram 监听）。 */
export const FLOW_FOCUS_EVENT = "ka:flow-focus";
/** ChatPanel 发出提问时派发；引导据此完成最后一步。 */
export const ASK_SENT_EVENT = "ka:ask-sent";

function read(key: string): string {
  try { return localStorage.getItem(key) ?? ""; } catch { return ""; }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch { /* 隐私模式等：忽略 */ }
}

export function tourPending(): boolean {
  return read(PENDING_KEY) === "1";
}

export function requestTour(): void {
  write(PENDING_KEY, "1");
  write(SKIPPED_KEY, null);
  if (typeof window !== "undefined") window.dispatchEvent(new Event(TOUR_START_EVENT));
}

export function finishTour(): void {
  write(PENDING_KEY, null);
  write(SKIPPED_KEY, null);
}

/** 用户主动跳过的可选步骤（如向量化）。 */
export function skippedSteps(): Set<string> {
  return new Set(read(SKIPPED_KEY).split(",").filter(Boolean));
}

export function skipStep(id: string): Set<string> {
  const next = skippedSteps();
  next.add(id);
  write(SKIPPED_KEY, [...next].join(","));
  return next;
}

export function focusFlowNode(id: string): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(FLOW_FOCUS_EVENT, { detail: { id } }));
  }
}

export function notifyAskSent(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(ASK_SENT_EVENT));
}
