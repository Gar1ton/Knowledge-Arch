"use client";
/**
 * 一个设置项的输入控件（受控）。按 SettingSpec.kind 渲染：
 * bool 开关 / int·float 数字 / str·path 文本（path 带「浏览」）/ text 多行 / enum 下拉 /
 * secret 密码框（只显示后 4 位，留空 = 不改）/ model_ref「连接 + 模型」两级选择。
 *
 * variant="flow" 用数据流节点的样式（flow-quick-*），variant="ds" 用设计系统组件（偏好窗口）。
 */
import React from "react";
import { FlowSelect } from "@/components/flow/FlowSelect";
import { Select } from "@/components/ds/Select";
import { Toggle } from "@/components/ds/Toggle";
import type { Lang, TFunc } from "@/lib/i18n";
import { isModelRef, type Provider, type SettingItem } from "@/lib/settingsApi";
import {
  SECRET_CLEAR,
  boundsHint,
  optionLabel,
  secretPlaceholder,
  type DraftValue,
} from "./settingValues";

export type SettingInputVariant = "flow" | "ds";

export const DS_INPUT_STYLE: React.CSSProperties = {
  height: 30,
  padding: "0 10px",
  border: "1px solid var(--border-strong)",
  borderRadius: "var(--radius-md)",
  background: "var(--surface)",
  color: "var(--fg)",
  fontSize: 12,
  fontFamily: "inherit",
  outline: "none",
  width: 220,
  boxSizing: "border-box",
};

const MODEL_NONE = "";
const MODEL_LITERAL_PREFIX = "literal:";

type Props = {
  item: SettingItem;
  value: DraftValue;
  onChange: (value: DraftValue) => void;
  disabled?: boolean;
  variant: SettingInputVariant;
  providers?: Provider[];
  t: TFunc;
  lang: Lang;
  onBrowse?: () => void;
};

function Choice({
  variant, value, options, disabled, getLabel, onChange,
}: {
  variant: SettingInputVariant;
  value: string;
  options: string[];
  disabled: boolean;
  getLabel: (v: string) => string;
  onChange: (v: string) => void;
}) {
  if (variant === "flow") {
    return (
      <FlowSelect value={value} options={options} disabled={disabled} getLabel={getLabel} onChange={onChange} />
    );
  }
  return (
    <Select
      value={value}
      disabled={disabled}
      onChange={onChange}
      options={options.map((o) => ({ value: o, label: getLabel(o) }))}
      style={{ minWidth: 220 }}
    />
  );
}

