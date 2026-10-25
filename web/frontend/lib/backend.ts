/**
 * lib/backend.ts — 后端地址与访问令牌的唯一来源（以及与桌面壳的桥）。
 *
 * - 桌面壳（Tauri）里：`invoke("backend_info")` 取 `{base_url, token}`。后端每次启动端口与
 *   token 都会变，壳在后端重启后发 `backend://ready` 事件，这里据此丢弃缓存。
 * - 开发期（`next dev`）：请求走同源 `/api/*`，由 next.config 的 rewrite 代理到本机后端；
 *   token 取构建期环境变量 `NEXT_PUBLIC_KA_TOKEN`（与启动后端时的 `KA_TOKEN` 相同）。
 *
 * 所有请求带 `Authorization: Bearer <token>`；浏览器导航类请求（下载、PDF.js 加载）无法带
 * 请求头，改用 `?access_token=`（后端只对 GET 接受）。没有登录页、没有 cookie。
 */

export type BackendInfo = { base_url: string; token: string };

/** 壳上报的后端状态（崩溃页用）。 */
export type BackendState = {
  state: "starting" | "ready" | "crashed" | "stopped";
  base_url?: string;
  error?: string;
  exit_code?: number | null;
  log_tail?: string[];
  log_dir?: string;
};

export const BACKEND_READY_EVENT = "backend://ready";
export const BACKEND_EXITED_EVENT = "backend://exited";
export const MENU_PREFERENCES_EVENT = "menu://preferences";

let cached: BackendInfo | null = null;
let pending: Promise<BackendInfo> | null = null;
let readyListenerInstalled = false;

/** 是否运行在桌面壳的窗口里（Tauri 2 注入 `__TAURI_INTERNALS__`）。 */
export function inDesktopShell(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/** 调用桌面壳命令；不在壳里时抛错（调用方应先判断 inDesktopShell）。 */
export async function invokeShell<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  if (!inDesktopShell()) throw new Error(`desktop shell command unavailable: ${command}`);
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(command, args);
}

/** 订阅桌面壳事件；不在壳里时返回空的取消函数。 */
export async function onShellEvent<T>(
  event: string,
  handler: (payload: T) => void,
): Promise<() => void> {
  if (!inDesktopShell()) return () => undefined;
  const { listen } = await import("@tauri-apps/api/event");
  return listen<T>(event, (e) => handler(e.payload));
}

function devInfo(): BackendInfo {
  return { base_url: "", token: process.env.NEXT_PUBLIC_KA_TOKEN ?? "" };
}

async function installReadyListener(): Promise<void> {
  if (readyListenerInstalled || !inDesktopShell()) return;
  readyListenerInstalled = true;
  await onShellEvent<BackendInfo>(BACKEND_READY_EVENT, (info) => {
    cached = info && info.base_url ? info : null;
  });
  await onShellEvent(BACKEND_EXITED_EVENT, () => {
    cached = null;
  });
}

/** 当前后端的地址与 token；壳里会等到后端就绪（失败时抛出壳给的原因）。 */
export async function backendInfo(): Promise<BackendInfo> {
  if (cached) return cached;
  if (!inDesktopShell()) {
    cached = devInfo();
    return cached;
  }
  if (!pending) {
    pending = (async () => {
      await installReadyListener();
      const info = await invokeShell<BackendInfo>("backend_info");
      cached = info;
      return info;
    })().finally(() => {
      pending = null;
    });
  }
  return pending;
}

/** 丢弃缓存（后端重启、网络错误后重取）。 */
export function resetBackendInfo(): void {
  cached = null;
}

/** 后端 URL：path 必须以 `/api/` 开头。 */
export async function apiUrl(path: string): Promise<string> {
  const { base_url } = await backendInfo();
  return `${base_url.replace(/\/$/, "")}${path}`;
}

export async function authHeaders(): Promise<Record<string, string>> {
  const { token } = await backendInfo();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** 浏览器导航 / PDF.js 用：带 `?access_token=` 的完整 URL（仅限 GET）。 */
export async function authedUrl(path: string): Promise<string> {
  const { token } = await backendInfo();
  const url = await apiUrl(path);
  if (!token) return url;
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}access_token=${encodeURIComponent(token)}`;
}

/** 壳里后端的状态（崩溃页、启动页用）；不在壳里时视为 ready。 */
export async function backendState(): Promise<BackendState> {
  if (!inDesktopShell()) return { state: "ready" };
  return invokeShell<BackendState>("backend_state");
}

/** 让壳重启后端进程（崩溃页「重启服务」）；返回新的连接信息。 */
export async function restartBackendProcess(): Promise<BackendInfo> {
  resetBackendInfo();
  const info = await invokeShell<BackendInfo>("restart_backend");
  cached = info;
  return info;
}

/** 原生选择文件夹对话框；不在壳里返回 null（调用方回退到内置目录浏览）。 */
export async function pickFolder(title?: string): Promise<string | null> {
  if (!inDesktopShell()) return null;
  return invokeShell<string | null>("pick_folder", { title: title ?? null });
}

/** 打开日志目录（系统文件管理器）。 */
export async function openLogDir(): Promise<void> {
  if (!inDesktopShell()) return;
  await invokeShell<void>("open_log_dir");
}

/** 系统通知（后台任务完成时）；不在壳里时静默忽略。 */
export async function notifyDesktop(title: string, body: string): Promise<void> {
  if (!inDesktopShell()) return;
  try {
    await invokeShell<void>("notify", { title, body });
  } catch {
    /* 通知失败不影响业务 */
  }
}

/** 把壳负责的偏好（关闭到托盘、开机启动、检查更新、数据目录）交给壳保存并即时生效。 */
export async function setShellPref(key: string, value: unknown): Promise<void> {
  if (!inDesktopShell()) return;
  await invokeShell<void>("set_shell_pref", { key, value });
}

/** 壳保存的偏好（数据目录覆盖等）；不在壳里返回空对象。 */
export async function getShellPrefs(): Promise<Record<string, unknown>> {
  if (!inDesktopShell()) return {};
  return invokeShell<Record<string, unknown>>("get_shell_prefs");
}

// ─── 自动更新（桌面壳 Cargo feature `updater`；未启用时 enabled=false）──────────

export const UPDATE_AVAILABLE_EVENT = "update://available";

export type UpdateInfo = {
  enabled: boolean;
  available: boolean;
  current_version: string;
  version?: string | null;
  notes?: string | null;
};

export async function checkUpdate(): Promise<UpdateInfo | null> {
  if (!inDesktopShell()) return null;
  return invokeShell<UpdateInfo>("check_update");
}

/** 下载、校验签名并安装；成功后壳会重启应用（本调用通常不会返回）。 */
export async function installUpdate(): Promise<void> {
  await invokeShell<void>("install_update");
}
