"use client";
/**
 * 全局密钥弹框：AI 助手需要 API Key / token 时创建「密钥请求」，这里让用户直接填进应用。
 *
 * 只在 AI 助手接入开启时轮询；值经界面 token 提交给后端写进加密库，助手只能看到请求状态。
 * 欢迎页与主界面都挂载（ReadyShell），所以首次配置途中也能弹出。
 */
import React, { useEffect, useState } from "react";
import { Button } from "@/components/ds/Button";
import { Modal } from "@/components/ds/Modal";
import { useToast } from "@/components/ui/Toast";
import {
  cancelSecretRequest,
  fulfillSecretRequest,
  listSecretRequests,
  type SecretRequest,
} from "@/lib/agentApi";
import { useI18n } from "@/lib/i18n";
import { useAgentStatus } from "./AgentSetup";

export function SecretRequestDialog() {
  const { t } = useI18n();
  const { toast } = useToast();
  const [status] = useAgentStatus();
  const [request, setRequest] = useState<SecretRequest | null>(null);
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = status?.enabled ? status.pending_secret_requests : 0;

  useEffect(() => {
    if (!pending || request) return;
    listSecretRequests()
      .then((items) => setRequest(items.find((item) => item.status === "pending") ?? null))
      .catch(() => undefined);
  }, [pending, request]);

  if (!request) return null;

  const close = () => {
    setRequest(null);
    setValue("");
    setError("");
  };

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await fulfillSecretRequest(request.id, value.trim());
      toast(t("secret_saved"), "ok");
      close();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const decline = async () => {
    await cancelSecretRequest(request.id).catch(() => undefined);
    close();
  };

  return (
    <Modal title={t("secret_title")} icon="key" onClose={() => void decline()} width={460} height="auto"
      footer={(
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => void decline()}>{t("secret_decline")}</Button>
          <Button variant="primary" size="sm" loading={busy} disabled={!value.trim()} onClick={() => void save()}>
            {t("secret_save")}
          </Button>
        </div>
      )}>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: 18 }}>
        <p style={{ margin: 0, fontSize: 13, lineHeight: 1.6 }}>{t("secret_body", { label: request.label })}</p>
        {request.reason && (
          <p style={{ margin: 0, fontSize: 12, color: "var(--fg-subtle)" }}>{t("secret_reason", { reason: request.reason })}</p>
        )}
        <input
          className="flow-quick-input"
          type="password"
          autoComplete="off"
          autoFocus
          value={value}
          placeholder={t("secret_placeholder")}
          aria-label={request.label}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && value.trim()) void save(); }}
        />
        {error && <div role="alert" style={{ fontSize: 12, color: "var(--danger)" }}>{error}</div>}
      </div>
    </Modal>
  );
}
