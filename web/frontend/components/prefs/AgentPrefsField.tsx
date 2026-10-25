"use client";
/**
 * 偏好设置 › 通用 › AI 助手：重新复制首次配置 prompt、一键安装 skill 到 Codex / Claude Code。
 * 接入开关本身是 schema 字段 `prefs.general.agent_access`，由 PrefsSchemaFields 渲染。
 */
import React, { useState } from "react";
import { Button } from "@/components/ds/Button";
import { Field } from "@/components/ds/Card";
import { useToast } from "@/components/ui/Toast";
import { copySetupPrompt, skillStateText, useAgentPrompt, useAgentStatus } from "@/components/desktop/AgentSetup";
import { installAgentSkill, SKILL_TARGETS } from "@/lib/agentApi";
import { useI18n, type I18nKey } from "@/lib/i18n";

export function AgentPrefsField() {
  const { t, lang } = useI18n();
  const { toast } = useToast();
  const [status, refresh] = useAgentStatus();
  const prompt = useAgentPrompt(lang);
  const [busy, setBusy] = useState(false);
  const skill = status?.skill;

  const run = async (action: () => Promise<unknown>, done: string) => {
    setBusy(true);
    try {
      await action();
      toast(done, "ok");
      refresh();
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setBusy(false);
    }
  };

  const skillHint = skill
    ? SKILL_TARGETS.map((target) => `${t(`agent_tab_${target}` as I18nKey)}: ${skillStateText(t, skill[target].state)}`).join(" · ")
    : "";
  return (
    <Field label={t("prefs_agent_title")} hint={[t("prefs_agent_hint"), skillHint].filter(Boolean).join(" ")}>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <Button size="sm" variant="outline" disabled={busy || !prompt}
          onClick={() => void run(() => copySetupPrompt(prompt, status), t("agent_copied"))}>
          {t("agent_copy")}
        </Button>
        <Button size="sm" variant="outline" loading={busy}
          onClick={() => void run(() => installAgentSkill(SKILL_TARGETS), t("agent_skill_done"))}>
          {skill && SKILL_TARGETS.some((target) => skill[target].state === "outdated")
            ? t("agent_skill_update") : t("agent_skill_install")}
        </Button>
      </div>
    </Field>
  );
}
