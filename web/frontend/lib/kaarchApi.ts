/**
 * 3.0.0 偏好设置「备份与迁移」：`.kaarch` 导出导入。
 * 后端：`kacore/managers/kaarch.py`（POST /api/kaarch/export|inspect|import、GET /api/kaarch/job）。
 * 导出与导入在后台任务里跑，界面轮询 job 看进度。
 */
import { apiFetch } from "./api";

export type KaarchPart = "settings" | "secrets" | "library";
export type KaarchMode = "overwrite" | "merge";

export interface KaarchJob {
  job_id: string;
  action: "export" | "import_overwrite" | "import_merge";
  status: "running" | "success" | "error";
  stage: string;
  progress: number;
  detail: string;
  error: string;
  result: Record<string, unknown>;
  started_at: string;
  finished_at: string;
}

export interface KaarchInfo {
  path: string;
  size: number;
  app_version: string;
  created_at: string;
  parts: KaarchPart[];
  counts: { documents?: number; local_documents?: number; collections?: number; notes?: number; conversations?: number };
  secret_count: number;
  settings_diff: number;
  /** null = 任一边没配 Embedding 模型，无从比较。 */
  embedding_same: boolean | null;
  local_documents: number;
  local_empty: boolean;
}

const json = (body: unknown) => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export function startKaarchExport(folder: string, parts: KaarchPart[], passphrase: string): Promise<KaarchJob> {
  return apiFetch<KaarchJob>("/api/kaarch/export", json({ folder, parts, passphrase }));
}

export function inspectKaarch(path: string): Promise<KaarchInfo> {
  return apiFetch<KaarchInfo>("/api/kaarch/inspect", { ...json({ path }), timeoutMs: 60_000 });
}

export function startKaarchImport(path: string, mode: KaarchMode, passphrase: string): Promise<KaarchJob> {
  return apiFetch<KaarchJob>("/api/kaarch/import", { ...json({ path, mode, passphrase }), timeoutMs: 60_000 });
}

export function getKaarchJob(): Promise<KaarchJob | null> {
  return apiFetch<KaarchJob | null>("/api/kaarch/job");
}
