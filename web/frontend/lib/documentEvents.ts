/**
 * lib/documentEvents.ts — 文档库变化的页面内事件：上传等操作完成后通知文档列表重新加载。
 */

export const DOCUMENTS_CHANGED_EVENT = "ka:documents-changed";

export function notifyDocumentsChanged(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(DOCUMENTS_CHANGED_EVENT));
}
