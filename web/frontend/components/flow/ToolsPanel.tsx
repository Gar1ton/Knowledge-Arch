"use client";
/**
 * 数据流「联网补充」节点的工具面板：开关、设置、试调用、最近调用（用户 11-22：方便调试与管理来源）。
 * 全部由后端的工具声明生成（`GET /api/tools`）：加一个工具，这里自动多一张卡片；本文件不写任何工具名。
 * 设置走通用的设置接口（home = flow.external.tools）；试调用只在用户按「调用」时发出真实请求。
 *
 * 11-23 用户反馈「点保存之后卡了，也没有提示是否配置完成」：每个设置有明确的保存中 / 已保存 / 失败状态，
 * 已配置的 key 常显（掩码），回车保存、Esc 取消；保存结果同时发一条提示。
 */
import React, { useCallback, useEffect, useState } from "react";
import { Toggle } from "@/components/ds/Toggle";
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

function maskedOf(item: SettingItem | undefined): string {
  const v = item?.value as { set?: boolean; masked?: string } | undefined;
  return v && typeof v === "object" && v.set ? v.masked || "••••" : "";
}

export function ToolsPanel({ t, onNotify }: { t: TFunc; onNotify: Notify }) {
  const [tools, setTools] = useState<ToolInfo[] | null>(null);
  const [values, setValues] = useState<Record<string, SettingItem>>({});
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

  /** 写一个设置并刷新面板；失败抛给调用方显示在原地。 */
  const save = useCallback(async (key: string, value: unknown) => {
    await patchSetting(key, value);
    await load();
  }, [load]);

  const toggle = async (tool: ToolInfo) => {
    try {
      await save(tool.enabled_key, !tool.enabled);
      onNotify(t(tool.enabled ? "tools_turned_off" : "tools_turned_on", { name: tool.name }), "ok");
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
  const missing = tools.filter((tool) => tool.enabled && tool.missing.length).length;

  return (
    <div className="flow-node-actions tl-panel" onClick={(e) => e.stopPropagation()}
      onPointerDown={(e) => e.stopPropagation()}>
      <div className="tl-section">
        <span>{t("tools_title")}</span>
        <small>{t("tools_summary", { ready: ready.length, total: tools.length })}
          {missing ? ` · ${t("tools_summary_missing", { n: missing })}` : ""}</small>
      </div>
      <div className="tl-cards">
        {tools.map((tool) => (
          <div key={tool.name} className={`tl-card ${tool.enabled ? "" : "is-off"}`}>
            <div className="tl-head">
              <Toggle checked={tool.enabled} onChange={() => void toggle(tool)} />
              <code title={tool.description}>{tool.name}</code>
              <ToolState tool={tool} t={t} />
            </div>
            <div className="tl-desc" title={tool.description}>{tool.description}</div>
            {Object.entries(tool.info).length > 0 && (
              <div className="tl-info">
                {Object.entries(tool.info).map(([k, v]) => <span key={k} className="flow-meta-chip">{k}: {v}</span>)}
              </div>
            )}
            {tool.enabled && tool.settings.map((setting) => (
              <SettingRow key={setting.key} name={setting.name} kind={setting.kind}
                required={setting.required} item={values[setting.key]} t={t} onNotify={onNotify}
                onSave={(value) => save(setting.key, value)} />
            ))}
          </div>
        ))}
      </div>

      <div className="tl-section"><span>{t("tools_test")}</span><small>{t("tools_test_hint")}</small></div>
      <div className="flow-tool-test">
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

      <div className="tl-section"><span>{t("tools_recent")}</span></div>
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

type SaveState = { kind: "idle" } | { kind: "saving" } | { kind: "saved" } | { kind: "error"; message: string };

/** 一项设置：key 已配置时显示掩码 +「更换 / 清除」；编辑时回车保存、Esc 取消；状态写在原地。 */
function SettingRow({ name, kind, required, item, t, onNotify, onSave }: {
  name: string; kind: string; required: boolean; item: SettingItem | undefined; t: TFunc;
  onNotify: Notify; onSave: (value: unknown) => Promise<void>;
}) {
  const secret = kind === "secret";
  const masked = secret ? maskedOf(item) : "";
  const current = secret ? "" : String(item?.value ?? "");
  const [draft, setDraft] = useState<string | null>(null);
  const [state, setState] = useState<SaveState>({ kind: "idle" });

  useEffect(() => {
    if (state.kind !== "saved") return;
    const timer = setTimeout(() => setState({ kind: "idle" }), 4000);
    return () => clearTimeout(timer);
  }, [state]);

  const commit = async (value: string) => {
    setState({ kind: "saving" });
    try {
      await onSave(kind === "int" ? Number(value || 0) : value);
      setDraft(null);
      setState({ kind: "saved" });
      onNotify(t(value ? "tools_saved" : "tools_cleared", { name }), "ok");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setState({ kind: "error", message });
      onNotify(t("tools_save_failed", { name, error: message }), "error");
    }
  };

  const editing = draft !== null;
  const showEditor = editing || !secret || !masked;
  const value = draft ?? (secret ? "" : current);
  const dirty = editing && draft !== current;
  return (
    <div className="tl-setting">
      <span className="tl-label">{name}{required ? <b title={t("tools_required")}> *</b> : null}</span>
      {showEditor ? (
        <span className="tl-edit">
          <input type={secret ? "password" : "text"} value={value} autoComplete="off"
            placeholder={secret ? t("tools_key_placeholder") : ""}
            disabled={state.kind === "saving"}
            onChange={(e) => { setDraft(e.target.value); if (state.kind !== "saving") setState({ kind: "idle" }); }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && dirty) void commit(draft ?? "");
              if (e.key === "Escape") { setDraft(null); setState({ kind: "idle" }); }
            }} />
          <button type="button" className="flow-dep-button" disabled={!dirty || state.kind === "saving"}
            onClick={() => void commit(draft ?? "")}>
            {state.kind === "saving" ? t("tools_saving") : t("tools_save")}
          </button>
          {editing && secret && masked && (
            <button type="button" className="tl-link" onClick={() => setDraft(null)}>{t("btn_cancel")}</button>
          )}
        </span>
      ) : (
        <span className="tl-edit">
          <span className="tl-set">✓ {t("tools_key_set", { masked })}</span>
          <button type="button" className="tl-link" onClick={() => setDraft("")}>{t("tools_key_change")}</button>
          <button type="button" className="tl-link danger" onClick={() => void commit("")}>{t("tools_key_clear")}</button>
        </span>
      )}
      {state.kind === "saved" && <span className="tl-status ok">{t("tools_saved_inline")}</span>}
      {state.kind === "error" && <span className="tl-status error" title={state.message}>{t("tools_save_failed_inline", { error: state.message })}</span>}
      {state.kind === "idle" && secret && !masked && required && <span className="tl-status warn">{t("tools_key_missing")}</span>}
    </div>
  );
}

function ToolState({ tool, t }: { tool: ToolInfo; t: TFunc }) {
  if (!tool.enabled) return <span className="tl-pill muted">{t("tools_off")}</span>;
  if (tool.missing.length) {
    return <span className="tl-pill warn">{t("tools_missing", { names: tool.missing.join(", ") })}</span>;
  }
  const lastOk = tool.last_ok_at ? Date.parse(tool.last_ok_at) : 0;
  const lastErr = tool.last_error_at ? Date.parse(tool.last_error_at) : 0;
  if (tool.last_error && lastErr > lastOk) {
    return <span className="tl-pill error" title={tool.last_error}>{t("tools_last_error", { error: tool.last_error })}</span>;
  }
  if (lastOk) return <span className="tl-pill ok">{t("tools_last_ok", { time: shortTime(tool.last_ok_at) })}</span>;
  return <span className="tl-pill ok">{t("tools_ready")} · {t("tools_never")}</span>;
}
