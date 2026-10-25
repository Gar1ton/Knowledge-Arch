"use client";
/**
 * AI 助手配置：欢迎页的「用 AI 助手完成配置」一步，与偏好设置 › 通用里的精简入口。
 *
 * 复制 prompt 时打开 `prefs.general.agent_access`（后端写出连接文件），用户把 prompt 粘贴到 Codex /
 * Claude Code 后，助手用应用自带的 Python 运行 skill 客户端完成配置。本页轮询接入状态与数据流节点，
 * 实时显示「已连接」与各节点进度；助手需要的密钥由全局的 SecretRequestDialog 弹框收集。
 */
import React, { useCallback, useEffect, useState } from "react";
import { getCapabilities, type CapabilitiesData } from "@/lib/api";
import {
  getAgentPrompt,
  getAgentStatus,
  installAgentSkill,
  setAgentAccess,
  SKILL_TARGETS,
  type AgentStatus,
  type SkillInstallState,
} from "@/lib/agentApi";
import { useI18n, type I18nKey, type TFunc } from "@/lib/i18n";
import { requestTour } from "@/lib/tour";
import { landing } from "./LandingShell";

const POLL_MS = 3_000;
const CORE_STAGES = ["models", "embedding", "vector_store", "retrieval", "ask", "ingest"] as const;
const OPTIONAL_STAGES = ["zotero", "graph", "notion", "r2"] as const;
type Tool = "codex" | "claude" | "other";
const TOOLS: Tool[] = ["codex", "claude", "other"];

/** 轮询接入状态；enabled=false 时调用方停止轮询。 */
export function useAgentStatus(enabled = true): [AgentStatus | null, () => void] {
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const refresh = useCallback(() => {
    getAgentStatus().then(setStatus).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (!enabled) return undefined;
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, [enabled, refresh]);
  return [status, refresh];
}

export function useAgentPrompt(lang: string): string {
  const [prompt, setPrompt] = useState("");
  useEffect(() => {
    let alive = true;
    getAgentPrompt(lang).then((p) => { if (alive) setPrompt(p.prompt); }).catch(() => undefined);
    return () => { alive = false; };
  }, [lang]);
  return prompt;
}

async function writeClipboard(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch {
    /* 非安全上下文等：退回 execCommand */
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  document.execCommand("copy");
  area.remove();
}

/** 复制 prompt；接入未开启时顺带打开（这是用户明确的「让 AI 助手接手」动作）。 */
export async function copySetupPrompt(prompt: string, status: AgentStatus | null): Promise<void> {
  await writeClipboard(prompt);
  if (!status?.enabled) await setAgentAccess(true);
}

export function skillStateText(t: TFunc, state: SkillInstallState): string {
  return t(`agent_skill_state_${state}` as I18nKey);
}

function useCapabilities(): CapabilitiesData | null {
  const [caps, setCaps] = useState<CapabilitiesData | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => getCapabilities().then((c) => { if (alive) setCaps(c); }).catch(() => undefined);
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => { alive = false; clearInterval(timer); };
  }, []);
  return caps;
}

function StatusLine({ status }: { status: AgentStatus | null }) {
  const { t } = useI18n();
  const [dot, text] = !status?.enabled
    ? [landing.dot, t("agent_status_off")]
    : status.connected
      ? [`${landing.dot} ${landing.dotOn}`, t("agent_status_connected")]
      : [`${landing.dot} ${landing.dotWaiting}`, t("agent_status_waiting")];
  return (
    <div className={landing.statusLine} role="status" aria-live="polite">
      <span className={dot} aria-hidden="true" />
      <span>{text}</span>
    </div>
  );
}

