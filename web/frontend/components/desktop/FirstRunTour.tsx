"use client";
/**
 * 首次使用引导（不用 AI 助手时）：聚光灯 + 提示卡，一步步带用户配到「能收到第一条回复」。
 *
 * - 锁定：四块遮罩围出目标元素，只有高亮处（以及浮在引导层之上的弹窗、下拉）能点；
 *   提示卡上的「跳过引导」随时可退出。
 * - 每步按真实状态自动完成（能力快照、库是否为空、是否已提问），从第一个未完成的步骤开始；
 *   目标暂时不在屏幕上时（例如节点在数据流页），先高亮切换视图的按钮。
 * - 锚点是界面上的 `data-tour` 属性；状态与事件见 lib/tour.ts。
 */
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { Button } from "@/components/ds/Button";
import { useToast } from "@/components/ui/Toast";
import { getCapabilities, type CapabilitiesData } from "@/lib/api";
import { getAgentPrompt } from "@/lib/agentApi";
import { useConsole } from "@/lib/ConsoleContext";
import { useI18n, type I18nKey } from "@/lib/i18n";
import { getLegacyImportState, listProviders, type Provider } from "@/lib/settingsApi";
import {
  ASK_SENT_EVENT,
  finishTour,
  focusFlowNode,
  skippedSteps,
  skipStep,
  TOUR_START_EVENT,
  tourPending,
} from "@/lib/tour";
import { copySetupPrompt } from "./AgentSetup";
import styles from "./FirstRunTour.module.css";

type StepId = "flow" | "providers" | "models" | "embedding" | "upload" | "ask";

interface StepSpec {
  id: StepId;
  /** 依次尝试的锚点：第一个在屏幕上的被高亮；后面的是「先去那个视图」的兜底。 */
  anchors: string[];
}

const STEPS: StepSpec[] = [
  { id: "flow", anchors: ["topbar-flow"] },
  { id: "providers", anchors: ["node-providers", "topbar-flow"] },
  { id: "models", anchors: ["node-models", "topbar-flow"] },
  { id: "embedding", anchors: ["node-embedding", "topbar-flow"] },
  { id: "upload", anchors: ["upload", "topbar-workspace"] },
  { id: "ask", anchors: ["ask", "topbar-workspace"] },
];
const POLL_MS = 2_500;
const LOCAL_HOST = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:|\/|$)/i;
const HOLE_PAD = 6;
const CARD_W = 320;
const GAP = 14;

interface Facts {
  caps: CapabilitiesData | null;
  providers: Provider[];
  libraryEmpty: boolean;
  askSent: boolean;
  skipped: Set<string>;
  view: string;
}

function stageReady(caps: CapabilitiesData | null, id: string): boolean {
  const stage = caps?.pipeline.find((s) => s.id === id);
  return Boolean(stage && stage.configured !== false && stage.status === "ready");
}

function stepDone(id: StepId, f: Facts): boolean {
  switch (id) {
    case "flow":
      return f.view === "flow" || stepDone("providers", f);
    case "providers":
      // 只建连接不算：要填了 Key，或是本机免 Key 的服务（Ollama / LM Studio）。
      return f.providers.some((p) => p.api_key_set || LOCAL_HOST.test(p.base_url));
    case "models":
      return Boolean(f.caps?.pipeline.find((s) => s.id === "models")?.configured);
    case "embedding":
      return f.skipped.has("embedding") || stageReady(f.caps, "embedding");
    case "upload":
      return !f.libraryEmpty;
    case "ask":
      return f.askSent;
  }
}

function findAnchor(anchors: string[]): { rect: DOMRect; anchor: string } | null {
  for (const anchor of anchors) {
    const el = document.querySelector<HTMLElement>(`[data-tour="${anchor}"]`);
    if (!el) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) return { rect, anchor };
  }
  return null;
}

