"use client";
/**
 * 数据流「模型供应商」节点：连接列表（OpenAI 兼容 / Anthropic）。
 *
 * 每个连接显示地址、密钥后 4 位、最近一次测试结果与已缓存的模型数；可测试连接、获取模型列表、
 * 编辑（地址 / 类型 / 密钥）、删除（被「模型分配」或 Embedding 引用时后端拒绝）。
 * 操作即时生效，不走节点的草稿 / 保存徽章。
 */
import React, { useCallback, useMemo, useState } from "react";
import type { TFunc } from "@/lib/i18n";
import {
  createProvider,
  deleteProvider,
  refreshProviderModels,
  testProvider,
  updateProvider,
  type Provider,
  type ProviderDraft,
  type ProviderKind,
  type ProviderPreset,
} from "@/lib/settingsApi";
import { FlowSelect } from "./FlowSelect";

type Props = {
  providers: Provider[];
  presets: ProviderPreset[];
  t: TFunc;
  onChanged: () => Promise<void> | void;
  onNotify: (message: string, kind: "ok" | "error" | "info") => void;
};

const CUSTOM = "custom";

function statusText(p: Provider, t: TFunc): { text: string; ok: boolean | null } {
  if (!p.status) return { text: t("prov_status_untested"), ok: null };
  if (p.status.status === "ok") return { text: t("prov_status_ok", { n: p.status.models ?? p.models.length }), ok: true };
  return { text: p.status.message || p.status.code || t("prov_status_failed"), ok: false };
}

function ProviderEditor({
  initial, presets, t, busy, onSubmit, onCancel, isNew,
}: {
  initial: ProviderDraft;
  presets: ProviderPreset[];
  t: TFunc;
  busy: boolean;
  isNew: boolean;
  onSubmit: (draft: ProviderDraft) => void;
  onCancel: () => void;
}) {
  const [preset, setPreset] = useState(CUSTOM);
  const [draft, setDraft] = useState<ProviderDraft>(initial);
  const presetOptions = [CUSTOM, ...presets.map((p) => p.id)];
  const applyPreset = (id: string) => {
    setPreset(id);
    const p = presets.find((x) => x.id === id);
    if (p) setDraft((d) => ({ ...d, label: p.label, base_url: p.base_url, kind: p.kind, id: p.id }));
  };
  return (
    <div className="flow-provider-editor" onClick={(e) => e.stopPropagation()}>
      {isNew && (
        <label className="flow-quick-field flow-quick-field--wide">
          <span>{t("prov_preset")}</span>
          <FlowSelect
            value={preset}
            options={presetOptions}
            disabled={busy}
            getLabel={(v) => (v === CUSTOM ? t("prov_preset_custom") : presets.find((p) => p.id === v)?.label ?? v)}
            onChange={applyPreset}
          />
        </label>
      )}
      <div className="flow-quick-grid">
        <label className="flow-quick-field">
          <span>{t("prov_label")}</span>
          <input className="flow-quick-input" value={draft.label ?? ""} disabled={busy}
            onChange={(e) => setDraft({ ...draft, label: e.target.value })} />
        </label>
        <label className="flow-quick-field">
          <span>{t("prov_kind")}</span>
          <FlowSelect
            value={draft.kind ?? "openai_compatible"}
            options={["openai_compatible", "anthropic"]}
            disabled={busy}
            getLabel={(v) => t(v === "anthropic" ? "prov_kind_anthropic" : "prov_kind_openai")}
            onChange={(v) => setDraft({ ...draft, kind: v as ProviderKind })}
          />
        </label>
        <label className="flow-quick-field flow-quick-field--wide">
          <span>{t("prov_base_url")}</span>
          <input className="flow-quick-input" value={draft.base_url ?? ""} disabled={busy} spellCheck={false}
            onChange={(e) => setDraft({ ...draft, base_url: e.target.value })} />
        </label>
        <label className="flow-quick-field flow-quick-field--wide">
          <span>{t("prov_api_key")}</span>
          <input className="flow-quick-input" type="password" autoComplete="off" disabled={busy}
            placeholder={isNew ? t("prov_api_key_placeholder") : t("prov_api_key_keep")}
            value={draft.api_key ?? ""}
            onChange={(e) => setDraft({ ...draft, api_key: e.target.value })} />
        </label>
      </div>
      <div className="flow-provider-actions">
        <button type="button" className="flow-dep-button" disabled={busy || !draft.base_url}
          onClick={() => onSubmit(draft)}>
          {isNew ? t("prov_add_confirm") : t("btn_save")}
        </button>
        <button type="button" className="flow-quick-advanced-toggle" disabled={busy} onClick={onCancel}>
          {t("btn_cancel")}
        </button>
      </div>
    </div>
  );
}