function Checklist({ caps }: { caps: CapabilitiesData | null }) {
  const { t } = useI18n();
  const stages = new Map((caps?.pipeline ?? []).map((stage) => [stage.id, stage]));
  const row = (id: string, optional: boolean) => {
    const stage = stages.get(id);
    const ready = Boolean(stage && stage.status === "ready" && stage.configured !== false);
    const label = optional && !stage?.configured ? t("agent_stage_optional") : ready
      ? t("agent_stage_ready") : t("agent_stage_pending");
    return (
      <li key={id} className={landing.checkItem}>
        <span>{t(`flow_stage_${id}` as I18nKey)}</span>
        <span className={`${landing.checkState} ${ready ? landing.checkReady : ""}`}>{label}</span>
      </li>
    );
  };
  return (
    <ul className={landing.checklist} aria-label={t("agent_checklist")}>
      {CORE_STAGES.map((id) => row(id, false))}
      {OPTIONAL_STAGES.map((id) => row(id, true))}
    </ul>
  );
}

/** 欢迎页的 AI 配置一步。 */
export function AgentSetupStep({ onDone }: { onDone: () => void }) {
  const { t, lang } = useI18n();
  const [status, refresh] = useAgentStatus();
  const prompt = useAgentPrompt(lang);
  const caps = useCapabilities();
  const [tool, setTool] = useState<Tool>("codex");
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const [installing, setInstalling] = useState(false);

  const copy = async () => {
    setError("");
    try {
      await copySetupPrompt(prompt, status);
      setCopied(true);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const install = async () => {
    setError("");
    setInstalling(true);
    try {
      await installAgentSkill(SKILL_TARGETS);
      if (!status?.enabled) await setAgentAccess(true);
      refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setInstalling(false);
    }
  };

  const skill = status?.skill;
  const skillInstalled = skill && SKILL_TARGETS.every((target) => skill[target].state === "current");

  return (
    <>
      <h2 className={landing.heading}>{t("agent_title")}</h2>
      <p className={landing.text}>{t("agent_body")}</p>

      <div className={landing.tabs} role="tablist">
        {TOOLS.map((item) => (
          <button key={item} type="button" role="tab" aria-selected={tool === item}
            className={`${landing.tab} ${tool === item ? landing.tabActive : ""}`}
            onClick={() => setTool(item)}>
            {t(`agent_tab_${item}` as I18nKey)}
          </button>
        ))}
      </div>
      <p className={landing.hint}>{t(`agent_tip_${tool}` as I18nKey)}</p>
      <pre className={landing.prompt} aria-label={t("agent_copy")}>{prompt || "…"}</pre>
      <div className={landing.actions}>
        <button type="button" className={landing.primary} disabled={!prompt} onClick={() => void copy()}>
          {t("agent_copy")}
        </button>
        {copied || status?.connected ? (
          <button type="button" className={landing.ghost} onClick={onDone}>{t("agent_enter")}</button>
        ) : (
          <button type="button" className={landing.ghost} onClick={() => { requestTour(); onDone(); }}>
            {t("agent_skip")}
          </button>
        )}
      </div>
      {copied && <p className={landing.hint}>{t("agent_copied")}</p>}
      {error && <div role="alert" className={landing.error}>{error}</div>}

      <div className={landing.divider} aria-hidden="true" />
      <StatusLine status={status} />
      <div className={landing.fieldLabel}>{t("agent_checklist")}</div>
      <Checklist caps={caps} />

      <div className={landing.divider} aria-hidden="true" />
      <div className={landing.fieldLabel}>{t("agent_skill_title")}</div>
      <p className={landing.hint}>{t("agent_skill_hint")}</p>
      <div className={landing.row}>
        <button type="button" className={landing.ghost} disabled={installing || Boolean(skillInstalled)}
          onClick={() => void install()}>
          {installing ? "…" : skill && SKILL_TARGETS.some((target) => skill[target].state === "outdated")
            ? t("agent_skill_update") : t("agent_skill_install")}
        </button>
        {skill && (
          <span className={landing.hint}>
            {SKILL_TARGETS.map((target) => `${t(`agent_tab_${target}` as I18nKey)}: ${skillStateText(t, skill[target].state)}`)
              .join(" · ")}
          </span>
        )}
      </div>
    </>
  );
}
