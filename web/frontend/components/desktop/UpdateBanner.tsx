"use client";
/**
 * 新版本提示条：桌面壳启动检查到更新时发 `update://available`，这里显示版本号与「安装并重启」。
 */
import React, { useEffect, useState } from "react";
import { Button } from "@/components/ds/Button";
import { useToast } from "@/components/ui/Toast";
import { UPDATE_AVAILABLE_EVENT, installUpdate, onShellEvent, type UpdateInfo } from "@/lib/backend";
import { useI18n } from "@/lib/i18n";

export function UpdateBanner() {
  const { t } = useI18n();
  const { toast } = useToast();
  const [info, setInfo] = useState<UpdateInfo | null>(null);
  const [installing, setInstalling] = useState(false);

  useEffect(() => {
    let off: (() => void) | undefined;
    void onShellEvent<UpdateInfo>(UPDATE_AVAILABLE_EVENT, (payload) => {
      if (payload?.available) setInfo(payload);
    }).then((f) => { off = f; });
    return () => off?.();
  }, []);

  if (!info) return null;
  return (
    <div className="ka-update-banner" role="status">
      <span>{t("update_available", { version: info.version ?? "" })}</span>
      <Button
        size="sm"
        variant="primary"
        loading={installing}
        onClick={async () => {
          setInstalling(true);
          try {
            await installUpdate();
          } catch (err) {
            toast(err instanceof Error ? err.message : String(err), "error");
            setInstalling(false);
          }
        }}
      >
        {t("update_install")}
      </Button>
      <Button size="sm" variant="outline" disabled={installing} onClick={() => setInfo(null)}>
        {t("update_later")}
      </Button>
    </div>
  );
}
