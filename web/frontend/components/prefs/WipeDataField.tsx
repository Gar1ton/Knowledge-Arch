"use client";
/**
 * 偏好设置 › 存储 ›「删除全部数据」（仅桌面壳）。
 *
 * 输入确认词后：后端删除钥匙串主密钥 → 清空界面本机缓存 → 壳停后端、删除数据 / 日志 / 缓存 /
 * 壳配置目录并退出。自选数据目录只删应用自己的文件（见 desktop/src-tauri/src/wipe.rs）。
 * macOS / Linux 卸载时无法询问用户，卸载前用这里清除数据。
 */

import React, { useState } from "react";
import { Button } from "@/components/ds/Button";
import { Field } from "@/components/ds/Card";
import { Modal } from "@/components/ds/Modal";
import { wipeDataAndQuit } from "@/lib/backend";
import { useI18n } from "@/lib/i18n";
import { forgetMasterKey } from "@/lib/settingsApi";

const CONFIRM_WORD = "DELETE";

export function WipeDataField() {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ready = typed.trim() === CONFIRM_WORD;

  const close = () => {
    if (busy) return;
    setOpen(false);
    setTyped("");
    setError(null);
  };

  const wipe = async () => {
    setBusy(true);
    setError(null);
    // 主密钥失败（钥匙串不可用 / 后端已停）不阻止删除：密文文件随数据目录一起删掉。
    await forgetMasterKey().catch(() => undefined);
    try {
      window.localStorage.clear();
    } catch {
      /* 存储不可用时忽略 */
    }
    try {
      await wipeDataAndQuit();
    } catch (err) {
      setBusy(false);
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <>
      <Field label={t("prefs_wipe_label")} hint={t("prefs_wipe_hint")}>
        <Button size="sm" variant="danger" onClick={() => setOpen(true)}>{t("prefs_wipe_button")}</Button>
      </Field>
      {open && (
        <Modal title={t("prefs_wipe_title")} icon="trash" onClose={close} width={500} height="auto"
          footer={(
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <Button variant="outline" size="sm" disabled={busy} onClick={close}>{t("prefs_wipe_cancel")}</Button>
              <Button variant="danger" size="sm" loading={busy} disabled={!ready} onClick={() => void wipe()}>
                {t("prefs_wipe_confirm")}
              </Button>
            </div>
          )}>
          <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: 18, fontSize: 13, lineHeight: 1.6 }}>
            <p style={{ margin: 0 }}>{t("prefs_wipe_body")}</p>
            <p style={{ margin: 0, fontSize: 12, color: "var(--fg-subtle)" }}>{t("prefs_wipe_custom_dir")}</p>
            <p style={{ margin: 0, fontSize: 12, color: "var(--fg-subtle)" }}>{t("prefs_wipe_external")}</p>
            <label style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 12 }}>
              {t("prefs_wipe_type", { word: CONFIRM_WORD })}
              <input
                className="flow-quick-input"
                autoComplete="off"
                autoFocus
                value={typed}
                disabled={busy}
                placeholder={CONFIRM_WORD}
                onChange={(e) => setTyped(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && ready) void wipe(); }}
              />
            </label>
            {error && <div role="alert" style={{ fontSize: 12, color: "var(--danger)" }}>{error}</div>}
          </div>
        </Modal>
      )}
    </>
  );
}