export function ProvidersPanel({ providers, presets, t, onChanged, onNotify }: Props) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null); // provider id | "__new"
  const sorted = useMemo(() => [...providers].sort((a, b) => a.label.localeCompare(b.label)), [providers]);

  const run = useCallback(async (id: string, action: () => Promise<void>) => {
    setBusyId(id);
    try {
      await action();
      await onChanged();
    } catch (err) {
      onNotify(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setBusyId(null);
    }
  }, [onChanged, onNotify]);

  const submit = (id: string | null, draft: ProviderDraft) => run(id ?? "__new", async () => {
    const payload: ProviderDraft = { ...draft };
    if (!payload.api_key) delete payload.api_key; // 编辑时留空 = 不改密钥
    if (id) {
      const res = await updateProvider(id, payload);
      if (res.rebuild_required) onNotify(t("set_consequence_rebuild"), "info");
      else if (res.restart_required) onNotify(t("set_consequence_restart"), "info");
    } else {
      await createProvider(payload);
    }
    setEditing(null);
  });

  return (
    <div className="flow-quick-config flow-providers" onClick={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()}>
      {sorted.length === 0 && editing !== "__new" && (
        <div className="flow-quick-hint">{t("prov_empty")}</div>
      )}
      {sorted.map((p) => {
        const st = statusText(p, t);
        const busy = busyId === p.id;
        if (editing === p.id) {
          return (
            <ProviderEditor key={p.id} isNew={false} presets={presets} t={t} busy={busy}
              initial={{ label: p.label, base_url: p.base_url, kind: p.kind }}
              onSubmit={(d) => void submit(p.id, d)} onCancel={() => setEditing(null)} />
          );
        }
        return (
          <div key={p.id} className="flow-provider-row">
            <div className="flow-provider-head">
              <span className="flow-provider-name">{p.label}</span>
              <span className={`flow-provider-status ${st.ok === true ? "is-ok" : st.ok === false ? "is-error" : ""}`} title={st.text}>
                {st.text}
              </span>
            </div>
            <code className="flow-dep-pip" title={p.base_url}>{p.base_url}</code>
            <div className="flow-provider-meta">
              <span>{p.api_key_set ? t("prov_key_masked", { masked: p.api_key_masked }) : t("prov_key_missing")}</span>
              <span>{t("prov_models_count", { n: p.models.length })}</span>
              {p.referenced_by.length > 0 && <span>{t("prov_in_use")}</span>}
            </div>
            <div className="flow-provider-actions">
              <button type="button" className="flow-dep-button" disabled={busy}
                onClick={() => void run(p.id, async () => {
                  const res = await testProvider(p.id);
                  onNotify(res.status === "ok" ? t("prov_test_ok", { n: res.models ?? 0 }) : (res.message || t("prov_status_failed")),
                    res.status === "ok" ? "ok" : "error");
                })}>
                {busy ? t("prov_working") : t("prov_test")}
              </button>
              <button type="button" className="flow-dep-button" disabled={busy}
                onClick={() => void run(p.id, async () => {
                  const res = await refreshProviderModels(p.id);
                  if (res.status === "ok") onNotify(t("prov_models_loaded", { n: res.models.length }), "ok");
                  else onNotify(res.message || t("prov_status_failed"), "error");
                })}>
                {t("prov_fetch_models")}
              </button>
              <button type="button" className="flow-quick-advanced-toggle" disabled={busy} onClick={() => setEditing(p.id)}>
                {t("prov_edit")}
              </button>
              <button type="button" className="flow-quick-advanced-toggle" disabled={busy || p.referenced_by.length > 0}
                title={p.referenced_by.length > 0 ? t("prov_delete_in_use") : undefined}
                onClick={() => void run(p.id, () => deleteProvider(p.id))}>
                {t("btn_delete")}
              </button>
            </div>
          </div>
        );
      })}
      {editing === "__new" ? (
        <ProviderEditor isNew presets={presets} t={t} busy={busyId === "__new"}
          initial={{ kind: "openai_compatible", base_url: "", label: "" }}
          onSubmit={(d) => void submit(null, d)} onCancel={() => setEditing(null)} />
      ) : (
        <button type="button" className="flow-dep-button flow-provider-add" onClick={() => setEditing("__new")}>
          + {t("prov_add")}
        </button>
      )}
    </div>
  );
}
