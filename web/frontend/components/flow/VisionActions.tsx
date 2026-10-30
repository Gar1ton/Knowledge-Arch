"use client";
/**
 * Upload / Chunk 节点的识图操作：显示图表空位统计，「测试识图模型」发一张小图确认所选模型真能看图。
 * 补全描述本身在文件面板的集合操作里手动触发（先估算），这里不提供开始键。
 */
import React, { useEffect, useState } from "react";
import type { TFunc } from "@/lib/i18n";
import { getVisionStatus, testVisionModel, type VisionStatus } from "@/lib/visionApi";

type Notify = (message: string, kind: "ok" | "error" | "info") => void;

export function VisionActions({ t, onNotify }: { t: TFunc; onNotify: Notify }) {
  const [status, setStatus] = useState<VisionStatus | null>(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    let alive = true;
    getVisionStatus().then((s) => { if (alive) setStatus(s); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  if (!status?.enabled) return null;

  const test = async () => {
    setTesting(true);
    try {
      const result = await testVisionModel();
      if (result.status === "ok") {
        onNotify(t("vision_test_ok", { model: result.model ?? "", ms: result.elapsed_ms ?? 0 }), "ok");
      } else if (result.status === "warning") {
        onNotify(`${t("vision_test_warning")}（${result.answer ?? ""}）`, "info");
      } else {
        onNotify(result.message || result.code || "error", "error");
      }
    } catch (err) {
      onNotify(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setTesting(false);
    }
  };

  const done = status.counts.done ?? 0;
  return (
    <div className="flow-node-actions" onClick={(e) => e.stopPropagation()}>
      <div className="flow-meta-strip">
        <span className="flow-meta-chip">{status.model || t("vision_no_model_hint")}</span>
        <span className="flow-meta-chip">
          {done}/{status.figures} · {t("vision_figures_missing")} {status.missing}
        </span>
      </div>
      <div className="flow-provider-actions">
        <button
          type="button"
          className="flow-dep-button"
          disabled={testing || !status.configured}
          onClick={() => void test()}
        >
          {testing ? "…" : t("vision_test")}
        </button>
      </div>
    </div>
  );
}
