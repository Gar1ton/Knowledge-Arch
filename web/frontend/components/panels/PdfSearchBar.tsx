"use client";
import React, { useEffect, useRef } from "react";
import { IconButton } from "@/components/ds/IconButton";
import { useI18n } from "@/lib/i18n";

/**
 * PDF 阅读器顶栏下的查找条。只管输入与按键；查找本身由 PdfViewer 用 lib/pdfSearch 完成。
 * Enter 下一个、Shift+Enter 上一个、Esc 关闭。
 */
export function PdfSearchBar({
  query,
  onQueryChange,
  total,
  current,
  status,
  onPrev,
  onNext,
  onClose,
}: {
  query: string;
  onQueryChange: (value: string) => void;
  total: number;
  current: number;
  status: "idle" | "searching" | "done" | "no_text";
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  let summary = "";
  if (status === "searching") summary = t("pdf_search_searching");
  else if (status === "no_text") summary = t("pdf_search_no_text");
  else if (status === "done" && query.trim()) {
    summary = total > 0 ? `${current + 1}/${total}` : t("pdf_search_no_results");
  }

  return (
    <div
      style={{
        height: 34,
        display: "flex",
        alignItems: "center",
        gap: 6,
        padding: "0 10px",
        borderBottom: "1px solid var(--border)",
        background: "var(--surface)",
      }}
    >
      <input
        ref={inputRef}
        value={query}
        placeholder={t("pdf_search_placeholder")}
        aria-label={t("pdf_search")}
        onChange={(e) => onQueryChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            if (e.shiftKey) onPrev();
            else onNext();
          } else if (e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            onClose();
          }
        }}
        style={{
          flex: 1,
          minWidth: 0,
          height: 24,
          padding: "0 8px",
          fontSize: 12,
          fontFamily: "var(--font-sans)",
          color: "var(--fg)",
          background: "var(--bg-inset)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-sm)",
          outline: "none",
        }}
      />
      <span
        style={{
          minWidth: 56,
          textAlign: "right",
          fontSize: 11,
          color: status === "no_text" ? "var(--warn)" : "var(--fg-muted)",
          fontFamily: "var(--font-mono)",
          whiteSpace: "nowrap",
        }}
      >
        {summary}
      </span>
      <IconButton name="chevU" label={t("pdf_search_prev")} onClick={onPrev} style={{ opacity: total ? 1 : 0.45 }} />
      <IconButton name="chevD" label={t("pdf_search_next")} onClick={onNext} style={{ opacity: total ? 1 : 0.45 }} />
      <IconButton name="x" label={t("pdf_search_close")} onClick={onClose} />
    </div>
  );
}
