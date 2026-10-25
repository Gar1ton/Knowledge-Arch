"use client";
/**
 * 偏好设置窗口的六个分组：通用 / 外观 / 存储 / 本地模型 / 组件 / 诊断。
 */
import React, { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ds/Badge";
import { Button } from "@/components/ds/Button";
import { Card, Field } from "@/components/ds/Card";
import { Select } from "@/components/ds/Select";
import { ThemeGallery } from "@/components/modals/ThemeGallery";
import { ModelRuntimePanel, useModelRuntime } from "@/components/panels/ModelRuntimePanel";
import { TerminalPanel } from "@/components/ui/terminal/TerminalPanel";
import { useToast } from "@/components/ui/Toast";
import { formatBytes } from "@/components/flow/SyncActions";
import {
  deleteLocalModel,
  getSystemInfo,
  listLocalModels,
  restartPlugin,
  type LocalModel,
  type SystemInfo,
} from "@/lib/api";
import {
  getShellPrefs,
  inDesktopShell,
  openLogDir,
  pickFolder,
  setShellPref,
} from "@/lib/backend";
import { useI18n, type Lang } from "@/lib/i18n";
import { useTheme } from "@/lib/theme";
import {
  installComponent,
  listComponents,
  uninstallComponent,
  type ComponentStatus,
  type ComponentsStatus,
} from "@/lib/settingsApi";
import { PrefsSchemaFields, useSettingsHome } from "./PrefsSchemaFields";

// ─── 通用 ──────────────────────────────────────────────────────

export function GeneralGroup() {
  const { t, setLang } = useI18n();
  const { items, reload } = useSettingsHome("prefs.general");
  return (
    <Card title={t("prefs_group_general")} icon="settings">
      <PrefsSchemaFields
        items={items}
        onSaved={() => void reload()}
        onApplied={(key, value) => {
          if (key === "prefs.general.language" && (value === "zh" || value === "en")) setLang(value as Lang);
        }}
      />
      {!inDesktopShell() && (
        <div style={{ fontSize: 11, color: "var(--fg-subtle)", paddingTop: 8 }}>{t("prefs_shell_only_hint")}</div>
      )}
    </Card>
  );
}

// ─── 外观 ──────────────────────────────────────────────────────

export function AppearanceGroup({ onSaveSetting }: { onSaveSetting: (key: string, value: unknown) => void }) {
  const { t } = useI18n();
  const { theme, setTheme } = useTheme();
  return (
    <>
      <Card title={t("settings_card_appearance")} icon="sun">
        <Field label={t("set_prefs_appearance_theme_mode")} hint={t("settings_theme_hint")}>
          <Select
            value={theme ?? "system"}
            onChange={(v) => { setTheme(v); onSaveSetting("prefs.appearance.theme_mode", v); }}
            options={[
              { value: "light", label: t("settings_theme_light") },
              { value: "dark", label: t("settings_theme_dark") },
              { value: "system", label: t("settings_theme_system") },
            ]}
          />
        </Field>
      </Card>
      <Card title={t("settings_theme_gallery")} icon="sparkle" badge={<Badge tone="accent">{t("settings_theme_gallery_badge")}</Badge>}>
        <div style={{ fontSize: 12, color: "var(--fg-muted)", lineHeight: 1.55, marginTop: 4 }}>
          {t("settings_theme_gallery_hint")}
        </div>
        <ThemeGallery onChange={(name) => onSaveSetting("prefs.appearance.palette", name)} />
      </Card>
    </>
  );
}

// ─── 存储 ──────────────────────────────────────────────────────

export function StorageGroup() {
  const { t } = useI18n();
  const { toast } = useToast();
  const { items, reload } = useSettingsHome("prefs.storage");
  const [info, setInfo] = useState<SystemInfo | null>(null);
  const [override, setOverride] = useState<string>("");

  useEffect(() => {
    getSystemInfo().then(setInfo).catch(() => undefined);
    getShellPrefs().then((p) => setOverride(String(p["prefs.storage.data_dir"] ?? ""))).catch(() => undefined);
  }, []);

  const change = async () => {
    const picked = await pickFolder(t("prefs_storage_pick_title"));
    if (!picked) return;
    try {
      await setShellPref("prefs.storage.data_dir", picked);
      setOverride(picked);
      toast(t("prefs_storage_restart_app"), "info");
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), "error");
    }
  };

  const reset = async () => {
    await setShellPref("prefs.storage.data_dir", "");
    setOverride("");
    toast(t("prefs_storage_restart_app"), "info");
  };

  return (
    <Card title={t("prefs_group_storage")} icon="db">
      <Field label={t("set_prefs_storage_data_dir")} hint={t("prefs_storage_data_dir_hint")}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-end" }}>
          <code style={{ fontSize: 11, maxWidth: 360, overflowWrap: "anywhere" }}>{info?.data_dir ?? "—"}</code>
          {override && override !== info?.data_dir && (
            <span style={{ fontSize: 11, color: "var(--warn, var(--accent))" }}>
              {t("prefs_storage_next_launch", { path: override })}
            </span>
          )}
          {inDesktopShell() ? (
            <div style={{ display: "flex", gap: 6 }}>
              <Button size="sm" variant="outline" onClick={() => void change()}>{t("prefs_storage_change")}</Button>
              {override && <Button size="sm" variant="outline" onClick={() => void reset()}>{t("prefs_storage_reset")}</Button>}
            </div>
          ) : (
            <span style={{ fontSize: 11, color: "var(--fg-subtle)" }}>{t("prefs_shell_only_hint")}</span>
          )}
        </div>
      </Field>
      <PrefsSchemaFields items={items} exclude={["prefs.storage.data_dir"]} onSaved={() => void reload()} />
    </Card>
  );
}