function TextBox({
  variant, value, disabled, placeholder, type = "text", onChange, wide,
}: {
  variant: SettingInputVariant;
  value: string;
  disabled: boolean;
  placeholder?: string;
  type?: string;
  onChange: (v: string) => void;
  wide?: boolean;
}) {
  return (
    <input
      className={variant === "flow" ? "flow-quick-input" : undefined}
      style={variant === "ds" ? { ...DS_INPUT_STYLE, width: wide ? 320 : 220 } : undefined}
      type={type}
      value={value}
      disabled={disabled}
      placeholder={placeholder}
      autoComplete="off"
      spellCheck={false}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

/** model_ref：连接下拉 + 模型下拉（连接没有缓存模型列表时退化为文本框）。 */
function ModelRefInput({ item, value, onChange, disabled, variant, providers = [], t }: Props) {
  const literals = item.options;
  const current = isModelRef(value) ? value : null;
  const providerValue = current
    ? current.provider_id
    : typeof value === "string" && value
      ? `${MODEL_LITERAL_PREFIX}${value}`
      : MODEL_NONE;
  const providerOptions = [
    ...(item.nullable ? [MODEL_NONE] : []),
    ...literals.map((l) => `${MODEL_LITERAL_PREFIX}${l}`),
    ...providers.map((p) => p.id),
  ];
  const providerLabel = (v: string) => {
    if (v === MODEL_NONE) {
      return item.key === "flow.models.chat" ? t("set_model_unassigned") : t("set_model_follow_chat");
    }
    if (v.startsWith(MODEL_LITERAL_PREFIX)) return t("set_model_literal_local");
    return providers.find((p) => p.id === v)?.label ?? v;
  };
  const selected = current ? providers.find((p) => p.id === current.provider_id) : undefined;
  const models = selected?.models ?? [];

  const onProvider = (v: string) => {
    if (v === MODEL_NONE) onChange(null);
    else if (v.startsWith(MODEL_LITERAL_PREFIX)) onChange(v.slice(MODEL_LITERAL_PREFIX.length));
    else {
      const p = providers.find((x) => x.id === v);
      onChange({ provider_id: v, model: p?.models[0] ?? "" });
    }
  };

  return (
    <div className="setting-model-ref" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <Choice
        variant={variant}
        value={providerValue}
        options={providerOptions.length ? providerOptions : [MODEL_NONE]}
        disabled={Boolean(disabled)}
        getLabel={providerLabel}
        onChange={onProvider}
      />
      {current && (
        models.length > 0 ? (
          <Choice
            variant={variant}
            value={current.model}
            options={models.includes(current.model) || !current.model ? models : [current.model, ...models]}
            disabled={Boolean(disabled)}
            getLabel={(m) => m}
            onChange={(m) => onChange({ provider_id: current.provider_id, model: m })}
          />
        ) : (
          <TextBox
            variant={variant}
            value={current.model}
            disabled={Boolean(disabled)}
            placeholder={t("set_model_name_placeholder")}
            onChange={(m) => onChange({ provider_id: current.provider_id, model: m })}
          />
        )
      )}
      {providers.length === 0 && !literals.length && (
        <span className="flow-quick-hint">{t("set_model_no_providers")}</span>
      )}
    </div>
  );
}

export function SettingInput(props: Props) {
  const { item, value, onChange, disabled = false, variant, t, lang, onBrowse } = props;

  if (item.kind === "model_ref") return <ModelRefInput {...props} />;

  if (item.kind === "bool") {
    const on = Boolean(value);
    if (variant === "ds") return <Toggle checked={on} disabled={disabled} onChange={onChange} />;
    return (
      <button
        type="button"
        role="switch"
        aria-checked={on}
        className={`flow-quick-toggle ${on ? "is-on" : ""}`}
        disabled={disabled}
        onClick={() => onChange(!on)}
      >
        <span className="flow-quick-toggle-knob" />
        {on ? t("flow_quick_on") : t("flow_quick_off")}
      </button>
    );
  }

  if (item.kind === "enum") {
    return (
      <Choice
        variant={variant}
        value={String(value ?? "")}
        options={item.options}
        disabled={disabled}
        getLabel={(v) => optionLabel(item, v, t, lang)}
        onChange={onChange}
      />
    );
  }

  if (item.kind === "secret") {
    const cleared = value === SECRET_CLEAR;
    const isSet = typeof item.value === "object" && item.value !== null && "set" in item.value
      && Boolean((item.value as { set: boolean }).set);
    return (
      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
        <TextBox
          variant={variant}
          type="password"
          value={cleared ? "" : String(value ?? "")}
          disabled={disabled}
          placeholder={cleared ? t("set_secret_will_clear") : secretPlaceholder(item, t)}
          onChange={onChange}
        />
        {isSet && !cleared && (
          <button
            type="button"
            className={variant === "flow" ? "flow-quick-dir-btn" : undefined}
            style={
              variant === "ds"
                ? { ...DS_INPUT_STYLE, width: "auto", cursor: "pointer", whiteSpace: "nowrap" }
                : { width: "auto", padding: "0 8px", whiteSpace: "nowrap", flexShrink: 0 }
            }
            disabled={disabled}
            title={t("set_secret_clear")}
            onClick={() => onChange(SECRET_CLEAR)}
          >
            {t("set_secret_clear")}
          </button>
        )}
      </div>
    );
  }

  if (item.kind === "text") {
    return (
      <textarea
        className={variant === "flow" ? "flow-quick-input flow-quick-textarea" : undefined}
        style={variant === "ds" ? { ...DS_INPUT_STYLE, width: 320, height: 96, padding: 8 } : { minHeight: 84, resize: "vertical" }}
        value={String(value ?? "")}
        disabled={disabled}
        spellCheck={false}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }

  if (item.kind === "path") {
    return (
      <div className={variant === "flow" ? "flow-quick-dir-row" : undefined} style={variant === "ds" ? { display: "flex", gap: 6 } : undefined}>
        <TextBox variant={variant} value={String(value ?? "")} disabled={disabled} onChange={onChange} wide />
        {onBrowse && (
          <button
            type="button"
            className={variant === "flow" ? "flow-quick-dir-btn" : undefined}
            style={variant === "ds" ? { ...DS_INPUT_STYLE, width: "auto", cursor: "pointer" } : undefined}
            disabled={disabled}
            title={t("dir_picker_title")}
            onClick={onBrowse}
          >
            …
          </button>
        )}
      </div>
    );
  }

  const numeric = item.kind === "int" || item.kind === "float";
  return (
    <TextBox
      variant={variant}
      type={numeric ? "number" : "text"}
      value={String(value ?? "")}
      disabled={disabled}
      placeholder={numeric ? boundsHint(item) : undefined}
      onChange={onChange}
    />
  );
}
