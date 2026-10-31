"use client";
import React, { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Button } from "@/components/ds/Button";
import { useToast } from "@/components/ui/Toast";
import { useConsole } from "@/lib/ConsoleContext";
import { useI18n } from "@/lib/i18n";
import { getVisionStatus, startVisionBuild, type VisionStatus } from "@/lib/visionApi";
import { Z } from "@/lib/zLayers";

/**
 * 「补全图表描述（仅缺失）」确认框：先估算再开始。
 *
 * 数字全部来自后端 `/api/vision/status`（图数、缺描述的图、估算 token、扫描页文档数）；
 * 识图没开或没选模型时不给开始键，只给「去数据流设置」。视觉与 FullTextConfirmDialog 同款。
 */
export function VisionBuildDialog({
  collection,
  onClose,
}: {
  collection: string | null;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const { toast } = useToast();
  const { setView } = useConsole();
  const [status, setStatus] = useState<VisionStatus | null>(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    let alive = true;
    getVisionStatus(collection)
      .then((s) => { if (alive) setStatus(s); })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : String(e)); });
    return () => { alive = false; };
  }, [collection]);

  async function start() {
    setStarting(true);
    try {
      await startVisionBuild(collection);
      toast(t("vision_started"), "ok");
      onClose();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setStarting(false);
    }
  }

  const nf = new Intl.NumberFormat();
  const running = status?.active_job?.status === "running";
  const blocker = !status
    ? ""
    : !status.enabled
      ? t("vision_off_hint")
      : !status.configured
        ? t("vision_no_model_hint")
        : "";
  const rows: [string, string][] = status
    ? [
        [t("vision_scope"), collection || t("vision_scope_all")],
        [t("vision_model"), status.model || "—"],
        [t("vision_documents"), nf.format(status.documents)],
        [t("vision_figures_total"), nf.format(status.figures)],
        [t("vision_figures_missing"), nf.format(status.missing)],
        [
          t("vision_est_tokens"),
          `≈ ${nf.format(status.estimated_prompt_tokens)} / ${nf.format(status.estimated_completion_tokens)}`,
        ],
      ]
    : [];

  if (typeof document === "undefined") return null;

  return createPortal(
    <div
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,.35)", display: "flex",
        alignItems: "center", justifyContent: "center", zIndex: Z.dialog,
      }}
      onClick={(e) => e.target === e.currentTarget && !starting && onClose()}
    >
      <div
        role="dialog"
        aria-label={t("vision_dialog_title")}
        style={{
          background: "var(--surface)", border: "1px solid var(--border)",
          borderRadius: "var(--radius-3xl)", padding: 24, width: 430,
          boxShadow: "var(--shadow-pop)", display: "flex", flexDirection: "column", gap: 16,
        }}
      >
        <div>
          <h3 style={{ margin: "0 0 5px", fontSize: 15, fontWeight: 700, color: "var(--heading)" }}>
            {t("vision_dialog_title")}
          </h3>
          <p style={{ margin: 0, fontSize: 11, lineHeight: 1.6, color: "var(--fg-muted)" }}>
            {t("vision_dialog_body")}
          </p>
        </div>
        {error && <div style={{ fontSize: 12, color: "var(--danger)" }}>{error}</div>}
        {!status && !error && (
          <div style={{ fontSize: 12, color: "var(--fg-subtle)" }}>{t("vision_loading")}</div>
        )}
        {rows.length > 0 && (
          <div
            style={{
              background: "var(--bg-inset)", border: "1px solid var(--border)",
              borderRadius: 10, overflow: "hidden",
            }}
          >
            {rows.map(([label, value], i) => (
              <div
                key={label}
                style={{
                  display: "flex", justifyContent: "space-between", gap: 12, padding: "8px 14px",
                  borderTop: i > 0 ? "1px solid var(--border)" : undefined,
                }}
              >
                <span style={{ fontSize: 12, color: "var(--fg-muted)" }}>{label}</span>
                <span
                  style={{
                    fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--fg)", fontWeight: 600,
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }}
                >
                  {value}
                </span>
              </div>
            ))}
          </div>
        )}
        {status && status.scanned_documents > 0 && (
          <div style={{ fontSize: 11, lineHeight: 1.6, color: "var(--fg-muted)" }}>
            {t("vision_scanned_note", { count: status.scanned_documents })}
          </div>
        )}
        {(blocker || running) && (
          <div
            style={{
              fontSize: 11, lineHeight: 1.6, color: "var(--warn)",
              background: "color-mix(in srgb, var(--warn) 10%, transparent)",
              border: "1px solid color-mix(in srgb, var(--warn) 30%, transparent)",
              borderRadius: 8, padding: "8px 12px",
            }}
          >
            {running ? t("vision_already_running") : blocker}
          </div>
        )}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", flexWrap: "wrap" }}>
          <Button variant="ghost" size="sm" disabled={starting} onClick={onClose}>
            {t("btn_cancel")}
          </Button>
          {blocker ? (
            <Button size="sm" onClick={() => { setView("flow"); onClose(); }}>
              {t("vision_open_workflow")}
            </Button>
          ) : (
            <Button
              size="sm"
              loading={starting}
              disabled={!status || running || status.missing === 0}
              onClick={() => void start()}
            >
              {status && status.missing === 0 ? t("vision_nothing_missing") : t("vision_start")}
            </Button>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
