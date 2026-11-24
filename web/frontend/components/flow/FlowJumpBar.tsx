"use client";
/**
 * 数据流页头的快捷按钮条：每个节点一个按钮，点击让画布平移并缩放到该节点（由 FlowDiagram 执行）。
 *
 * - 状态色与画布同源：传入的 stages 已经过 `effectiveStages`（含上游 degraded 的级联）。
 * - 「待处理」的节点集中到最左一组，便于先改；其余按 JUMP_GROUPS 分组，被提走的节点不重复出现，
 *   组内一个都不剩时整组隐藏。
 * - 原生滚动条隐藏；内容超出时两端淡出并出现箭头，鼠标滚轮可直接横向滚动。
 */
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { PipelineStage } from "@/lib/api";
import type { I18nKey, TFunc } from "@/lib/i18n";
import { ChevronIcon } from "./Icons";
import {
  isFlowStageId,
  JUMP_GROUPS,
  JUMP_LABEL_KEYS,
  STAGE_META,
  type FlowStageId,
  type FlowStageStatus,
} from "./model";

const STATUS_KEY: Record<FlowStageStatus, I18nKey> = {
  ready: "flow_status_ready",
  degraded: "flow_status_degraded",
  off: "flow_status_off",
  info: "flow_status_info",
};

/** 按钮滚进可见区时离边缘留的距离，避开两端的淡出与箭头。 */
const EDGE_GUTTER = 36;

type Group = { key: string; label: string; ids: FlowStageId[]; chain: boolean; attention: boolean };

function JumpButton({
  id,
  status,
  active,
  t,
  onJump,
}: {
  id: FlowStageId;
  status: FlowStageStatus;
  active: boolean;
  t: TFunc;
  onJump: (id: FlowStageId) => void;
}) {
  return (
    <button
      type="button"
      data-jump={id}
      className={`flow-jump-btn ${status} ${active ? "is-active" : ""}`}
      aria-pressed={active}
      title={t("flow_jump_tip", { name: t(STAGE_META[id].titleKey), status: t(STATUS_KEY[status]) })}
      onClick={() => onJump(id)}
    >
      <span className="flow-jump-dot" />
      {t(JUMP_LABEL_KEYS[id])}
    </button>
  );
}

export function FlowJumpBar({
  stages,
  activeId,
  t,
  onJump,
}: {
  stages: PipelineStage[];
  activeId: string | null;
  t: TFunc;
  onJump: (id: FlowStageId) => void;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [edges, setEdges] = useState({ left: false, right: false });

  const statusById = useMemo(() => {
    const map = new Map<FlowStageId, FlowStageStatus>();
    for (const stage of stages) if (isFlowStageId(stage.id)) map.set(stage.id, stage.status);
    return map;
  }, [stages]);

  const groups = useMemo<Group[]>(() => {
    const attention = [...statusById.entries()]
      .filter(([, status]) => status === "degraded")
      .map(([id]) => id)
      .sort((a, b) => STAGE_META[a].idx - STAGE_META[b].idx);
    const out: Group[] = [];
    if (attention.length > 0) {
      out.push({
        key: "attention",
        label: t("flow_jump_group_attention", { n: attention.length }),
        ids: attention,
        chain: false,
        attention: true,
      });
    }
    for (const group of JUMP_GROUPS) {
      const ids = group.ids.filter((id) => statusById.has(id) && !attention.includes(id));
      if (ids.length === 0) continue;
      out.push({ key: group.labelKey, label: t(group.labelKey), ids, chain: Boolean(group.chain), attention: false });
    }
    return out;
  }, [statusById, t]);

  const updateEdges = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const left = el.scrollLeft > 2;
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 2;
    setEdges((prev) => (prev.left === left && prev.right === right ? prev : { left, right }));
  }, []);

  useLayoutEffect(() => {
    updateEdges();
  }, [groups, updateEdges]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    // 纵向滚轮转成横向滚动；React 的 onWheel 是 passive，调不了 preventDefault，所以手动挂。
    const onWheel = (event: WheelEvent) => {
      if (Math.abs(event.deltaY) <= Math.abs(event.deltaX) || el.scrollWidth <= el.clientWidth) return;
      event.preventDefault();
      el.scrollLeft += event.deltaY;
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("scroll", updateEdges, { passive: true });
    const observer = "ResizeObserver" in window ? new ResizeObserver(updateEdges) : null;
    observer?.observe(el);
    return () => {
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("scroll", updateEdges);
      observer?.disconnect();
    };
  }, [updateEdges]);

  // 选中的节点（点按钮或点画布）对应的按钮若在可见区外，把它滚进来。
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !activeId) return;
    const button = el.querySelector<HTMLElement>(`[data-jump="${activeId}"]`);
    if (!button) return;
    const box = el.getBoundingClientRect();
    const rect = button.getBoundingClientRect();
    if (rect.left < box.left + EDGE_GUTTER) {
      el.scrollBy({ left: rect.left - box.left - EDGE_GUTTER, behavior: "smooth" });
    } else if (rect.right > box.right - EDGE_GUTTER) {
      el.scrollBy({ left: rect.right - box.right + EDGE_GUTTER, behavior: "smooth" });
    }
  }, [activeId, groups]);

  const scrollByPage = (dir: -1 | 1) => {
    const el = scrollRef.current;
    if (el) el.scrollBy({ left: dir * el.clientWidth * 0.6, behavior: "smooth" });
  };

  if (groups.length === 0) return null;

  return (
    <nav className="flow-jump" aria-label={t("flow_jump_nav")}>
      {edges.left && (
        <button
          type="button"
          className="flow-jump-arrow flow-jump-arrow--left"
          aria-label={t("flow_jump_scroll_left")}
          onClick={() => scrollByPage(-1)}
        >
          <ChevronIcon dir="left" />
        </button>
      )}
      <div
        ref={scrollRef}
        className={`flow-jump-scroll ${edges.left ? "fade-left" : ""} ${edges.right ? "fade-right" : ""}`}
      >
        {groups.map((group) => (
          <div key={group.key} className={`flow-jump-group ${group.attention ? "is-attention" : ""}`}>
            <span className="flow-jump-group-label">{group.label}</span>
            <div className="flow-jump-group-items">
              {group.ids.map((id, index) => (
                <React.Fragment key={id}>
                  {group.chain && index > 0 && (
                    <span className="flow-jump-sep" aria-hidden="true">
                      {group.ids[index - 1] === "retrieval" && id === "graph" ? "/" : "›"}
                    </span>
                  )}
                  <JumpButton
                    id={id}
                    status={statusById.get(id) ?? "off"}
                    active={activeId === id}
                    t={t}
                    onJump={onJump}
                  />
                </React.Fragment>
              ))}
            </div>
          </div>
        ))}
      </div>
      {edges.right && (
        <button
          type="button"
          className="flow-jump-arrow flow-jump-arrow--right"
          aria-label={t("flow_jump_scroll_right")}
          onClick={() => scrollByPage(1)}
        >
          <ChevronIcon dir="right" />
        </button>
      )}
    </nav>
  );
}
