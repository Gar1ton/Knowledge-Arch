"use client";
/**
 * 联网补充节点的状态区：每个数据库最近是否成功、最近的错误、OpenAlex 今日免费额度。
 * 数据来自 `/api/capabilities` 里 external 环节的 detail（后端 `_overlay_external_runtime`）。
 * OpenAlex 额度用完后当天自动跳过（用户 11-20：走免费，用完拉倒），这里只负责说清楚。
 */
import React from "react";
import type { PipelineStage } from "@/lib/api";
import type { TFunc } from "@/lib/i18n";
import { ENGINE_LABEL } from "./model";

type EngineRuntime = {
  last_ok_at?: string;
  last_error?: string;
  last_error_at?: string;
  quota_limit_usd?: number | null;
  quota_remaining_usd?: number | null;
  exhausted?: boolean;
  exhausted_until?: string;
};

const ENGINES = ["crossref", "openalex", "openlibrary", "arxiv", "semantic_scholar"];

function engineLine(name: string, enabled: boolean, rt: EngineRuntime | undefined,
                    keys: Record<string, boolean>, t: TFunc): { text: string; tone: string } {
  if (!enabled) return { text: "—", tone: "muted" };
  if (name === "semantic_scholar" && !keys.semantic_scholar) {
    return { text: t("external_status_needs_key"), tone: "warn" };
  }
  if (rt?.exhausted) return { text: t("external_status_exhausted"), tone: "warn" };
  const lastOk = rt?.last_ok_at ? Date.parse(rt.last_ok_at) : 0;
  const lastErr = rt?.last_error_at ? Date.parse(rt.last_error_at) : 0;
  if (rt?.last_error && lastErr > lastOk) return { text: rt.last_error, tone: "error" };
  if (lastOk) return { text: t("external_status_ok"), tone: "ok" };
  return { text: t("external_status_never"), tone: "muted" };
}

export function ExternalActions({ stage, t }: { stage: PipelineStage; t: TFunc }) {
  const detail = (stage.detail || {}) as Record<string, unknown>;
  const enabled = new Set((detail.engines as string[] | undefined) ?? []);
  const runtime = (detail.runtime as Record<string, EngineRuntime> | undefined) ?? {};
  const keys = (detail.keys as Record<string, boolean> | undefined) ?? {};
  const oa = runtime.openalex;
  const quota = oa && typeof oa.quota_remaining_usd === "number" && typeof oa.quota_limit_usd === "number"
    ? `$${oa.quota_remaining_usd.toFixed(3)} / $${oa.quota_limit_usd.toFixed(2)}`
    : null;
  return (
    <div className="flow-node-actions" onClick={(e) => e.stopPropagation()}>
      <div className="flow-meta-strip">
        <span className="flow-meta-chip">{t("external_status_title")}</span>
        {quota && <span className="flow-meta-chip">OpenAlex · {t("external_status_quota")} {quota}</span>}
        {keys.openalex && <span className="flow-meta-chip">OpenAlex · {t("external_status_key_set")}</span>}
      </div>
      <table className="flow-external-status">
        <tbody>
          {ENGINES.map((name) => {
            const line = engineLine(name, enabled.has(name), runtime[name], keys, t);
            return (
              <tr key={name}>
                <td>{ENGINE_LABEL[name] ?? name}</td>
                <td className={`flow-external-status--${line.tone}`}>{line.text}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
