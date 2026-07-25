"use client";

import React, { useEffect, useState } from "react";
import { Badge } from "@/components/ds/Badge";
import { Button } from "@/components/ds/Button";
import { Toggle } from "@/components/ds/Toggle";
import { useToast } from "@/components/ui/Toast";
import { deleteDiscordToken, getDiscordSettings, setDiscordToken, updateDiscordSettings, type DiscordSettings } from "@/lib/api";

const inputStyle: React.CSSProperties = { width: "100%", height: 32, border: "1px solid var(--border-strong)", borderRadius: "var(--radius-md)", background: "var(--surface)", color: "var(--fg)", padding: "0 10px", fontFamily: "var(--font-mono)", fontSize: 12 };

function parseIds(value: string): string[] {
  return Array.from(new Set(value.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean)));
}

export function DiscordConfigCard() {
  const { toast } = useToast();
  const [settings, setSettings] = useState<DiscordSettings | null>(null);
  const [token, setToken] = useState("");
  const [users, setUsers] = useState("");
  const [guilds, setGuilds] = useState("");
  const [channels, setChannels] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    const value = await getDiscordSettings();
    setSettings(value);
    setUsers(value.allowed_user_ids.join(", "));
    setGuilds(value.allowed_guild_ids.join(", "));
    setChannels(value.allowed_channel_ids.join(", "));
  }
  useEffect(() => {
    let active = true;
    void getDiscordSettings()
      .then((value) => {
        if (!active) return;
        setSettings(value);
        setUsers(value.allowed_user_ids.join(", "));
        setGuilds(value.allowed_guild_ids.join(", "));
        setChannels(value.allowed_channel_ids.join(", "));
      })
      .catch(() => {});
    return () => { active = false; };
  }, []);

  async function save() {
    if (!settings) return;
    setBusy(true);
    try {
      await updateDiscordSettings({
        enabled: settings.enabled,
        allowed_user_ids: parseIds(users),
        allowed_guild_ids: parseIds(guilds),
        allowed_channel_ids: parseIds(channels),
      });
      if (token.trim()) await setDiscordToken(token);
      setToken("");
      await refresh();
      toast("Discord 配置已保存，重启独立版后生效", "ok");
    } catch (error) {
      toast(error instanceof Error ? error.message : "Discord 配置保存失败", "error");
    } finally { setBusy(false); }
  }

  if (!settings) return null;
  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "var(--radius-xl)", padding: 16, marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <strong style={{ fontSize: 13, color: "var(--heading)" }}>Discord 渠道</strong>
        <Badge tone={settings.status.connected ? "ok" : settings.enabled ? "warn" : "neutral"}>{settings.status.connected ? "CONNECTED" : settings.enabled ? "RESTART" : "OFF"}</Badge>
        <span style={{ flex: 1 }} />
        <Toggle checked={settings.enabled} onChange={(enabled) => setSettings({ ...settings, enabled })} />
      </div>
      <div style={{ fontSize: 11, color: "var(--fg-muted)", marginBottom: 10, lineHeight: 1.5 }}>
        仅响应私信、@提及和回复 Bot。Developer Portal 必须启用 Message Content Intent。状态：{settings.status.detail || "—"}
      </div>
      <div style={{ display: "grid", gap: 8 }}>
        <input style={inputStyle} value={users} onChange={(e) => setUsers(e.target.value)} placeholder="允许的 User ID（必填，逗号分隔）" />
        <input style={inputStyle} value={guilds} onChange={(e) => setGuilds(e.target.value)} placeholder="允许的 Guild ID（可选）" />
        <input style={inputStyle} value={channels} onChange={(e) => setChannels(e.target.value)} placeholder="允许的 Channel/父频道 ID（可选）" />
        <input type="password" style={inputStyle} value={token} onChange={(e) => setToken(e.target.value)} placeholder={settings.token_present ? "Bot Token 已保存；留空保持不变" : "Bot Token"} />
      </div>
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 10 }}>
        {settings.token_present && <Button variant="ghost" size="sm" onClick={async () => { await deleteDiscordToken(); await refresh(); }}>删除 Token</Button>}
        <Button size="sm" loading={busy} onClick={() => void save()}>保存 Discord 配置</Button>
      </div>
    </div>
  );
}
