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

/** 名字匹配 prefer 的模型排前面（embedding 连接上列 embed 类、rerank 连接上列 rerank 类）。 */
function preferredModelsFirst(models: string[], prefer: RegExp): string[] {
  return [...models.filter((m) => prefer.test(m)), ...models.filter((m) => !prefer.test(m))];
}

const EMBED_MODEL = /embed/i;
const RERANK_MODEL = /rerank/i;

/**
 * 「连接 + 模型」：只列 OpenAI 兼容连接（Anthropic 没有 embedding / rerank 接口），
 * 模型可从连接的模型列表里挑（prefer 类排前），也可手填。
 */
function ConnectionModelInput({
  item, value, onChange, disabled, variant, providers = [], t, prefer, placeholder,
}: Props & { prefer: RegExp; placeholder: string }) {
  const current = isModelRef(value) ? value : null;
  const eligible = providers.filter((p) => p.kind === "openai_compatible");
  const selected = current ? eligible.find((p) => p.id === current.provider_id) : undefined;
  const listId = `${item.key}-models`;
  const pick = (id: string) => {
    if (id === MODEL_NONE) return onChange(null);
    const p = eligible.find((x) => x.id === id);
    onChange({ provider_id: id, model: preferredModelsFirst(p?.models ?? [], prefer)[0] ?? "" });
  };
  if (eligible.length === 0) {
    return (
      <span className="flow-quick-hint">
        {providers.length > 0 ? t("set_embedding_no_compatible") : t("set_model_no_providers")}
      </span>
    );
  }
  const ids = eligible.map((p) => p.id);
  return (
    <>
      <Choice
        variant={variant}
        value={current?.provider_id ?? MODEL_NONE}
        options={current
          ? (ids.includes(current.provider_id) ? ids : [current.provider_id, ...ids])
          : [MODEL_NONE, ...ids]}
        disabled={Boolean(disabled)}
        getLabel={(id) => (id === MODEL_NONE
          ? t("set_connection_pick")
          : providers.find((p) => p.id === id)?.label ?? id)}
        onChange={pick}
      />
      {current && (
        <>
          <input
            className={variant === "flow" ? "flow-quick-input" : undefined}
            style={variant === "ds" ? { ...DS_INPUT_STYLE, width: 320 } : undefined}
            list={listId}
            value={current.model}
            disabled={Boolean(disabled)}
            placeholder={placeholder}
            autoComplete="off"
            spellCheck={false}
            onChange={(e) => onChange({ provider_id: current.provider_id, model: e.target.value })}
          />
          <datalist id={listId}>
            {preferredModelsFirst(selected?.models ?? [], prefer).map((m) => <option key={m} value={m} />)}
          </datalist>
        </>
      )}
    </>
  );
}

/** Embedding 来源：「本地模型 / 线上 API」二选一；线上走 OpenAI 兼容连接的 /embeddings。 */
function EmbeddingSourceInput(props: Props) {
  const { item, value, onChange, disabled, variant, providers = [], t } = props;
  const local = item.options[0];
  // 默认线上：只有明确选了本地模型才是 local；未选连接（null）也算线上。
  const mode = value === local ? "local" : "online";

  const onMode = (m: string) => {
    if (m === "local") {
      onChange(local);
      return;
    }
    const first = providers.find((p) => p.kind === "openai_compatible");
    onChange(first ? { provider_id: first.id, model: preferredModelsFirst(first.models, EMBED_MODEL)[0] ?? "" } : null);
  };

  return (
    <div className="setting-model-ref" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <Choice
        variant={variant}
        value={mode}
        options={["local", "online"]}
        disabled={Boolean(disabled)}
        getLabel={(m) => (m === "local" ? t("set_model_literal_local") : t("set_embedding_online"))}
        onChange={onMode}
      />
      {mode === "online" && (
        <ConnectionModelInput {...props} prefer={EMBED_MODEL} placeholder={t("set_embedding_model_placeholder")} />
      )}
    </div>
  );
}

/** 线上 Rerank 的连接与模型（仅 provider=remote 时由 NodePanel 显示）。 */
function RerankConnectionInput(props: Props) {
  return (
    <div className="setting-model-ref" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <ConnectionModelInput {...props} prefer={RERANK_MODEL} placeholder={props.t("set_rerank_model_placeholder")} />
    </div>
  );
}

export function SettingInput(props: Props) {
  const { item, value, onChange, disabled = false, variant, t, lang, onBrowse } = props;

  if (item.key === "flow.embedding.source") return <EmbeddingSourceInput {...props} />;
  if (item.key === "flow.retrieval.rerank.remote") return <RerankConnectionInput {...props} />;
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
