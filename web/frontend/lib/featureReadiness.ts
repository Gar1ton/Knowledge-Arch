/**
 * lib/featureReadiness.ts — 需要另外配置的功能（Zotero 同步、R2 备份、LightRAG 图谱）是否就绪。
 *
 * 数据来自 /api/capabilities 各环节的 status（ready / degraded / off）；界面据此把未就绪的
 * 按钮显示为灰色，点击时再查一次（刚在数据流页配好也能立刻用），仍未就绪就提示去配置。
 */

import { useCallback, useEffect, useState } from "react";
import { getCapabilities } from "@/lib/api";

export type Feature = "zotero" | "r2" | "graph";

type Readiness = Partial<Record<Feature, boolean>>;

const FEATURES: readonly Feature[] = ["zotero", "r2", "graph"];

async function loadReadiness(): Promise<Readiness> {
  const caps = await getCapabilities();
  const out: Readiness = {};
  for (const stage of caps.pipeline) {
    if ((FEATURES as readonly string[]).includes(stage.id)) {
      out[stage.id as Feature] = stage.status === "ready";
    }
  }
  return out;
}

export function useFeatureReadiness(): {
  ready: Readiness;
  ensure: (feature: Feature) => Promise<boolean>;
} {
  const [ready, setReady] = useState<Readiness>({});

  useEffect(() => {
    let alive = true;
    const refresh = () => {
      loadReadiness()
        .then((next) => {
          if (alive) setReady(next);
        })
        .catch(() => undefined);
    };
    refresh();
    window.addEventListener("focus", refresh);
    return () => {
      alive = false;
      window.removeEventListener("focus", refresh);
    };
  }, []);

  /** 现查一次；查不到（网络 / 后端重启中）时放行，交给后端给出具体错误。 */
  const ensure = useCallback(async (feature: Feature) => {
    try {
      const next = await loadReadiness();
      setReady(next);
      return next[feature] !== false;
    } catch {
      return true;
    }
  }, []);

  return { ready, ensure };
}
