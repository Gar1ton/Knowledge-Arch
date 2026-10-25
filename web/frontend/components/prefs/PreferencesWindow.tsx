"use client";
/**
 * 偏好设置窗口（替代旧的设置弹窗）：六个分组，菜单 Preferences…（⌘, / Ctrl+,）打开。
 * 数据流节点的设置不在这里，在主窗口「数据流」页的各节点上。
 */
import React, { useCallback } from "react";
import { Icon } from "@/components/ds/Icon";
import { Modal } from "@/components/ds/Modal";
import { useToast } from "@/components/ui/Toast";
import { useConsole, type PrefsGroup } from "@/lib/ConsoleContext";
import { useI18n, type I18nKey } from "@/lib/i18n";
import { patchSetting } from "@/lib/settingsApi";
import {
  AppearanceGroup,
  ComponentsGroup,
  DiagnosticsGroup,
  GeneralGroup,
  LocalModelsGroup,
  StorageGroup,
  restartService,
} from "./PrefsGroups";

const GROUPS: { id: PrefsGroup; labelKey: I18nKey; icon: string }[] = [
  { id: "general", labelKey: "prefs_group_general", icon: "settings" },
  { id: "appearance", labelKey: "prefs_group_appearance", icon: "sun" },
  { id: "storage", labelKey: "prefs_group_storage", icon: "db" },
  { id: "local_models", labelKey: "prefs_group_local_models", icon: "chip" },
  { id: "components", labelKey: "prefs_group_components", icon: "layers" },
  { id: "diagnostics", labelKey: "prefs_group_diagnostics", icon: "terminal" },
];

export function PreferencesWindow() {
  const { prefsGroup, openPrefs, closePrefs } = useConsole();
  const { t } = useI18n();
  const { toast } = useToast();

  const saveQuietly = useCallback((key: string, value: unknown) => {
    patchSetting(key, value).catch(() => undefined);
  }, []);

  const restart = useCallback(async () => {
    toast(t("flow_restart_running"), "info");
    await restartService();
  }, [t, toast]);

  if (prefsGroup === null) return null;

  return (
    <Modal title={t("prefs_title")} icon="settings" onClose={closePrefs} width={940}>
      <div style={{ display: "flex", height: "100%" }}>
        <nav style={{ width: 172, flexShrink: 0, borderRight: "1px solid var(--border)", background: "var(--surface)", padding: 10 }}>
          {GROUPS.map((g) => {
            const active = prefsGroup === g.id;
            return (
              <button
                key={g.id}
                type="button"
                onClick={() => openPrefs(g.id)}
                style={{
                  display: "flex", alignItems: "center", gap: 9, width: "100%", padding: "8px 10px",
                  borderRadius: "var(--radius-md)", border: "none", marginBottom: 2, textAlign: "left",
                  background: active ? "var(--accent-soft)" : "transparent",
                  color: active ? "var(--accent)" : "var(--fg-muted)",
                  cursor: "pointer", fontSize: 13, fontWeight: active ? 600 : 450, fontFamily: "var(--font-sans)",
                }}
              >
                <Icon name={g.icon} size={15} /> {t(g.labelKey)}
              </button>
            );
          })}
        </nav>
        <div style={{ flex: 1, overflow: "auto", padding: "18px 22px", minHeight: 0 }}>
          {prefsGroup === "general" && <GeneralGroup />}
          {prefsGroup === "appearance" && <AppearanceGroup onSaveSetting={saveQuietly} />}
          {prefsGroup === "storage" && <StorageGroup />}
          {prefsGroup === "local_models" && <LocalModelsGroup />}
          {prefsGroup === "components" && <ComponentsGroup onRestart={() => void restart()} />}
          {prefsGroup === "diagnostics" && <DiagnosticsGroup onRestart={() => void restart()} />}
        </div>
      </div>
    </Modal>
  );
}
