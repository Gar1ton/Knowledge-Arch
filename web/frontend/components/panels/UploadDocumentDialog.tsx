"use client";
/** 上传文档对话框：拖放或选择 PDF / Markdown / 文本，选目标本地集合，逐个上传。 */
import React, { useState } from "react";
import { createPortal } from "react-dom";
import { Z } from "@/lib/zLayers";
import { IconButton } from "@/components/ds/IconButton";
import { Icon } from "@/components/ds/Icon";
import { Select } from "@/components/ds/Select";
import { formatBytes } from "@/components/flow/SyncActions";
import { notifyDocumentsChanged } from "@/lib/documentEvents";
import { uploadDocument } from "@/lib/api";
import { setUploadBatch } from "@/lib/uploadBatch";
import { useToast } from "@/components/ui/Toast";
import { useI18n } from "@/lib/i18n";

// ─── Upload Document Dialog ────────────────────────────────────

interface UploadDocumentDialogProps {
  defaultCollection: string | null;
  localCollections: string[];
  onClose: () => void;
}

// 与后端 IngestManager 的解析分支一致：PDF 走 pymupdf4llm，md / markdown / txt 按纯文本单页。
const UPLOAD_EXTENSIONS = [".pdf", ".md", ".markdown", ".txt"];

function isUploadable(file: File): boolean {
  const name = file.name.toLowerCase();
  return UPLOAD_EXTENSIONS.some((ext) => name.endsWith(ext));
}

