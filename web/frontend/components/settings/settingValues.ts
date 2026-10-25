/**
 * 设置项的纯函数：草稿 ↔ 存储值、脏检查、选项文案、旧键映射。
 *
 * 数据流节点面板（NodePanel）与偏好设置窗口共用；渲染见 SettingInput.tsx。
 */
import type { I18nKey, Lang, TFunc } from "@/lib/i18n";
import { backendLabel } from "@/components/flow/model";
import {
  isModelRef,
  isSecretValue,
  type ModelRefValue,
  type SettingItem,
} from "@/lib/settingsApi";

/** 草稿值：secret 为新输入的明文（"" = 未改动），model_ref 为对象 / 字面值 / null。 */
export type DraftValue = string | number | boolean | ModelRefValue | null;
export type SettingsDraft = Record<string, DraftValue>;
/** secret 草稿里表示「清除已保存的值」的哨兵（界面「清除」按钮）。 */
export const SECRET_CLEAR = "\u0000clear";

export function initialDraft(item: SettingItem): DraftValue {
  switch (item.kind) {
    case "secret":
      return "";
    case "bool":
      return Boolean(item.value);
    case "int":
    case "float":
      return item.value === null || item.value === undefined ? "" : String(item.value);
    case "model_ref":
      return isModelRef(item.value) ? item.value : typeof item.value === "string" ? item.value : null;
    default:
      return item.value === null || item.value === undefined ? "" : String(item.value);
  }
}

function sameModelRef(a: DraftValue, b: DraftValue): boolean {
  if (isModelRef(a) && isModelRef(b)) return a.provider_id === b.provider_id && a.model === b.model;
  return a === b;
}

export function isDirty(item: SettingItem, draft: DraftValue): boolean {
  if (item.kind === "secret") return draft !== "" && draft !== null;
  if (item.kind === "model_ref") return !sameModelRef(draft, initialDraft(item));
  return String(draft) !== String(initialDraft(item));
}

/** 草稿 → PATCH 的值；返回 undefined 表示草稿不合法（如数字越界），不提交。 */
export function toPatchValue(item: SettingItem, draft: DraftValue): unknown {
  if (item.kind === "secret") return draft === SECRET_CLEAR ? "" : String(draft ?? "");
  if (item.kind === "bool") return Boolean(draft);
  if (item.kind === "int" || item.kind === "float") {
    const raw = String(draft ?? "").trim();
    if (raw === "") return undefined;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return undefined;
    if (item.kind === "int" && !Number.isInteger(parsed)) return undefined;
    if (item.bounds && (parsed < item.bounds[0] || parsed > item.bounds[1])) return undefined;
    return parsed;
  }
  if (item.kind === "model_ref") {
    if (isModelRef(draft)) return draft.provider_id && draft.model ? draft : undefined;
    return draft === null || draft === "" ? (item.nullable ? null : undefined) : draft;
  }
  return draft ?? "";
}

export function settingLabel(item: SettingItem, t: TFunc): string {
  const text = t(item.label_key as I18nKey);
  return text === item.label_key ? item.key : text;
}

/** enum 选项文案：先找 `opt_<value>`，再退回数据流已有的后端名映射。 */
export function optionLabel(item: SettingItem, value: string, t: TFunc, lang: Lang): string {
  if (item.key === "flow.graph.query_mode") return t(`graph_mode_${value}` as I18nKey);
  const key = `opt_${value}` as I18nKey;
  const text = t(key);
  return text === key ? backendLabel(value, lang) : text;
}

export function secretPlaceholder(item: SettingItem, t: TFunc): string {
  if (isSecretValue(item.value) && item.value.set) {
    return t("set_secret_saved", { masked: item.value.masked });
  }
  return t("set_secret_empty");
}

export function boundsHint(item: SettingItem): string {
  if (!item.bounds) return "";
  return `${item.bounds[0]} – ${item.bounds[1]}`;
}

/**
 * Zotero 节点仍沿用旧的「段.键」字段模型；保存时映射到新设置键。
 * （其余节点直接按 SettingSpec 渲染，不经过这张表。）
 */
const ZOTERO_KEYS: Record<string, string> = {
  enabled: "flow.zotero.enabled",
  access_mode: "flow.zotero.access_mode",
  zotero_data_dir: "flow.zotero.data_dir",
  api_port: "flow.zotero.api_port",
  storage_mode: "flow.zotero.storage_mode",
  linked_root: "flow.zotero.linked_root",
  zotmoov_root: "flow.zotero.zotmoov_root",
  sync_mode: "flow.zotero.sync_mode",
  auto_sync_enabled: "flow.zotero.auto_sync",
  auto_sync_interval_sec: "flow.zotero.auto_sync_interval_sec",
};

export function settingKeyFor(section: string | undefined, key: string): string {
  if (!section) return key;
  if (section === "zotero_sync" && ZOTERO_KEYS[key]) return ZOTERO_KEYS[key];
  throw new Error(`no setting key for ${section}.${key}`);
}
