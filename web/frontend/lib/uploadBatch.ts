/**
 * 文件面板「逐个上传」的批次计数（只在本窗口内存里）。
 *
 * 为什么存在：后端每个上传请求只有一份文件，摄入任务快照不知道这是一批里的第几份；
 * 进度栏轮询时读这里，把「文件 X/Y」拼到摄入行上。没有批次时为 null。
 */

export type UploadBatch = { current: number; total: number };

let batch: UploadBatch | null = null;

/** 开始上传第 `current` 份（从 1 起）；传 null 表示这一批结束。 */
export function setUploadBatch(next: UploadBatch | null): void {
  batch = next;
}

export function getUploadBatch(): UploadBatch | null {
  return batch;
}
