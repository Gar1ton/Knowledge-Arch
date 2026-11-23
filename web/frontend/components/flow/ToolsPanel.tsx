"use client";
/**
 * 数据流「联网补充」节点的工具面板：开关、设置、试调用、最近调用（用户 11-22：方便调试与管理来源）。
 * 全部由后端的工具声明生成（`GET /api/tools`）：加一个工具，这里自动多一行；本文件不写任何工具名。
 * 设置走通用的设置接口（home = flow.external.tools）；试调用只在用户按「调用」时发出真实请求。
 */
import React, { useCallback, useEffect, useState } from "react";
import type { TFunc } from "@/lib/i18n";
import { getSettingsSchema, patchSetting, type SettingItem } from "@/lib/settingsApi";
import {
  argumentsLabel,
  listTools,
  recentToolCalls,
  testTool,
  type RecentToolCall,
  type ToolInfo,
} from "@/lib/externalApi";

type Notify = (message: string, kind: "ok" | "error" | "info") => void;

const TOOLS_HOME = "flow.external.tools";

type Snapshot = { tools: ToolInfo[]; values: Record<string, SettingItem>; recent: RecentToolCall[] };

async function fetchSnapshot(): Promise<Snapshot> {
  const [tools, schema, recent] = await Promise.all([
    listTools(), getSettingsSchema(TOOLS_HOME), recentToolCalls(20),
  ]);
  return { tools, values: Object.fromEntries(schema.items.map((i) => [i.key, i])), recent };
}

function argsTemplate(tool: ToolInfo | undefined): string {
  const required = tool?.input_schema.required ?? [];
  return JSON.stringify(Object.fromEntries(required.map((k) => [k, ""])), null, 0);
}

