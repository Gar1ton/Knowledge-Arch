// 识图（2.1.0）：补全图表描述的任务接口。单列文件是因为 lib/api.ts 已过 600 行。
import { apiFetch } from "@/lib/api";

/** 后台识图任务快照（kacore/vision_job.py 的 to_dict）。 */
export interface VisionJob {
  job_id: string;
  collection: string;
  model: string;
  status: "running" | "success" | "partial_failure" | "cancelled" | "error";
  total_docs: number;
  processed_docs: number;
  total_figures: number;
  described: number;
  failed: number;
  skipped: number;
  prompt_tokens: number;
  completion_tokens: number;
  progress_percent: number;
  current_doc: string;
  current_figure: string;
  recent_error: string;
  cancel_requested: boolean;
  started_at: number;
  finished_at: number | null;
}

/** 集合（或全部）的图表空位统计与识图开关、模型。 */
export interface VisionStatus {
  collection: string;
  enabled: boolean;
  model: string;
  configured: boolean;
  max_concurrency: number;
  documents: number;
  figures: number;
  counts: Record<string, number>;
  missing: number;
  scanned_documents: number;
  estimated_prompt_tokens: number;
  estimated_completion_tokens: number;
  active_job: VisionJob | null;
  last_job: VisionJob | null;
}

export interface VisionTestResult {
  status: "ok" | "warning" | "error";
  model?: string;
  answer?: string;
  code?: string;
  message?: string;
  elapsed_ms?: number;
}

export async function getVisionStatus(collection?: string | null): Promise<VisionStatus> {
  const query = collection ? `?collection=${encodeURIComponent(collection)}` : "";
  return apiFetch(`/api/vision/status${query}`, { timeoutMs: 8_000 });
}

/** 开始补全；后端只处理缺描述的图（pending / failed），进度交给 getActiveVisionJob 轮询。 */
export async function startVisionBuild(collection?: string | null): Promise<VisionJob> {
  const res = await apiFetch<{ job: VisionJob }>("/api/vision/build", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ collection: collection || null }),
  });
  return res.job;
}

export async function getActiveVisionJob(): Promise<VisionJob | null> {
  const res = await apiFetch<{ job: VisionJob | null }>("/api/vision/build/active", {
    timeoutMs: 4_000,
  });
  return res.job;
}

export async function cancelVisionBuild(): Promise<VisionJob | null> {
  const res = await apiFetch<{ job: VisionJob | null }>("/api/vision/build/cancel", {
    method: "POST",
  });
  return res.job;
}

/** 发一张小测试图给所选识图模型，确认它真的能看图。 */
export async function testVisionModel(): Promise<VisionTestResult> {
  return apiFetch("/api/vision/test", { method: "POST", timeoutMs: 180_000 });
}