/** 提示卡位置：优先目标右侧，其次下方、上方、左侧，最后居中；都夹在视口内。 */
function cardPosition(rect: DOMRect | null, cardH: number): React.CSSProperties {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const clampX = (x: number) => Math.max(12, Math.min(x, vw - CARD_W - 12));
  const clampY = (y: number) => Math.max(12, Math.min(y, vh - cardH - 12));
  if (!rect) return { left: clampX((vw - CARD_W) / 2), top: clampY((vh - cardH) / 2) };
  if (rect.right + GAP + CARD_W <= vw - 12) return { left: rect.right + GAP, top: clampY(rect.top) };
  if (rect.bottom + GAP + cardH <= vh - 12) return { left: clampX(rect.left), top: rect.bottom + GAP };
  if (rect.top - GAP - cardH >= 12) return { left: clampX(rect.left), top: rect.top - GAP - cardH };
  if (rect.left - GAP - CARD_W >= 12) return { left: rect.left - GAP - CARD_W, top: clampY(rect.top) };
  return { left: clampX((vw - CARD_W) / 2), top: clampY((vh - cardH) / 2) };
}

function Masks({ hole }: { hole: DOMRect | null }) {
  if (!hole) return <div className={styles.mask} style={{ inset: 0 }} />;
  const top = Math.max(0, hole.top - HOLE_PAD);
  const left = Math.max(0, hole.left - HOLE_PAD);
  const right = hole.right + HOLE_PAD;
  const bottom = hole.bottom + HOLE_PAD;
  return (
    <>
      <div className={styles.mask} style={{ top: 0, left: 0, right: 0, height: top }} />
      <div className={styles.mask} style={{ top: bottom, left: 0, right: 0, bottom: 0 }} />
      <div className={styles.mask} style={{ top, left: 0, width: left, height: bottom - top }} />
      <div className={styles.mask} style={{ top, left: right, right: 0, height: bottom - top }} />
      <div className={styles.ring} style={{ top, left, width: right - left, height: bottom - top }} />
    </>
  );
}