// ─── 本地模型 ──────────────────────────────────────────────────

export function LocalModelsGroup() {
  const { t } = useI18n();
  const { toast } = useToast();
  const { items, reload } = useSettingsHome("prefs.local_models");
  const { runtime, busy, error, doUnload } = useModelRuntime();
  const [models, setModels] = useState<LocalModel[]>([]);

  const refreshModels = useCallback(() => {
    listLocalModels().then(setModels).catch(() => setModels([]));
  }, []);
  useEffect(() => { refreshModels(); }, [refreshModels]);

  return (
    <>
      <Card title={t("prefs_group_local_models")} icon="chip">
        <PrefsSchemaFields items={items} onSaved={() => void reload()} />
      </Card>
      <Card title={t("models_modal_title")} icon="chip">
        <ModelRuntimePanel runtime={runtime} busy={busy} error={error} onUnload={(kinds) => void doUnload(kinds)} />
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10, paddingTop: 8 }}>
          <span style={{ fontSize: 11, color: "var(--fg-subtle)", lineHeight: 1.5 }}>{t("models_unload_hint")}</span>
          <Button size="sm" variant="outline" disabled={busy || (runtime?.resident_count ?? 0) === 0} onClick={() => void doUnload()}>
            {t("models_unload_all")}
          </Button>
        </div>
      </Card>
      <Card title={t("prefs_local_models_cache")} icon="folder">
        {models.length === 0 && <div style={{ fontSize: 12, color: "var(--fg-subtle)", padding: "8px 0" }}>{t("prefs_local_models_empty")}</div>}
        {models.map((m) => (
          <Field key={m.dir_name} label={m.name} hint={formatBytes(m.size_bytes)}>
            <Button size="sm" variant="outline" onClick={async () => {
              try {
                await deleteLocalModel(m.name);
                refreshModels();
              } catch (err) {
                toast(err instanceof Error ? err.message : String(err), "error");
              }
            }}>
              {t("btn_delete")}
            </Button>
          </Field>
        ))}
      </Card>
    </>
  );
}

// ─── 组件 ──────────────────────────────────────────────────────

function componentStateBadge(c: ComponentStatus, t: ReturnType<typeof useI18n>["t"]) {
  if (c.state === "installed") {
    return <Badge tone={c.active ? "ok" : "accent"}>{c.active ? t("comp_state_active") : t("comp_state_installed")}</Badge>;
  }
  if (c.state === "stale") return <Badge tone="warn">{t("comp_state_stale")}</Badge>;
  if (c.importable) return <Badge tone="ok">{t("comp_state_bundled")}</Badge>;
  return <Badge tone="neutral">{t("comp_state_missing")}</Badge>;
}

