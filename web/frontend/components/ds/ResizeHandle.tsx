"use client";
/**
 * 竖向分栏把手：左右拖动调整左侧宽度，双击还原；样式与工作台的分栏把手一致（细竖线，悬停变深）。
 *
 * 用法：父容器横向 flex，左栏宽度交给 `useResizableWidth`，把手放在两栏之间。
 * 宽度记在本机（localStorage），下次打开还是上次拖的宽度。
 */
import React, { useCallback, useRef, useState } from "react";

function readSaved(storageKey: string, initial: number): number {
  try {
    const saved = typeof localStorage === "undefined" ? 0 : Number(localStorage.getItem(storageKey));
    return saved > 0 ? saved : initial;
  } catch {
    return initial; // 读不到本机存储时用默认宽度
  }
}

/** 只在客户端交互后才出现的面板用：初值直接读本机存储（不经 effect）。 */
export function useResizableWidth(storageKey: string, initial: number, min: number, maxRatio = 0.7) {
  const [width, setWidth] = useState(() => readSaved(storageKey, initial));
  const commit = useCallback(
    (next: number, containerWidth: number) => {
      const clamped = Math.round(Math.min(Math.max(next, min), Math.max(min, containerWidth * maxRatio)));
      setWidth(clamped);
      return clamped;
    },
    [min, maxRatio],
  );
  const save = useCallback((value: number | null) => {
    try {
      if (value === null) localStorage.removeItem(storageKey);
      else localStorage.setItem(storageKey, String(value));
    } catch {
      /* 存不下时只是下次回到默认宽度 */
    }
  }, [storageKey]);
  const reset = useCallback(() => {
    setWidth(initial);
    save(null);
  }, [initial, save]);
  return { width, commit, save, reset };
}

export function ResizeHandle({
  containerRef,
  width,
  onResize,
  onEnd,
  onReset,
  label,
}: {
  containerRef: React.RefObject<HTMLElement | null>;
  width: number;
  onResize: (next: number, containerWidth: number) => number;
  onEnd: (final: number) => void;
  onReset: () => void;
  label: string;
}) {
  const [active, setActive] = useState(false);
  const [hover, setHover] = useState(false);
  const start = useRef<{ x: number; w: number; last: number } | null>(null);

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    start.current = { x: e.clientX, w: width, last: width };
    e.currentTarget.setPointerCapture(e.pointerId);
    setActive(true);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const s = start.current;
    const box = containerRef.current?.getBoundingClientRect();
    if (!s || !box) return;
    s.last = onResize(s.w + e.clientX - s.x, box.width);
  };
  const finish = (e: React.PointerEvent<HTMLDivElement>) => {
    const s = start.current;
    if (!s) return;
    start.current = null;
    setActive(false);
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* 指针已经离开 */
    }
    onEnd(s.last);
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      title={label}
      className={`ds-resize ${active ? "is-active" : ""} ${hover ? "is-hover" : ""}`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={finish}
      onPointerCancel={finish}
      onDoubleClick={onReset}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <span />
    </div>
  );
}
