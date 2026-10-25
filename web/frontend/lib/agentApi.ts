/**
 * lib/agentApi.ts — AI 助手接入（Codex / Claude Code）的接口客户端。
 *
 * 开关是普通设置 `prefs.general.agent_access`（后端即时写入 / 删除连接文件）；prompt 由后端按本机
 * 路径生成；agent 需要密钥时创建「密钥请求」，界面在这里列出并把用户填写的值交给后端写进加密库。
 */

import { apiFetch } from "@/lib/api";
import { patchSetting } from "@/lib/settingsApi";

export const AGENT_ACCESS_KEY = "prefs.general.agent_access";
const JSON_HEADERS = { "Content-Type": "application/json" };
export const SKILL_TARGETS = ["codex", "claude"] as const;
export type SkillTarget = (typeof SKILL_TARGETS)[number];

export type SkillInstallState = "missing" | "current" | "outdated" | "foreign";

export interface AgentStatus {
  enabled: boolean;
  connected: boolean;
  last_seen_at: number | null;
  pending_secret_requests: number;
  skill?: Record<SkillTarget, { state: SkillInstallState; path: string }>;
}

export interface AgentPrompt {
  prompt: string;
  command: string;
  skill_dir: string;
}

export interface SecretRequest {
  id: string;
  target: string;
  label: string;
  reason: string;
  status: "pending" | "fulfilled" | "cancelled";
  created_at: number;
}

export async function getAgentStatus(): Promise<AgentStatus> {
  return apiFetch<AgentStatus>("/api/agent/status", { timeoutMs: 5_000 });
}

export async function setAgentAccess(enabled: boolean): Promise<void> {
  await patchSetting(AGENT_ACCESS_KEY, enabled);
}

export async function getAgentPrompt(lang: string): Promise<AgentPrompt> {
  return apiFetch<AgentPrompt>(`/api/agent/prompt?lang=${encodeURIComponent(lang)}`);
}

export async function installAgentSkill(
  targets: readonly SkillTarget[],
): Promise<Record<SkillTarget, { state: SkillInstallState; path: string }>> {
  const result = await apiFetch<{ targets: Record<SkillTarget, { state: SkillInstallState; path: string }> }>(
    "/api/agent/skill/install",
    { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({ targets }) },
  );
  return result.targets;
}

export async function listSecretRequests(): Promise<SecretRequest[]> {
  const result = await apiFetch<{ requests: SecretRequest[] }>("/api/agent/secret-requests", { timeoutMs: 5_000 });
  return result.requests;
}

export async function fulfillSecretRequest(id: string, value: string): Promise<void> {
  await apiFetch(`/api/agent/secret-requests/${encodeURIComponent(id)}/fulfill`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ value }),
  });
}

export async function cancelSecretRequest(id: string): Promise<void> {
  await apiFetch(`/api/agent/secret-requests/${encodeURIComponent(id)}/cancel`, { method: "POST" });
}
