/**
 * lib/askModes.ts — 检索模式的能力表，来自后端 /api/capabilities 的 ask_modes
 * （唯一真相源：kacore/retrieval_modes.py 的 MODES）。
 *
 * 对话栏据此决定哪些模式可选、范围怎么显示、联网补充是否适用，不在前端另写一份规则；
 * 以后加模式只改后端那张表。表还没取到（后端重启中等）时返回空对象，调用方按「不知道」处理，
 * 窗口重新获得焦点时再取一次。
 */

import { useEffect, useState } from "react";
import { getCapabilities, type AskModeSpec } from "@/lib/api";

export type AskModes = Partial<Record<string, AskModeSpec>>;

let cached: Promise<AskModes> | null = null;

function load(): Promise<AskModes> {
  if (!cached) {
    cached = getCapabilities()
      .then((caps) => Object.fromEntries((caps.ask_modes ?? []).map((m) => [m.id, m])) as AskModes)
      .then((modes) => {
        if (Object.keys(modes).length === 0) cached = null; // 旧后端没有这张表：下次再试
        return modes;
      })
      .catch((err) => {
        cached = null;
        throw err;
      });
  }
  return cached;
}

export function useAskModes(): AskModes {
  const [modes, setModes] = useState<AskModes>({});
  useEffect(() => {
    let alive = true;
    const refresh = () => {
      load()
        .then((next) => {
          if (alive) setModes(next);
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
  return modes;
}