export function ComponentsGroup({ onRestart }: { onRestart: () => void }) {
  const { t } = useI18n();
  const { toast } = useToast();
  const [status, setStatus] = useState<ComponentsStatus | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [needsRestart, setNeedsRestart] = useState(false);

  const refresh = useCallback(() => {
    listComponents().then(setStatus).catch(() => setStatus(null));
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  const act = async (key: string, action: "install" | "uninstall") => {
    setBusyKey(key);
    if (action === "install") toast(t("comp_installing_hint"), "info");
    try {
      const res = action === "install" ? await installComponent(key) : await uninstallComponent(key);
      toast(res.message, res.status === "ok" ? "ok" : "error");
      if (res.status === "ok" && res.restart_required) setNeedsRestart(true);
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setBusyKey(null);
      refresh();
    }
  };

  return (
    <Card title={t("prefs_group_components")} icon="layers">
      <div style={{ fontSize: 12, color: "var(--fg-muted)", lineHeight: 1.55, padding: "4px 0 8px" }}>
        {t("comp_intro")}
      </div>
      {status && !status.installer_available && (
        <div role="alert" style={{ fontSize: 12, color: "var(--danger)", padding: "6px 0" }}>{t("comp_installer_missing")}</div>
      )}
      {(status?.components ?? []).map((c) => (
        <Field
          key={c.key}
          label={t(`comp_${c.key}` as Parameters<typeof t>[0])}
          hint={`${t(`comp_${c.key}_desc` as Parameters<typeof t>[0])}${c.size_bytes ? ` · ${formatBytes(c.size_bytes)}` : ""}`}
        >
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {componentStateBadge(c, t)}
            {c.state === "installed" ? (
              <Button size="sm" variant="outline" disabled={busyKey !== null} onClick={() => void act(c.key, "uninstall")}>
                {t("comp_uninstall")}
              </Button>
            ) : (
              <Button size="sm" variant="primary" loading={busyKey === c.key}
                disabled={busyKey !== null || !status?.installer_available || (c.importable && c.state === "missing")}
                onClick={() => void act(c.key, "install")}>
                {c.state === "stale" ? t("comp_reinstall") : t("comp_install")}
              </Button>
            )}
          </div>
        </Field>
      ))}
      {needsRestart && (
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: 10 }}>
          <span style={{ fontSize: 12, color: "var(--fg-muted)" }}>{t("flow_deps_restart_hint")}</span>
          <Button size="sm" variant="primary" onClick={onRestart}>{t("flow_restart_service")}</Button>
        </div>
      )}
      {status?.extras_dir && (
        <div style={{ fontSize: 11, color: "var(--fg-subtle)", paddingTop: 8, overflowWrap: "anywhere" }}>
          {t("comp_dir", { path: status.extras_dir })}
        </div>
      )}
    </Card>
  );
}

// ─── 诊断 ──────────────────────────────────────────────────────

export function DiagnosticsGroup({ onRestart }: { onRestart: () => void }) {
  const { t } = useI18n();
  const { items, reload } = useSettingsHome("prefs.diagnostics");
  const [info, setInfo] = useState<SystemInfo | null>(null);
  useEffect(() => { getSystemInfo().then(setInfo).catch(() => undefined); }, []);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 0, height: "100%" }}>
      <Card title={t("prefs_group_diagnostics")} icon="terminal">
        <PrefsSchemaFields items={items} onSaved={() => void reload()} />
        <Field label={t("prefs_diag_runtime")}>
          <code style={{ fontSize: 11 }}>
            Python {info?.python_version ?? "—"} · {info?.platform ?? "—"}
          </code>
        </Field>
        <Field label={t("prefs_diag_actions")}>
          <div style={{ display: "flex", gap: 8 }}>
            <Button size="sm" variant="outline" onClick={onRestart}>{t("flow_restart_service")}</Button>
            {inDesktopShell() && (
              <Button size="sm" variant="outline" onClick={() => void openLogDir()}>{t("crash_open_logs")}</Button>
            )}
          </div>
        </Field>
      </Card>
      <div style={{ height: 420, minHeight: 0, marginTop: 12, border: "1px solid var(--border)", borderRadius: "var(--radius-md)", overflow: "hidden" }}>
        <TerminalPanel variant="embedded" />
      </div>
    </div>
  );
}

/** 「重启服务」：软重启后端（进程与端口不变），完成后由各面板自行刷新。 */
export async function restartService(): Promise<void> {
  try {
    await restartPlugin();
  } catch {
    /* 软重启会断开当前请求，网络错误视为已触发 */
  }
}
