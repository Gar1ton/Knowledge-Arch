import React, { forwardRef, useCallback, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import type { EffectiveConfig, PipelineStage } from "@/lib/api";
import type { I18nKey, Lang, TFunc } from "@/lib/i18n";
import type { Provider, ProviderPreset, SettingItem } from "@/lib/settingsApi";
import { backendLabel, type FlowStageId } from "./model";
import { FlowSelect } from "./FlowSelect";
import { NodePanel } from "./NodePanel";
import { ProvidersPanel } from "./ProvidersPanel";
import { NotionActions, R2Actions } from "./SyncActions";
import { ZoteroQuickConfig } from "./ZoteroQuickConfig";

export type FlowConfigSnapshot = EffectiveConfig;
export type QuickConfigValues = Record<string, string | boolean>;
// section 为空 = key 即设置键（NodePanel）；Zotero 面板仍用「段.键」，保存时映射到设置键。
export type QuickConfigUpdate = { section?: string; key: string; value: unknown };

export type ConfigSection =
  | "source_store"
  | "r2_sync"
  | "notion_sync"
  | "graph"
  | "vector_db"
  | "embedding"
  | "zotero_sync"
  | "rerank"
  | "deep_thinking"
  | "enhanced_recall";

// 由各 panel 上报给 FlowNode 头部徽章的草稿态；徽章据此变「保存」。
export type QuickConfigDirty = { count: number; canSave: boolean };
// FlowNode 通过 ref 触发 panel 提交（头部徽章充当唯一保存入口）。
export type QuickConfigHandle = { save: () => void };

export const ZOTERO_SYNC_MODES = ["strict_mirror", "conservative", "archive"];
export const ZOTERO_STORAGE_MODES = ["managed_copy", "linked", "zotmoov"];
type QuickConfigFieldBase = {
  id: string;
  section: ConfigSection;
  key: string;
  labelKey: I18nKey;
  wide?: boolean;
  helpKey?: I18nKey;
};

export type QuickConfigField =
  | (QuickConfigFieldBase & { kind: "text"; value: string; placeholder?: string; browseDir?: boolean })
  | (QuickConfigFieldBase & { kind: "number"; value: string; min?: number; step?: number })
  | (QuickConfigFieldBase & { kind: "boolean"; value: boolean })
  | (QuickConfigFieldBase & { kind: "select"; value: string; options: string[] })
  | (QuickConfigFieldBase & { kind: "readonly"; value: string });

function fieldId(section: ConfigSection, key: string): string {
  return `${section}.${key}`;
}

export function textField(
  section: ConfigSection,
  key: string,
  labelKey: I18nKey,
  value: string,
  wide = false,
  browseDir = false,
  helpKey?: I18nKey,
): QuickConfigField {
  return { id: fieldId(section, key), kind: "text", section, key, labelKey, value, wide, browseDir, helpKey };
}

export function numberField(
  section: ConfigSection,
  key: string,
  labelKey: I18nKey,
  value: string,
  helpKey?: I18nKey,
  min = 1,
  step?: number,
): QuickConfigField {
  return { id: fieldId(section, key), kind: "number", section, key, labelKey, value, helpKey, min, step };
}

export function booleanField(
  section: ConfigSection,
  key: string,
  labelKey: I18nKey,
  value: boolean,
): QuickConfigField {
  return { id: fieldId(section, key), kind: "boolean", section, key, labelKey, value };
}

export function selectField(
  section: ConfigSection,
  key: string,
  labelKey: I18nKey,
  value: string,
  options: string[],
  wide = false,
  helpKey?: I18nKey,
): QuickConfigField {
  return { id: fieldId(section, key), kind: "select", section, key, labelKey, value, options, wide, helpKey };
}

function sectionData(config: FlowConfigSnapshot, section: ConfigSection): Record<string, unknown> {
  return (config[section] ?? {}) as Record<string, unknown>;
}

export function readString(config: FlowConfigSnapshot, section: ConfigSection, key: string, fallback = ""): string {
  const value = sectionData(config, section)[key];
  if (value === null || value === undefined) return fallback;
  return String(value);
}

export function readNumberString(config: FlowConfigSnapshot, section: ConfigSection, key: string, fallback: number): string {
  const value = sectionData(config, section)[key];
  const parsed = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
  return Number.isFinite(parsed) ? String(parsed) : String(fallback);
}

export function readBoolean(config: FlowConfigSnapshot, section: ConfigSection, key: string, fallback: boolean): boolean {
  const value = sectionData(config, section)[key];
  if (typeof value === "boolean") return value;
  if (typeof value === "string") return value.toLowerCase() === "true";
  if (typeof value === "number") return value !== 0;
  return fallback;
}

export function fieldInitialValue(field: QuickConfigField): string | boolean {
  return field.kind === "boolean" ? field.value : field.value;
}

function graphModeLabel(value: string, t: TFunc): string {
  const key = `graph_mode_${value}` as I18nKey;
  return t(key);
}

// ─── Field control ────────────────────────────────────────────

export function FieldControl({
  field,
  value,
  lang,
  t,
  saving,
  onChange,
  onBrowseDir,
}: {
  field: QuickConfigField;
  value: string | boolean;
  lang: Lang;
  t: TFunc;
  saving: boolean;
  onChange: (value: string | boolean) => void;
  onBrowseDir?: () => void;
}) {
  if (field.kind === "readonly") {
    const raw = String(value);
    const display = raw
      ? field.id === "rerank.runtime_status"
        ? backendLabel(raw, lang)
        : raw
      : t("flow_value_empty");
    return (
      <div
        className={`flow-quick-readonly ${raw ? "" : "is-empty"}`}
        title={raw || undefined}
      >
        {display}
      </div>
    );
  }

  if (field.kind === "boolean") {
    const enabled = Boolean(value);
    return (
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        className={`flow-quick-toggle ${enabled ? "is-on" : ""}`}
        disabled={saving}
        onClick={() => onChange(!enabled)}
      >
        <span className="flow-quick-toggle-knob" />
        {enabled ? t("flow_quick_on") : t("flow_quick_off")}
      </button>
    );
  }

  if (field.kind === "select") {
    const getLabel =
      field.section === "graph" && field.key === "query_mode"
        ? (v: string) => graphModeLabel(v, t)
        : (v: string) => backendLabel(v, lang);
    return (
      <FlowSelect
        value={String(value)}
        options={field.options}
        disabled={saving}
        getLabel={getLabel}
        onChange={onChange}
      />
    );
  }

  if (field.kind === "text" && field.browseDir) {
    return (
      <div className="flow-quick-dir-row">
        <input
          className="flow-quick-input"
          type="text"
          value={String(value)}
          disabled={saving}
          placeholder={field.placeholder}
          onChange={(e) => onChange(e.target.value)}
        />
        <button
          type="button"
          className="flow-quick-dir-btn"
          disabled={saving}
          title={t("dir_picker_title")}
          onClick={onBrowseDir}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
          </svg>
        </button>
      </div>
    );
  }

  return (
    <input
      className="flow-quick-input"
      type={field.kind === "number" ? "number" : "text"}
      inputMode={field.kind === "number" ? (field.step ? "decimal" : "numeric") : undefined}
      min={field.kind === "number" ? (field.min ?? 1) : undefined}
      step={field.kind === "number" ? field.step : undefined}
      value={String(value)}
      disabled={saving}
      placeholder={field.kind === "text" ? field.placeholder : undefined}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

// ─── 共享：草稿计算 + hook + 字段网格 + 高级折叠 ────────────────

// 比对 draft 与初值得出待提交 updates（required+advanced 合并；Generic 与 Zotero 共用）。
export function computeUpdates(
  fields: QuickConfigField[],
  draft: QuickConfigValues,
): { updates: QuickConfigUpdate[]; hasInvalidNumber: boolean } {
  const updates: QuickConfigUpdate[] = [];
  let hasInvalidNumber = false;

  for (const field of fields) {
    const current = draft[field.id] ?? fieldInitialValue(field);
    const initial = fieldInitialValue(field);

    if (field.kind === "readonly") continue;

    if (field.kind === "boolean") {
      const value = Boolean(current);
      if (value !== field.value) updates.push({ section: field.section, key: field.key, value });
      continue;
    }

    const raw = String(current);
    if (raw === String(initial)) continue;

    if (field.kind === "number") {
      if (raw.trim() === "") continue;
      const parsed = Number(raw);
      const min = field.min ?? 1;
      if (!Number.isFinite(parsed) || parsed < min) {
        hasInvalidNumber = true;
        continue;
      }
      updates.push({ section: field.section, key: field.key, value: parsed });
      continue;
    }

    updates.push({ section: field.section, key: field.key, value: raw });
  }

  return { updates, hasInvalidNumber };
}

function buildDraftFromFields(fields: QuickConfigField[]): QuickConfigValues {
  const next: QuickConfigValues = {};
  for (const field of fields) next[field.id] = fieldInitialValue(field);
  return next;
}

// 字段集 → 草稿态。字段签名变化时以派生初始值重置草稿，并即时计算 updates。
export function useQuickConfigDraft(fields: QuickConfigField[]) {
  const fieldSignature = useMemo(
    () => fields.map((f) => `${f.id}:${String(fieldInitialValue(f))}`).join("|"),
    [fields],
  );
  const initialDraft = useMemo(() => buildDraftFromFields(fields), [fields]);
  const [draftState, setDraftState] = useState(() => ({
    signature: fieldSignature,
    values: initialDraft,
  }));
  const draft = draftState.signature === fieldSignature ? draftState.values : initialDraft;
  const setDraft = useCallback<React.Dispatch<React.SetStateAction<QuickConfigValues>>>(
    (updater) => {
      setDraftState((current) => {
        const base = current.signature === fieldSignature ? current.values : initialDraft;
        const values = typeof updater === "function" ? updater(base) : updater;
        return { signature: fieldSignature, values };
      });
    },
    [fieldSignature, initialDraft],
  );

  const { updates, hasInvalidNumber } = useMemo(() => computeUpdates(fields, draft), [draft, fields]);
  return { draft, setDraft, updates, hasInvalidNumber };
}

// 高级折叠：折叠按钮 + 展开浮层。slot 非空时整体 Portal 到节点底部槽位（按钮在节点最底部，
// 浮层紧贴节点底）；展开浮层绝对定位、不计入节点测量高度，故展开/收起不影响边对齐。
export function AdvancedSection({
  open,
  onToggle,
  label,
  slot,
  children,
}: React.PropsWithChildren<{ open: boolean; onToggle: () => void; label: string; slot?: HTMLElement | null }>) {
  const content = (
    <div className="flow-quick-advanced" onClick={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()}>
      <button type="button" className="flow-quick-advanced-toggle" onClick={onToggle}>
        <span className={`flow-quick-advanced-caret ${open ? "is-open" : ""}`}>▸</span>
        {label}
      </button>
      {open && <div className="flow-quick-advanced-panel">{children}</div>}
    </div>
  );
  return slot ? createPortal(content, slot) : content;
}

// ─── Panel ────────────────────────────────────────────────────

export type QuickConfigPanelProps = {
  stage: PipelineStage;
  config: FlowConfigSnapshot;
  /** 本节点的 SettingSpec 条目（`/api/settings/schema` 按 home 分组）。 */
  settingsItems: SettingItem[];
  providers: Provider[];
  presets: ProviderPreset[];
  hiddenKeys?: string[];
  lang: Lang;
  t: TFunc;
  saving: boolean;
  onSave: (stageId: FlowStageId, updates: QuickConfigUpdate[]) => void;
  onRefresh?: () => Promise<void>;
  onNotify: (message: string, kind: "ok" | "error" | "info") => void;
  // 头部徽章充当唯一保存入口：panel 上报草稿态，FlowNode 通过 ref 触发提交。
  onDirtyChange?: (state: QuickConfigDirty) => void;
  advancedOpen: boolean;
  onToggleAdvanced: () => void;
  // 高级折叠 Portal 到的节点底部槽位（FlowNode 提供）。
  advancedSlot?: HTMLElement | null;
};

// Zotero 走标签式专用面板；模型供应商是连接列表；其余节点按 SettingSpec 渲染（NodePanel）。
export const QuickConfigPanel = forwardRef<QuickConfigHandle, QuickConfigPanelProps>(function QuickConfigPanel(props, ref) {
  const { stage, settingsItems, providers, presets, hiddenKeys, t, onRefresh, onNotify } = props;
  if (stage.id === "zotero") return <ZoteroQuickConfig ref={ref} {...props} />;
  if (stage.id === "providers") {
    return (
      <ProvidersPanel providers={providers} presets={presets} t={t} onNotify={onNotify}
        onChanged={async () => { await onRefresh?.(); }} />
    );
  }
  const extra =
    stage.id === "notion" ? <NotionActions stage={stage} t={t} onNotify={onNotify} onChanged={() => void onRefresh?.()} />
    : stage.id === "r2" ? <R2Actions stage={stage} t={t} onNotify={onNotify} />
    : undefined;
  return (
    <NodePanel
      ref={ref}
      stage={stage}
      items={settingsItems}
      providers={providers}
      hiddenKeys={hiddenKeys}
      lang={props.lang}
      t={t}
      saving={props.saving}
      onSave={props.onSave}
      onDirtyChange={props.onDirtyChange}
      advancedOpen={props.advancedOpen}
      onToggleAdvanced={props.onToggleAdvanced}
      advancedSlot={props.advancedSlot}
      extra={extra}
    />
  );
});
