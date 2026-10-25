"use client";
/**
 * 数据流节点的设置面板：从 `GET /api/settings/schema?home=flow.<node>` 渲染，
 * 写入经 `PATCH /api/settings`（由页面统一提交并按 consequence 提示重启服务 / 重建索引）。
 *
 * 与旧的 QuickConfigPanel 同一套外部契约：草稿态经 onDirtyChange 上报给节点头部徽章，
 * 头部「保存」经 ref 调 save()；advanced 层的设置项收进节点底部的「高级」折叠。
 */
import React, { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useState } from "react";
import type { PipelineStage } from "@/lib/api";
import { pickFolder, inDesktopShell } from "@/lib/backend";
import type { Lang, TFunc } from "@/lib/i18n";
import type { Provider, SettingItem } from "@/lib/settingsApi";
import { SettingInput } from "@/components/settings/SettingInput";
import {
  initialDraft,
  isDirty,
  settingLabel,
  toPatchValue,
  type DraftValue,
  type SettingsDraft,
} from "@/components/settings/settingValues";
import { DirPickerDialog } from "./DirPickerDialog";
import { AdvancedSection, type QuickConfigDirty, type QuickConfigHandle, type QuickConfigUpdate } from "./QuickConfigPanel";
import type { FlowStageId } from "./model";

export type NodePanelProps = {
  stage: PipelineStage;
  items: SettingItem[];
  providers: Provider[];
  /** 已由节点头部开关（Segmented）控制的键，不在面板里重复出现。 */
  hiddenKeys?: string[];
  lang: Lang;
  t: TFunc;
  saving: boolean;
  onSave: (stageId: FlowStageId, updates: QuickConfigUpdate[]) => void;
  onDirtyChange?: (state: QuickConfigDirty) => void;
  advancedOpen: boolean;
  onToggleAdvanced: () => void;
  advancedSlot?: HTMLElement | null;
  /** 节点专属操作（Notion 初始化 / R2 立即备份 …），渲染在字段之后。 */
  extra?: React.ReactNode;
};

function signatureOf(items: SettingItem[]): string {
  return items.map((i) => `${i.key}=${JSON.stringify(i.value)}`).join("|");
}

function ConsequenceTag({ item, t }: { item: SettingItem; t: TFunc }) {
  if (item.consequence === "none") return null;
  return (
    <span className={`flow-help-dot flow-consequence-tag is-${item.consequence}`} title={
      item.consequence === "rebuild" ? t("set_consequence_rebuild") : t("set_consequence_restart")
    }>
      {item.consequence === "rebuild" ? "R" : "↻"}
    </span>
  );
}

export const NodePanel = forwardRef<QuickConfigHandle, NodePanelProps>(function NodePanel(
  {
    stage, items, providers, hiddenKeys = [], lang, t, saving, onSave, onDirtyChange,
    advancedOpen, onToggleAdvanced, advancedSlot, extra,
  },
  ref,
) {
  const visible = useMemo(
    () => items.filter((i) => !hiddenKeys.includes(i.key) && i.owner === "backend"),
    [items, hiddenKeys],
  );
  const basic = visible.filter((i) => i.tier === "basic");
  const advanced = visible.filter((i) => i.tier === "advanced");
  const signature = useMemo(() => signatureOf(visible), [visible]);
  const [draftState, setDraftState] = useState<{ sig: string; values: SettingsDraft }>({ sig: "", values: {} });
  const draft = useMemo<SettingsDraft>(
    () => (draftState.sig === signature ? draftState.values : {}),
    [draftState, signature],
  );
  const [browseKey, setBrowseKey] = useState<string | null>(null);

  const valueOf = useCallback(
    (item: SettingItem): DraftValue => (item.key in draft ? draft[item.key] : initialDraft(item)),
    [draft],
  );
  const setValue = useCallback((key: string, value: DraftValue) => {
    setDraftState((cur) => ({ sig: signature, values: { ...(cur.sig === signature ? cur.values : {}), [key]: value } }));
  }, [signature]);

  const { updates, invalid } = useMemo(() => {
    const next: QuickConfigUpdate[] = [];
    let bad = false;
    for (const item of visible) {
      const value = valueOf(item);
      if (!isDirty(item, value)) continue;
      const patch = toPatchValue(item, value);
      if (patch === undefined) { bad = true; continue; }
      next.push({ key: item.key, value: patch });
    }
    return { updates: next, invalid: bad };
  }, [visible, valueOf]);

  const canSave = updates.length > 0 && !invalid && !saving;
  const stageId = stage.id as FlowStageId;

  useImperativeHandle(ref, () => ({
    save: () => {
      if (canSave) onSave(stageId, updates);
    },
  }), [canSave, onSave, stageId, updates]);

  useEffect(() => {
    onDirtyChange?.({ count: updates.length + (invalid ? 1 : 0), canSave });
  }, [updates.length, invalid, canSave, onDirtyChange]);

  const browse = useCallback(async (key: string) => {
    if (inDesktopShell()) {
      const picked = await pickFolder();
      if (picked) setValue(key, picked);
      return;
    }
    setBrowseKey(key);
  }, [setValue]);

  const grid = (list: SettingItem[]) => (
    <div className="flow-quick-grid">
      {list.map((item) => (
        <label
          key={item.key}
          className={`flow-quick-field ${["text", "path", "model_ref", "secret"].includes(item.kind) ? "flow-quick-field--wide" : ""}`}
        >
          <span>
            {settingLabel(item, t)}
            <ConsequenceTag item={item} t={t} />
          </span>
          <SettingInput
            item={item}
            value={valueOf(item)}
            onChange={(v) => setValue(item.key, v)}
            disabled={saving}
            variant="flow"
            providers={providers}
            t={t}
            lang={lang}
            onBrowse={item.kind === "path" ? () => void browse(item.key) : undefined}
          />
        </label>
      ))}
    </div>
  );

  if (visible.length === 0 && !extra) return null;

  const browseItem = browseKey ? visible.find((i) => i.key === browseKey) : undefined;

  return (
    <>
      <div className="flow-quick-config" onClick={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()}>
        {basic.length > 0 && grid(basic)}
        {invalid && <div className="flow-quick-error">{t("set_invalid_draft")}</div>}
        {extra}
        {advanced.length > 0 && (
          <AdvancedSection open={advancedOpen} onToggle={onToggleAdvanced} label={t("flow_quick_advanced")} slot={advancedSlot}>
            {grid(advanced)}
          </AdvancedSection>
        )}
      </div>
      {browseItem && (
        <DirPickerDialog
          initialPath={String(valueOf(browseItem) || "") || undefined}
          t={t}
          onSelect={(path) => { setValue(browseItem.key, path); setBrowseKey(null); }}
          onClose={() => setBrowseKey(null)}
        />
      )}
    </>
  );
});