function shortTime(iso: string): string {
  return iso
    ? new Date(iso).toLocaleString(undefined, { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
    : "";
}

export function ToolsPanel({ t, onNotify }: { t: TFunc; onNotify: Notify }) {
  const [tools, setTools] = useState<ToolInfo[] | null>(null);
  const [values, setValues] = useState<Record<string, SettingItem>>({});
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [recent, setRecent] = useState<RecentToolCall[]>([]);
  const [pick, setPick] = useState("");
  const [args, setArgs] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const apply = useCallback((snap: Snapshot) => {
    setTools(snap.tools);
    setValues(snap.values);
    setRecent(snap.recent);
  }, []);
  const load = useCallback(async () => {
    try {
      apply(await fetchSnapshot());
    } catch (err) {
      onNotify(err instanceof Error ? err.message : String(err), "error");
    }
  }, [apply, onNotify]);

  useEffect(() => {
    let alive = true;
    fetchSnapshot()
      .then((snap) => { if (alive) apply(snap); })
      .catch((err) => onNotify(err instanceof Error ? err.message : String(err), "error"));
    return () => { alive = false; };
  }, [apply, onNotify]);

  const save = async (key: string, value: unknown) => {
    try {
      await patchSetting(key, value);
      setDrafts((d) => { const next = { ...d }; delete next[key]; return next; });
      await load();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : String(err), "error");
    }
  };

  const runTest = async () => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(args || "{}");
    } catch {
      parsed = null;
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      onNotify(t("tools_bad_json"), "error");
      return;
    }
    setBusy(true);
    try {
      const res = await testTool(pick, parsed as Record<string, unknown>);
      setResult(res.text);
      await load();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setBusy(false);
    }
  };

  if (!tools) return null;
  const ready = tools.filter((tool) => tool.ready);

  return (
    <div className="flow-node-actions" onClick={(e) => e.stopPropagation()}>
      <div className="flow-meta-strip"><span className="flow-meta-chip">{t("tools_title")}</span></div>
      <div className="flow-tools">
        {tools.map((tool) => (
          <div key={tool.name} className="flow-tool">
            <label className="flow-tool-head" title={tool.description}>
              <input type="checkbox" checked={tool.enabled}
                onChange={() => void save(tool.enabled_key, !tool.enabled)} />
              <code>{tool.name}</code>
              <ToolState tool={tool} t={t} />
            </label>
            {Object.entries(tool.info).length > 0 && (
              <div className="flow-tool-info">
                {Object.entries(tool.info).map(([k, v]) => <span key={k} className="flow-meta-chip">{k}: {v}</span>)}
              </div>
            )}
            {tool.enabled && tool.settings.map((setting) => {
              const item = values[setting.key];
              const secret = setting.kind === "secret";
              const shown = drafts[setting.key] ?? (secret ? "" : String(item?.value ?? ""));
              return (
                <div key={setting.key} className="flow-tool-setting">
                  <span>{setting.name}{setting.required ? " *" : ""}</span>
                  <input type={secret ? "password" : "text"} value={shown}
                    placeholder={secret && item?.is_set ? t("tools_secret_set") : ""}
                    onChange={(e) => setDrafts((d) => ({ ...d, [setting.key]: e.target.value }))} />
                  <button type="button" className="flow-dep-button"
                    disabled={drafts[setting.key] === undefined}
                    onClick={() => void save(setting.key, setting.kind === "int"
                      ? Number(drafts[setting.key] || 0) : drafts[setting.key])}>
                    {t("tools_save")}
                  </button>
                </div>
              );
            })}
          </div>
        ))}
      </div>

      <div className="flow-meta-strip"><span className="flow-meta-chip">{t("tools_test")}</span></div>
      <div className="flow-tool-test" title={t("tools_test_hint")}>
        <select value={pick} onChange={(e) => {
          setPick(e.target.value);
          setArgs(argsTemplate(tools.find((tool) => tool.name === e.target.value)));
          setResult(null);
        }}>
          <option value="">—</option>
          {ready.map((tool) => <option key={tool.name} value={tool.name}>{tool.name}</option>)}
        </select>
        <input className="mono" value={args} onChange={(e) => setArgs(e.target.value)} />
        <button type="button" className="flow-dep-button" disabled={!pick || busy}
          onClick={() => void runTest()}>{busy ? "…" : t("tools_call")}</button>
      </div>
      {result !== null && <pre className="flow-tool-result">{result}</pre>}

      <div className="flow-meta-strip"><span className="flow-meta-chip">{t("tools_recent")}</span></div>
      <div className="flow-tool-log">
        {recent.length === 0 && <div className="flow-tool-empty">{t("tools_recent_empty")}</div>}
        {recent.map((call) => (
          <div key={call.call_id} className={`flow-tool-logrow ${call.is_error ? "is-error" : ""}`}>
            <span>{shortTime(call.started_at)}</span>
            <code>{call.tool}</code>
            <span className="flow-tool-args" title={argumentsLabel(call.arguments)}>
              {call.is_error ? call.blocks[0]?.text : argumentsLabel(call.arguments)}
            </span>
            <span>{call.is_error ? "" : t("tools_blocks", { n: call.blocks.length })} · {t(
              call.actor === "agent" ? "tools_actor_agent" : call.actor === "test" ? "tools_actor_test" : "tools_actor_ui",
            )}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ToolState({ tool, t }: { tool: ToolInfo; t: TFunc }) {
  if (!tool.enabled) return <span className="flow-tool-state muted">{t("tools_off")}</span>;
  if (tool.missing.length) {
    return <span className="flow-tool-state warn">{t("tools_missing", { names: tool.missing.join(", ") })}</span>;
  }
  const lastOk = tool.last_ok_at ? Date.parse(tool.last_ok_at) : 0;
  const lastErr = tool.last_error_at ? Date.parse(tool.last_error_at) : 0;
  if (tool.last_error && lastErr > lastOk) {
    return <span className="flow-tool-state error" title={tool.last_error}>{t("tools_last_error", { error: tool.last_error })}</span>;
  }
  if (lastOk) return <span className="flow-tool-state ok">{t("tools_last_ok", { time: shortTime(tool.last_ok_at) })}</span>;
  return <span className="flow-tool-state muted">{t("tools_ready")} · {t("tools_never")}</span>;
}