export function UploadDocumentDialog({ defaultCollection, localCollections, onClose }: UploadDocumentDialogProps) {
  const { t } = useI18n();
  const { toast } = useToast();
  const [files, setFiles] = useState<File[]>([]);
  const [chosenCollection, setCollection] = useState("");
  // 未手选时用默认集合；集合列表晚于对话框到达时也能补上。
  const collection = chosenCollection || defaultCollection || localCollections[0] || "";
  const [uploading, setUploading] = useState(false);
  const [done, setDone] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = React.useRef<HTMLInputElement>(null);

  // 追加文件（按「名称 + 大小」去重），不支持的格式提示后忽略。
  function addFiles(list: FileList | null) {
    if (!list) return;
    const picked = Array.from(list);
    const rejected = picked.filter((f) => !isUploadable(f));
    if (rejected.length) {
      toast(t("file_upload_unsupported", { names: rejected.map((f) => f.name).join(", ") }), "error");
    }
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => `${f.name}:${f.size}`));
      return [...prev, ...picked.filter((f) => isUploadable(f) && !seen.has(`${f.name}:${f.size}`))];
    });
  }

  // 逐个上传（后端每个请求一份文件）；失败的留在列表里，全部成功才关闭。
  async function handleUpload() {
    if (!files.length) { toast(t("file_upload_no_file"), "error"); return; }
    if (!collection) { toast(t("file_build_no_collection"), "error"); return; }
    setUploading(true);
    setDone(0);
    const failed: File[] = [];
    const errors: string[] = [];
    for (const [index, file] of files.entries()) {
      setUploadBatch({ current: index + 1, total: files.length });
      try {
        await uploadDocument(file, collection);
      } catch (err: unknown) {
        failed.push(file);
        errors.push(`${file.name}: ${err instanceof Error ? err.message : String(err)}`);
      }
      setDone((n) => n + 1);
    }
    setUploadBatch(null);
    setUploading(false);
    const ok = files.length - failed.length;
    if (ok) notifyDocumentsChanged();
    if (!failed.length) {
      toast(files.length === 1
        ? t("file_upload_success", { name: files[0].name })
        : t("file_upload_batch_done", { count: ok }), "ok");
      onClose();
      return;
    }
    setFiles(failed);
    toast(ok
      ? t("file_upload_batch_partial", { ok, failed: failed.length, message: errors.join("; ") })
      : t("file_upload_error", { message: errors.join("; ") }), "error");
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    if (!uploading) addFiles(e.dataTransfer.files);
  }

  if (typeof document === "undefined") return null;

  const totalBytes = files.reduce((sum, f) => sum + f.size, 0);
  const active = files.length > 0 || dragOver;

  return createPortal(
    <div
      onClick={(e) => { if (e.target === e.currentTarget && !uploading) onClose(); }}
      style={{
        position: "fixed", inset: 0, zIndex: Z.dialog,
        background: "rgba(22,23,26,.46)",
        display: "flex", alignItems: "center", justifyContent: "center",
        animation: "overlayIn .15s ease",
      }}
    >
      <div
        style={{
          background: "var(--surface)", border: "1px solid var(--border)",
          borderRadius: "var(--radius-2xl)", boxShadow: "var(--shadow-pop)",
          width: 440, maxWidth: "calc(100vw - 32px)",
          padding: "22px 22px 18px",
          animation: "modalIn .18s cubic-bezier(.2,.7,.2,1)",
          display: "flex", flexDirection: "column", gap: 14,
        }}
      >
        <div style={{ fontSize: 14, fontWeight: 700, color: "var(--heading)" }}>
          {t("file_upload_title")}
        </div>

        {/* Drop zone */}
        <div
          onClick={() => { if (!uploading) inputRef.current?.click(); }}
          onDrop={handleDrop}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          style={{
            border: `2px dashed ${active ? "var(--accent)" : "var(--border-strong)"}`,
            borderRadius: "var(--radius-lg)",
            padding: "20px 16px",
            textAlign: "center",
            cursor: uploading ? "default" : "pointer",
            background: active ? "var(--accent-soft)" : "var(--bg-inset)",
            transition: "all .15s",
          }}
        >
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={UPLOAD_EXTENSIONS.join(",")}
            style={{ display: "none" }}
            onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }}
          />
          <Icon name="upload" size={20} style={{ color: active ? "var(--accent)" : "var(--fg-subtle)", marginBottom: 6 }} />
          <div style={{ fontSize: 12, color: active ? "var(--accent)" : "var(--fg-subtle)", lineHeight: 1.5 }}>
            {files.length === 0
              ? t("file_upload_drop_hint")
              : files.length === 1
                ? t("file_upload_selected_one", { size: formatBytes(totalBytes) })
                : t("file_upload_selected", { count: files.length, size: formatBytes(totalBytes) })}
          </div>
        </div>

        {files.length > 0 && (
          <ul style={{
            listStyle: "none", margin: 0, padding: 0, maxHeight: 160, overflowY: "auto",
            display: "flex", flexDirection: "column", gap: 4,
          }}>
            {files.map((f) => (
              <li key={`${f.name}:${f.size}`} style={{
                display: "flex", alignItems: "center", gap: 8, fontSize: 12,
                padding: "4px 4px 4px 8px", borderRadius: "var(--radius-md)", background: "var(--bg-inset)",
              }}>
                <Icon name="doc" size={13} style={{ color: "var(--fg-subtle)", flexShrink: 0 }} />
                <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--fg)" }}>
                  {f.name}
                </span>
                <span style={{ color: "var(--fg-subtle)", flexShrink: 0 }}>{formatBytes(f.size)}</span>
                {!uploading && (
                  <IconButton
                    name="x" size={22} label={t("file_upload_remove")}
                    onClick={() => setFiles((prev) => prev.filter((x) => x !== f))}
                  />
                )}
              </li>
            ))}
          </ul>
        )}

        {/* Collection selector */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label style={{ fontSize: 12, fontWeight: 500, color: "var(--fg-muted)" }}>
            {t("file_upload_collection_label")}
          </label>
          <Select
            value={collection}
            onChange={setCollection}
            options={localCollections.map((c) => ({ value: c, label: c }))}
            size="md"
            disabled={uploading}
            style={{ width: "100%" }}
          />
        </div>

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button
            type="button" onClick={onClose} disabled={uploading}
            style={{
              background: "var(--bg-inset)", border: "1px solid var(--border-strong)",
              color: "var(--fg-muted)", borderRadius: "var(--radius-xl)",
              padding: "7px 16px", fontSize: 12.5, fontWeight: 500,
              cursor: uploading ? "not-allowed" : "pointer", fontFamily: "inherit",
            }}
          >
            {t("btn_cancel")}
          </button>
          <button
            type="button" onClick={handleUpload} disabled={!files.length || uploading}
            style={{
              background: files.length && !uploading ? "var(--accent)" : "var(--bg-inset)",
              border: "1px solid transparent",
              color: files.length && !uploading ? "var(--accent-fg, #fff)" : "var(--fg-subtle)",
              borderRadius: "var(--radius-xl)", padding: "7px 16px",
              fontSize: 12.5, fontWeight: 600,
              cursor: files.length && !uploading ? "pointer" : "not-allowed",
              fontFamily: "inherit",
              display: "flex", alignItems: "center", gap: 6,
            }}
          >
            {uploading ? (
              <>
                <span style={{
                  width: 10, height: 10, borderRadius: "50%",
                  border: "2px solid rgba(255,255,255,0.4)", borderTopColor: "#fff",
                  animation: "spin .6s linear infinite", display: "inline-block",
                }} />
                {t("file_upload_progress", { done, total: files.length })}
              </>
            ) : t("file_upload_btn")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
