"use client";
import React, { useEffect, useRef, useState } from "react";
import type { PdfItemRange } from "@/lib/pdfSearch";
import styles from "./PdfPage.module.css";

/** pdf.js 的最小类型面：只声明本阅读器用到的部分（pdfjs-dist 动态 import，不直接引其类型）。 */
export type PdfViewport = {
  width: number;
  height: number;
  scale: number;
  rotation: number;
  rawDims: { pageWidth: number; pageHeight: number; pageX: number; pageY: number };
};

export type PdfTextContent = { items: Array<{ str?: string; hasEOL?: boolean }> };

export type PdfPage = {
  getViewport: (options: { scale: number }) => PdfViewport;
  getTextContent: () => Promise<PdfTextContent>;
  render: (context: {
    canvasContext: CanvasRenderingContext2D;
    viewport: PdfViewport;
    transform?: number[];
  }) => { promise: Promise<void>; cancel: () => void };
};

export type PdfDocument = {
  numPages: number;
  getPage: (pageNumber: number) => Promise<PdfPage>;
  destroy?: () => Promise<void>;
};

type TextLayerInstance = {
  render: () => Promise<unknown>;
  cancel: () => void;
  textDivs: HTMLElement[];
  textContentItemsStr: string[];
};

export type TextLayerCtor = new (options: {
  textContentSource: PdfTextContent;
  container: HTMLElement;
  viewport: PdfViewport;
}) => TextLayerInstance;

const CANCELLED_ERRORS = new Set(["RenderingCancelledException", "AbortException"]);

/** 本页要高亮的区间；current 为当前命中（滚动到它并加深颜色）。 */
export type PageHighlight = { ranges: PdfItemRange[]; current: boolean };

/** 把高亮区间包进文本项：先还原原文，再按区间切成「前 / 命中 / 后」。 */
function applyHighlights(layer: TextLayerInstance, highlights: PageHighlight[]): HTMLElement | null {
  layer.textDivs.forEach((div, index) => {
    if (div.childElementCount > 0) div.textContent = layer.textContentItemsStr[index] ?? "";
  });
  const byItem = new Map<number, Array<{ start: number; end: number; current: boolean }>>();
  highlights.forEach(({ ranges, current }) => {
    ranges.forEach(({ item, start, end }) => {
      const list = byItem.get(item) ?? [];
      list.push({ start, end, current });
      byItem.set(item, list);
    });
  });
  let currentEl: HTMLElement | null = null;
  byItem.forEach((list, item) => {
    const div = layer.textDivs[item];
    const text = layer.textContentItemsStr[item];
    if (!div || text == null) return;
    list.sort((a, b) => a.start - b.start);
    div.textContent = "";
    let cursor = 0;
    list.forEach(({ start, end, current }) => {
      if (start < cursor) return; // 重叠的命中只画第一段
      if (start > cursor) div.append(document.createTextNode(text.slice(cursor, start)));
      const mark = document.createElement("span");
      mark.className = current ? `${styles.hit} ${styles.hitCurrent}` : styles.hit;
      mark.textContent = text.slice(start, end);
      div.append(mark);
      if (current && !currentEl) currentEl = mark;
      cursor = end;
    });
    if (cursor < text.length) div.append(document.createTextNode(text.slice(cursor)));
  });
  return currentEl;
}

/**
 * 单页：canvas 画面 + 透明文字层（可选中文字、承载查找高亮）。
 * 文字层在每次缩放后重建；高亮在文字层就绪后与每次命中变化时重新套用。
 */
export function PdfPageView({
  pdf,
  pageNumber,
  scale,
  textLayerCtor,
  highlights,
}: {
  pdf: PdfDocument;
  pageNumber: number;
  scale: number;
  textLayerCtor: TextLayerCtor | null;
  highlights: PageHighlight[];
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const textRef = useRef<HTMLDivElement | null>(null);
  const layerRef = useRef<TextLayerInstance | null>(null);
  const [rendering, setRendering] = useState(true);
  const [layerVersion, setLayerVersion] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let renderTask: { promise: Promise<void>; cancel: () => void } | null = null;
    let textLayer: TextLayerInstance | null = null;

    async function renderPage() {
      setRendering(true);
      const page = await pdf.getPage(pageNumber);
      if (cancelled) return;
      // 文字先取好再开画：取文字期间被取消时还没有渲染任务，不会留下未处理的取消异常。
      const textContent = textLayerCtor ? await page.getTextContent() : null;
      if (cancelled) return;
      const viewport = page.getViewport({ scale });
      const canvas = canvasRef.current;
      const context = canvas?.getContext("2d");
      if (!canvas || !context) return;
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.floor(viewport.width * ratio);
      canvas.height = Math.floor(viewport.height * ratio);
      canvas.style.width = `${Math.floor(viewport.width)}px`;
      canvas.style.height = `${Math.floor(viewport.height)}px`;
      // pdfjs v6 会从 canvasContext 反推 canvas 并重新 getContext，预设的 context 变换会被忽略；
      // 设备像素比通过 render 的 transform 参数传入才能在高 DPR 屏正确缩放。
      renderTask = page.render({
        canvasContext: context,
        viewport,
        transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : undefined,
      });
      try {
        await renderTask.promise;
        const container = textRef.current;
        if (cancelled || !textLayerCtor || !textContent || !container) return;
        container.replaceChildren();
        container.style.setProperty("--total-scale-factor", String(viewport.scale));
        textLayer = new textLayerCtor({ textContentSource: textContent, container, viewport });
        await textLayer.render();
        if (!cancelled) {
          layerRef.current = textLayer;
          setLayerVersion((value) => value + 1);
        }
      } catch (err) {
        // 缩放 / 切换文档时的取消属正常，其余错误打印出来便于排障。
        if (err instanceof Error && !CANCELLED_ERRORS.has(err.name)) {
          console.error(`PDF page ${pageNumber} render failed:`, err);
        }
      } finally {
        if (!cancelled) setRendering(false);
      }
    }

    void renderPage();
    return () => {
      cancelled = true;
      renderTask?.cancel();
      textLayer?.cancel();
      layerRef.current = null;
    };
  }, [pdf, pageNumber, scale, textLayerCtor]);

  useEffect(() => {
    const layer = layerRef.current;
    if (!layer) return;
    const current = applyHighlights(layer, highlights);
    current?.scrollIntoView({ block: "center", inline: "nearest" });
  }, [highlights, layerVersion]);

  return (
    <>
      <canvas ref={canvasRef} style={{ display: "block" }} />
      <div ref={textRef} className={styles.textLayer} style={{ ["--scale-round-x" as string]: "1px", ["--scale-round-y" as string]: "1px" }} />
      {rendering && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            zIndex: 2,
            display: "grid",
            placeItems: "center",
            background: "rgba(255,255,255,.62)",
            fontSize: 11,
            color: "var(--fg-subtle)",
          }}
        >
          Rendering
        </div>
      )}
    </>
  );
}
