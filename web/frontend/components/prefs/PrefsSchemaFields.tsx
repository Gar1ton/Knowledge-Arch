"use client";
/**
 * 偏好设置窗口：按 SettingSpec 渲染一组设置（ds 样式），改动即时写入 `PATCH /api/settings`。
 *
 * 开关 / 下拉 / 模型选择一改就保存；文本与数字在失焦或回车时保存。owner=shell 的项另外交给
 * 桌面壳即时生效（setShellPref），owner=frontend 的项由调用方的 onApplied 应用到界面。
 */
import React, { useCallback, useEffect, useState } from "react";
import { Field } from "@/components/ds/Card";
import { useToast } from "@/components/ui/Toast";
import { setShellPref } from "@/lib/backend";
import { useI18n } from "@/lib/i18n";
import {
  getSettingsSchema,
  patchSetting,
  type Consequence,
  type SettingItem,
} from "@/lib/settingsApi";
import { SettingInput } from "@/components/settings/SettingInput";
import {
  initialDraft,
  isDirty,
  settingLabel,
  toPatchValue,
  type DraftValue,
} from "@/components/settings/settingValues";

const IMMEDIATE_KINDS = new Set(["bool", "enum", "model_ref"]);

export function useSettingsHome(home: string) {
  const [items, setItems] = useState<SettingItem[]>([]);
  const [error, setError] = useState("");
  const reload = useCallback(async () => {
    try {
      setItems((await getSettingsSchema(home)).items);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [home]);
  useEffect(() => {
    let alive = true;
    getSettingsSchema(home)
      .then((schema) => { if (alive) { setItems(schema.items); setError(""); } })
      .catch((err) => { if (alive) setError(err instanceof Error ? err.message : String(err)); });
    return () => { alive = false; };
  }, [home]);
  return { items, error, reload };
}

export function PrefsSchemaFields({
  items,
  exclude = [],
  onSaved,
  onApplied,
}: {
  items: SettingItem[];
  exclude?: string[];
  onSaved?: (key: string, consequence: Consequence) => void;
  onApplied?: (key: string, value: unknown) => void;
}) {
  const { t, lang } = useI18n();
  const { toast } = useToast();
  const [drafts, setDrafts] = useState<Record<string, DraftValue>>({});
  const [savingKey, setSavingKey] = useState<string | null>(null);

  const commit = useCallback(async (item: SettingItem, value: DraftValue) => {
    if (!isDirty(item, value)) return;
    const patch = toPatchValue(item, value);
    if (patch === undefined) {
      toast(t("set_invalid_draft"), "error");
      return;
    }
    setSavingKey(item.key);
    try {
      const result = await patchSetting(item.key, patch);
      if (item.owner === "shell") await setShellPref(item.key, patch);
      onApplied?.(item.key, patch);
      onSaved?.(item.key, result.consequence);
      if (result.consequence === "restart") toast(t("set_consequence_restart"), "info");
      else if (result.consequence === "rebuild") toast(t("set_consequence_rebuild"), "info");
      setDrafts((d) => {
        const next = { ...d };
        delete next[item.key];
        return next;
      });
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setSavingKey(null);
    }
  }, [onApplied, onSaved, t, toast]);

  return (
    <>
      {items.filter((item) => !exclude.includes(item.key)).map((item) => {
        const value = item.key in drafts ? drafts[item.key] : initialDraft(item);
        const onChange = (next: DraftValue) => {
          setDrafts((d) => ({ ...d, [item.key]: next }));
          if (IMMEDIATE_KINDS.has(item.kind)) void commit(item, next);
        };
        return (
          <Field key={item.key} label={settingLabel(item, t)}>
            <div
              onBlur={() => { if (!IMMEDIATE_KINDS.has(item.kind)) void commit(item, value); }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && item.kind !== "text") void commit(item, value);
              }}
            >
              <SettingInput
                item={item}
                value={value}
                onChange={onChange}
                disabled={savingKey === item.key}
                variant="ds"
                t={t}
                lang={lang}
              />
            </div>
          </Field>
        );
      })}
    </>
  );
}