export function FirstRunTour() {
  const { t, lang } = useI18n();
  const { toast } = useToast();
  const { view } = useConsole();
  const [active, setActive] = useState(false);
  const [caps, setCaps] = useState<CapabilitiesData | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [libraryEmpty, setLibraryEmpty] = useState(true);
  const [askSent, setAskSent] = useState(false);
  const [skipped, setSkipped] = useState<Set<string>>(() => new Set());
  const [hole, setHole] = useState<{ rect: DOMRect; anchor: string } | null>(null);
  const [cardH, setCardH] = useState(180);
  const cardRef = React.useRef<HTMLDivElement | null>(null);

  // 启动：本机标记为待引导时，或偏好设置里点「重新查看引导」时。
  useEffect(() => {
    const start = () => {
      setSkipped(skippedSteps());
      setAskSent(false);
      setActive(tourPending());
    };
    start();
    window.addEventListener(TOUR_START_EVENT, start);
    const onAsk = () => setAskSent(true);
    window.addEventListener(ASK_SENT_EVENT, onAsk);
    return () => {
      window.removeEventListener(TOUR_START_EVENT, start);
      window.removeEventListener(ASK_SENT_EVENT, onAsk);
    };
  }, []);

  // 真实状态轮询（只在引导进行中）。
  useEffect(() => {
    if (!active) return undefined;
    let alive = true;
    const load = () => {
      getCapabilities().then((c) => { if (alive) setCaps(c); }).catch(() => undefined);
      listProviders().then((l) => { if (alive) setProviders(l.providers); }).catch(() => undefined);
      getLegacyImportState().then((s) => { if (alive) setLibraryEmpty(s.library_empty); }).catch(() => undefined);
    };
    load();
    const timer = setInterval(load, POLL_MS);
    return () => { alive = false; clearInterval(timer); };
  }, [active]);

  const facts: Facts = { caps, providers, libraryEmpty, askSent, skipped, view };
  const index = useMemo(
    () => STEPS.findIndex((step) => !stepDone(step.id, facts)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [caps, providers, libraryEmpty, askSent, skipped, view],
  );
  const step = index >= 0 ? STEPS[index] : null;

  // 目标位置：节点展开、面板滚动、窗口缩放都会移动它，定时重新量。
  useLayoutEffect(() => {
    if (!active || !step) return undefined;
    const measure = () => setHole(findAnchor(step.anchors));
    measure();
    const timer = setInterval(measure, 250);
    window.addEventListener("resize", measure);
    return () => { clearInterval(timer); window.removeEventListener("resize", measure); };
  }, [active, step]);

  // 目标是数据流节点而且不完全在屏幕内时，请画布把它移到中央（每步一次）。
  const focusedStep = React.useRef<string | null>(null);
  useEffect(() => {
    if (!step || !hole || hole.anchor !== step.anchors[0] || !hole.anchor.startsWith("node-")) return;
    if (focusedStep.current === step.id) return;
    const r = hole.rect;
    const inView = r.top >= 0 && r.left >= 0 && r.bottom <= window.innerHeight && r.right <= window.innerWidth;
    focusedStep.current = step.id;
    if (!inView) focusFlowNode(hole.anchor.slice("node-".length));
  }, [step, hole]);

  // 提示卡高度随文案变化，用来决定放在目标上方还是下方。
  useLayoutEffect(() => {
    const height = cardRef.current?.offsetHeight;
    if (height && height !== cardH) setCardH(height);
  }, [active, index, hole?.anchor, cardH]);

  const close = useCallback(() => {
    finishTour();
    setActive(false);
  }, []);

  if (!active || typeof document === "undefined") return null;

  const finished = !step;
  const usingFallback = Boolean(step && hole && hole.anchor !== step.anchors[0]);
  const titleKey = (finished ? "tour_done_title" : `tour_${step.id}_title`) as I18nKey;
  const bodyKey = (finished ? "tour_done_body" : `tour_${step.id}_body`) as I18nKey;

  // 文案里引用顶栏按钮的真实名称（中文「数据流」，英文「Workflow」）。
  const labels = { flow: t("topbar_workflow"), workspace: t("topbar_workspace") };
  const skipEmbedding = () => setSkipped(skipStep("embedding"));
  const copyAgentPrompt = async () => {
    try {
      const prompt = await getAgentPrompt(lang);
      await copySetupPrompt(prompt.prompt, null);
      toast(t("agent_copied"), "ok");
      skipEmbedding();
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), "error");
    }
  };

  return createPortal(
    <>
      <Masks hole={finished ? null : hole?.rect ?? null} />
      <div
        ref={cardRef}
        className={styles.card}
        role="dialog"
        aria-live="polite"
        aria-label={t(titleKey)}
        style={cardPosition(finished ? null : hole?.rect ?? null, cardH)}
      >
        {!finished && <p className={styles.step}>{t("tour_step", { n: index + 1, total: STEPS.length })}</p>}
        <h3 className={styles.title}>{t(titleKey)}</h3>
        <p className={styles.body}>{t(bodyKey, labels)}</p>
        {usingFallback && (
          <p className={styles.hint}>
            {t(hole?.anchor === "topbar-flow" ? "tour_go_flow" : "tour_go_workspace", labels)}
          </p>
        )}
        {step?.id === "embedding" && (
          <div className={styles.choices}>
            <Button size="sm" variant="outline" onClick={skipEmbedding}>{t("tour_embedding_keyword")}</Button>
            <Button size="sm" variant="outline" onClick={() => void copyAgentPrompt()}>{t("tour_embedding_agent")}</Button>
          </div>
        )}
        <div className={styles.foot}>
          <div className={styles.dots} aria-hidden="true">
            {STEPS.map((s, i) => (
              <span key={s.id} className={`${styles.dot} ${
                finished || i < index ? styles.dotDone : i === index ? styles.dotCurrent : ""}`} />
            ))}
          </div>
          <div className={styles.actions}>
            {finished ? (
              <Button size="sm" variant="primary" onClick={close}>{t("tour_finish")}</Button>
            ) : (
              <Button size="sm" variant="ghost" onClick={close}>{t("tour_skip")}</Button>
            )}
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}
