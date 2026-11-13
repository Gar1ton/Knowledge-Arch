"use client";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { IconButton } from "@/components/ds/IconButton";
import { ZoteroAnnotation } from "@/lib/api";
import { apiUrl, authHeaders } from "@/lib/backend";
import { useI18n } from "@/lib/i18n";
import type { CiteTarget } from "@/lib/ConsoleContext";
import { candidatePages, locateChunk } from "@/lib/pdfLocate";
import { buildPageText, findMatches, hasSearchableText, type PdfMatch, type PdfPageText } from "@/lib/pdfSearch";
import { Z } from "@/lib/zLayers";
import {
  PdfPageView,
  type PageCite,
  type PageHighlight,
  type PdfDocument,
  type TextLayerCtor,
} from "./PdfPage";
import { PdfSearchBar } from "./PdfSearchBar";
import styles from "./PdfPage.module.css";

interface PdfViewerProps {
  docId: string;
  annotations: ZoteroAnnotation[];
  /** 要定位的引用（属于本文档时才传）；处理完（定位到或退回跳页）后调用 onLocateHandled。 */
  locate?: CiteTarget | null;
  onLocateHandled?: () => void;
  /** 定位不到原文时，提示条里「在 MD 中查看该段」。 */
  onViewInMd?: (target: CiteTarget) => void;
}

/** 当前引用定位的结果；保留到下一次跳转或换文档（pdfUrl 变化即失效）。 */
type CiteState = {
  pdfUrl: string;
  target: CiteTarget;
  /** exact = 文字或图框定位到；page = 只跳到页并提示。 */
  status: "exact" | "page";
  matches: PdfMatch[];
  box: { page: number; bbox: number[] } | null;
  page: number;
};

type PdfDocumentSource = {
  // 文件字节由本组件带 token 取回后交给 pdf.js（桌面壳里后端与页面不同源，相对 URL 取不到）。
  data: Uint8Array;
  // pdfjs v6 把 JBIG2/JPEG2000 解码器迁到 WASM，扫描件 PDF 需要这些资源 URL 才能渲染图像。
  wasmUrl?: string;
  cMapUrl?: string;
  cMapPacked?: boolean;
  standardFontDataUrl?: string;
};

type PdfJsModule = {
  GlobalWorkerOptions: { workerSrc: string };
  TextLayer: TextLayerCtor;
  getDocument: (source: PdfDocumentSource) => { promise: Promise<PdfDocument>; destroy?: () => void };
};

type PdfLoadState = {
  pdfUrl: string;
  pdf: PdfDocument | null;
  textLayer: TextLayerCtor | null;
  pageCount: number;
  loading: boolean;
  error: string;
};

type PageState = {
  pdfUrl: string;
  page: number;
};

const MIN_SCALE = 0.5;
const MAX_SCALE = 2.4;
const SCALE_STEP = 0.15;

type SearchStatus = "idle" | "searching" | "done" | "no_text";

const NO_HIGHLIGHTS: PageHighlight[] = [];
const NO_MATCHES: PdfMatch[] = [];
const SEARCH_DEBOUNCE_MS = 200;

const PDF_MAGIC = [0x25, 0x50, 0x44, 0x46, 0x2d]; // "%PDF-"

class NotPdfError extends Error {}

/** 带 token 向后端取原件字节；不是 PDF（如存成 original.pdf 的文本副本）时抛 NotPdfError。 */
async function fetchPdfBytes(path: string): Promise<Uint8Array> {
  const res = await fetch(await apiUrl(path), { headers: await authHeaders() });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const bytes = new Uint8Array(await res.arrayBuffer());
  if (!PDF_MAGIC.every((b, i) => bytes[i] === b)) throw new NotPdfError();
  return bytes;
}

export function PdfViewer({ docId, annotations, locate, onLocateHandled, onViewInMd }: PdfViewerProps) {
  const { t } = useI18n();
  const [scale, setScale] = useState(1);
  // 全屏 = 阅读器铺满整个应用窗口（不是系统 F11 全屏）；Esc 或再点一次退出。
  const [fullscreen, setFullscreen] = useState(false);
  const pagesRef = useRef<Record<number, HTMLDivElement | null>>({});
  const viewportRef = useRef<HTMLDivElement | null>(null);

  const pdfUrl = useMemo(
    () => `/api/documents/${encodeURIComponent(docId)}/raw?disposition=inline`,
    [docId],
  );
  const [loadState, setLoadState] = useState<PdfLoadState>(() => ({
    pdfUrl,
    pdf: null,
    textLayer: null,
    pageCount: 0,
    loading: true,
    error: "",
  }));
  const [pageState, setPageState] = useState<PageState>(() => ({ pdfUrl, page: 1 }));
  const isCurrentPdf = loadState.pdfUrl === pdfUrl;
  const pdf = isCurrentPdf ? loadState.pdf : null;
  const textLayerCtor = isCurrentPdf ? loadState.textLayer : null;
  const pageCount = isCurrentPdf ? loadState.pageCount : 0;
  const loading = isCurrentPdf ? loadState.loading : true;
  const error = isCurrentPdf ? loadState.error : "";
  const currentPage = pageState.pdfUrl === pdfUrl ? pageState.page : 1;
  const setCurrentPage = useCallback<React.Dispatch<React.SetStateAction<number>>>(
    (updater) => {
      setPageState((current) => {
        const base = current.pdfUrl === pdfUrl ? current.page : 1;
        const page = typeof updater === "function" ? updater(base) : updater;
        return { pdfUrl, page };
      });
    },
    [pdfUrl],
  );

  useEffect(() => {
    let cancelled = false;
    let loadingTask: { promise: Promise<PdfDocument>; destroy?: () => void } | null = null;

    async function loadPdf() {
      try {
        const pdfjs = (await import("pdfjs-dist")) as unknown as PdfJsModule;
        pdfjs.GlobalWorkerOptions.workerSrc = new URL(
          "pdfjs-dist/build/pdf.worker.mjs",
          import.meta.url,
        ).toString();
        const data = await fetchPdfBytes(pdfUrl);
        if (cancelled) return;
        loadingTask = pdfjs.getDocument({
          data,
          wasmUrl: "/pdfjs/wasm/",
          cMapUrl: "/pdfjs/cmaps/",
          cMapPacked: true,
          standardFontDataUrl: "/pdfjs/standard_fonts/",
        });
        const doc = await loadingTask.promise;
        if (cancelled) {
          await doc.destroy?.();
          return;
        }
        setLoadState({
          pdfUrl,
          pdf: doc,
          textLayer: pdfjs.TextLayer ?? null,
          pageCount: doc.numPages,
          loading: false,
          error: "",
        });
      } catch (err) {
        if (!cancelled) {
          setLoadState({
            pdfUrl,
            pdf: null,
            textLayer: null,
            pageCount: 0,
            loading: false,
            error:
              err instanceof NotPdfError
                ? t("pdf_not_a_pdf")
                : err instanceof Error
                  ? err.message
                  : "PDF load failed",
          });
        }
      }
    }

    void loadPdf();
    return () => {
      cancelled = true;
      loadingTask?.destroy?.();
    };
  }, [pdfUrl, t]);

  const scrollToPage = useCallback((page: number) => {
    const clamped = Math.max(1, Math.min(pageCount || page, page));
    setCurrentPage(clamped);
    pagesRef.current[clamped]?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [pageCount, setCurrentPage]);

  const fitWidth = useCallback(() => {
    const viewport = viewportRef.current;
    if (!viewport || !pdf) return;
    void pdf.getPage(1).then((page) => {
      const natural = page.getViewport({ scale: 1 });
      const availableWidth = Math.max(320, viewport.clientWidth - 46);
      const nextScale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, availableWidth / natural.width));
      setScale(Number(nextScale.toFixed(2)));
    });
  }, [pdf]);

  // 载入后与切换全屏后（视口宽度变了）都重新适应宽度。
  useEffect(() => {
    if (!pdf) return;
    const frame = requestAnimationFrame(fitWidth);
    return () => cancelAnimationFrame(frame);
  }, [pdf, fitWidth, fullscreen]);

  useEffect(() => {
    if (!fullscreen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  // ── 各页文字（查找与引用定位共用，按文档缓存）───────────────
  const pageTextCacheRef = useRef<{ pdfUrl: string; pages: Map<number, PdfPageText> }>({
    pdfUrl: "",
    pages: new Map(),
  });
  const getPageTexts = useCallback(
    async (numbers: number[], isCancelled: () => boolean): Promise<PdfPageText[] | null> => {
      if (!pdf) return null;
      if (pageTextCacheRef.current.pdfUrl !== pdfUrl) {
        pageTextCacheRef.current = { pdfUrl, pages: new Map() };
      }
      const cache = pageTextCacheRef.current.pages;
      const out: PdfPageText[] = [];
      for (const n of numbers) {
        let text = cache.get(n);
        if (!text) {
          const page = await pdf.getPage(n);
          const content = await page.getTextContent();
          if (isCancelled()) return null;
          text = buildPageText(n, content.items);
          cache.set(n, text);
        }
        out.push(text);
      }
      return out;
    },
    [pdf, pdfUrl],
  );

  // ── 关键词查找 ─────────────────────────────────────────────
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<{
    pdfUrl: string;
    query: string;
    matches: PdfMatch[];
    status: SearchStatus;
    current: number;
  }>(() => ({ pdfUrl, query: "", matches: [], status: "idle", current: 0 }));
  const trimmedQuery = query.trim();
  const searchState =
    searchOpen && trimmedQuery && search.pdfUrl === pdfUrl
      ? search
      : { pdfUrl, query: "", matches: NO_MATCHES, status: "idle" as SearchStatus, current: 0 };
  const { matches: searchMatches, current: searchCurrent } = searchState;

  useEffect(() => {
    if (!searchOpen || !pdf || !trimmedQuery) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      setSearch((prev) => ({ ...prev, pdfUrl, query: trimmedQuery, status: "searching" }));
      try {
        // 各页文字只取一次：同一文档的后续查找直接复用。
        const all = Array.from({ length: pdf.numPages }, (_, i) => i + 1);
        const pages = await getPageTexts(all, () => cancelled);
        if (cancelled || !pages) return;
        const status: SearchStatus = hasSearchableText(pages) ? "done" : "no_text";
        const matches = status === "done" ? findMatches(pages, trimmedQuery) : [];
        setSearch({ pdfUrl, query: trimmedQuery, matches, status, current: 0 });
        if (matches.length > 0) setCurrentPage(matches[0].page);
      } catch (err) {
        console.error("PDF search failed:", err);
        if (!cancelled) setSearch({ pdfUrl, query: trimmedQuery, matches: [], status: "done", current: 0 });
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [searchOpen, pdf, pdfUrl, trimmedQuery, setCurrentPage, getPageTexts]);

  // ── 引用定位 ───────────────────────────────────────────────
  const [citeState, setCiteState] = useState<CiteState | null>(null);
  const [citeNoticeHidden, setCiteNoticeHidden] = useState<number | null>(null);
  const cite = citeState && citeState.pdfUrl === pdfUrl ? citeState : null;
  const locateNonce = locate?.nonce;
  const locateRef = useRef(locate);
  const handledRef = useRef(onLocateHandled);
  useEffect(() => {
    locateRef.current = locate;
    handledRef.current = onLocateHandled;
  });

  useEffect(() => {
    const target = locateRef.current;
    if (locateNonce == null || !target || !pdf) return;
    let cancelled = false;
    void (async () => {
      const firstPage = Math.min(pdf.numPages, Math.max(1, target.pages?.[0] ?? 1));
      let next: CiteState;
      if (target.kind === "figure" && target.bbox && target.bbox.length === 4) {
        next = { pdfUrl, target, status: "exact", matches: [], box: { page: firstPage, bbox: target.bbox }, page: firstPage };
      } else {
        const numbers = candidatePages(target.pages, pdf.numPages);
        let matches: PdfMatch[] | null = null;
        try {
          const texts = await getPageTexts(numbers, () => cancelled);
          if (cancelled) return;
          matches = texts ? locateChunk(texts, target.text ?? "") : null;
        } catch (err) {
          console.error("PDF citation locate failed:", err);
        }
        if (cancelled) return;
        next = matches
          ? { pdfUrl, target, status: "exact", matches, box: null, page: matches[0].page }
          : { pdfUrl, target, status: "page", matches: [], box: null, page: firstPage };
      }
      setCiteState(next);
      setCurrentPage(next.page);
      // 定位不到时没有可滚动的高亮，直接滚到页并整页闪一下。
      if (next.status === "page") pagesRef.current[next.page]?.scrollIntoView({ block: "start", behavior: "smooth" });
      handledRef.current?.();
    })();
    return () => {
      cancelled = true;
    };
  }, [locateNonce, pdf, pdfUrl, getPageTexts, setCurrentPage]);

  const stepMatch = (delta: number) => {
    const total = searchState.matches.length;
    if (!total) return;
    const next = (searchState.current + delta + total) % total;
    setSearch({ ...searchState, current: next });
    setCurrentPage(searchState.matches[next].page);
  };

  const closeSearch = useCallback(() => {
    setSearchOpen(false);
    setQuery("");
  }, []);

  // Ctrl/Cmd+F：鼠标在阅读器上或焦点在阅读器内时打开查找，不抢别处（如聊天框）的快捷键。
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "f") return;
      const root = rootRef.current;
      if (!root || !(root.matches(":hover") || root.contains(document.activeElement))) return;
      e.preventDefault();
      setSearchOpen(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const citeMatches = cite?.matches;
  const highlightsByPage = useMemo(() => {
    const byPage = new Map<number, PageHighlight[]>();
    (citeMatches ?? []).forEach((match) => {
      const list = byPage.get(match.page) ?? [];
      list.push({ ranges: match.ranges, current: false, cite: true });
      byPage.set(match.page, list);
    });
    searchMatches.forEach((match, index) => {
      const list = byPage.get(match.page) ?? [];
      list.push({ ranges: match.ranges, current: index === searchCurrent });
      byPage.set(match.page, list);
    });
    return byPage;
  }, [citeMatches, searchMatches, searchCurrent]);

  // 只有引用的起始页带滚动令牌；图框画在图所在页。
  const pageCite = useMemo<PageCite | null>(() => {
    if (!cite || cite.status !== "exact") return null;
    return { token: cite.target.nonce, box: cite.box?.bbox ?? null, scroll: true };
  }, [cite]);
  const showCiteNotice = cite?.status === "page" && citeNoticeHidden !== cite.target.nonce;

  const annotatedPages = useMemo(() => {
    const pages = new Set<number>();
    annotations.forEach((ann) => {
      const page = annotationPage(ann);
      if (page != null) pages.add(page);
    });
    return pages;
  }, [annotations]);

  const viewer = (
    <div
      ref={rootRef}
      style={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-lg)",
        overflow: "hidden",
        background: "var(--surface)",
        ...(fullscreen ? { position: "fixed", inset: 0, zIndex: Z.dialog, borderRadius: 0 } : {}),
      }}
    >
      <div
        style={{
          height: 38,
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "0 10px",
          borderBottom: "1px solid var(--border)",
          background: "var(--bg-inset)",
        }}
      >
        {/* 标题已在面板面包屑里，这里不再重复，只留阅读操作。 */}
        <span style={{ flex: 1 }} />
        <IconButton
          name="chevL"
          label={t("pdf_prev_page")}
          onClick={() => scrollToPage(currentPage - 1)}
          style={{ opacity: currentPage <= 1 ? 0.45 : 1 }}
        />
        <span style={{ fontSize: 11, color: "var(--fg-muted)", fontFamily: "var(--font-mono)" }}>
          {currentPage}/{pageCount || "-"}
        </span>
        <IconButton
          name="chevR"
          label={t("pdf_next_page")}
          onClick={() => scrollToPage(currentPage + 1)}
          style={{ opacity: pageCount && currentPage >= pageCount ? 0.45 : 1 }}
        />
        <IconButton
          name="zoomOut"
          label={t("pdf_zoom_out")}
          onClick={() => setScale((value) => Math.max(MIN_SCALE, Number((value - SCALE_STEP).toFixed(2))))}
        />
        <span style={{ minWidth: 38, textAlign: "center", fontSize: 11, color: "var(--fg-muted)" }}>
          {Math.round(scale * 100)}%
        </span>
        <IconButton
          name="zoomIn"
          label={t("pdf_zoom_in")}
          onClick={() => setScale((value) => Math.min(MAX_SCALE, Number((value + SCALE_STEP).toFixed(2))))}
        />
        <IconButton name="fitWidth" label={t("pdf_fit_width")} onClick={fitWidth} />
        <IconButton
          name="search"
          label={t("pdf_search")}
          active={searchOpen}
          onClick={() => (searchOpen ? closeSearch() : setSearchOpen(true))}
        />
        <IconButton
          name={fullscreen ? "minimize" : "maximize"}
          label={fullscreen ? t("pdf_exit_fullscreen") : t("pdf_fullscreen")}
          active={fullscreen}
          onClick={() => setFullscreen((value) => !value)}
        />
      </div>

      {showCiteNotice && cite && (
        <div
          role="status"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "6px 10px",
            borderBottom: "1px solid var(--border)",
            background: "var(--accent-soft)",
            fontSize: 11.5,
            color: "var(--fg)",
          }}
        >
          <span style={{ flex: 1, minWidth: 0 }}>{t("pdf_cite_page_only", { page: cite.page })}</span>
          {onViewInMd && (
            <button
              type="button"
              onClick={() => onViewInMd(cite.target)}
              style={{
                fontSize: 11,
                fontWeight: 600,
                padding: "3px 9px",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--accent-border)",
                background: "var(--surface)",
                color: "var(--accent)",
                cursor: "pointer",
              }}
            >
              {t("pdf_cite_view_in_md")}
            </button>
          )}
          <IconButton name="x" label={t("pdf_cite_dismiss")} onClick={() => setCiteNoticeHidden(cite.target.nonce)} />
        </div>
      )}

      {searchOpen && (
        <PdfSearchBar
          query={query}
          onQueryChange={setQuery}
          total={searchState.matches.length}
          current={searchState.current}
          status={trimmedQuery && searchState.status === "idle" ? "searching" : searchState.status}
          onPrev={() => stepMatch(-1)}
          onNext={() => stepMatch(1)}
          onClose={closeSearch}
        />
      )}

      <div style={{ display: "flex", flex: 1, minHeight: 0, overflow: "hidden" }}>
        <div
          ref={viewportRef}
          onScroll={() => {
            const entries = Object.entries(pagesRef.current);
            const top = viewportRef.current?.getBoundingClientRect().top ?? 0;
            let closest = currentPage;
            let distance = Number.POSITIVE_INFINITY;
            entries.forEach(([page, el]) => {
              if (!el) return;
              const nextDistance = Math.abs(el.getBoundingClientRect().top - top);
              if (nextDistance < distance) {
                distance = nextDistance;
                closest = Number(page);
              }
            });
            if (closest !== currentPage) setCurrentPage(closest);
          }}
          style={{
            flex: 1,
            minHeight: 0,
            overflow: "auto",
            background: "var(--bg)",
            padding: "10px 14px",
          }}
        >
          {loading && <PdfState label="Loading PDF" />}
          {error && <PdfState label={error} tone="error" />}
          {!loading && !error && pdf && (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 16 }}>
              {Array.from({ length: pageCount }, (_, index) => {
                const pageNumber = index + 1;
                const flash = cite?.status === "page" && cite.page === pageNumber;
                return (
                  <div
                    key={pageNumber}
                    ref={(el) => { pagesRef.current[pageNumber] = el; }}
                    style={{
                      position: "relative",
                      border: annotatedPages.has(pageNumber)
                        ? "2px solid var(--accent)"
                        : "1px solid var(--border)",
                      boxShadow: "var(--shadow-card)",
                      background: "white",
                    }}
                  >
                    <PdfPageView
                      pdf={pdf}
                      pageNumber={pageNumber}
                      scale={scale}
                      textLayerCtor={textLayerCtor}
                      highlights={highlightsByPage.get(pageNumber) ?? NO_HIGHLIGHTS}
                      cite={pageCite && cite?.page === pageNumber ? pageCite : null}
                    />
                    {flash && <div key={cite.target.nonce} className={styles.pageFlash} />}
                  </div>
                );
              })}
            </div>
          )}
        </div>
        {annotations.length > 0 && (
          <aside
            style={{
              width: 260,
              flexShrink: 0,
              borderLeft: "1px solid var(--border)",
              background: "var(--surface)",
              overflow: "auto",
              padding: 12,
            }}
          >
            <div style={{ fontSize: 11, fontWeight: 700, color: "var(--fg-muted)", marginBottom: 9 }}>
              Annotations · {annotations.length}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {annotations.map((ann) => {
                const page = annotationPage(ann);
                return (
                  <button
                    key={ann.id}
                    type="button"
                    onClick={() => page != null && scrollToPage(page)}
                    style={{
                      width: "100%",
                      textAlign: "left",
                      border: "1px solid var(--border)",
                      borderLeft: `3px solid ${ann.color || "var(--accent)"}`,
                      borderRadius: "var(--radius-sm)",
                      background: "var(--bg-inset)",
                      padding: "8px 9px",
                      cursor: page != null ? "pointer" : "default",
                      fontFamily: "var(--font-sans)",
                    }}
                  >
                    <div style={{ fontSize: 10.5, color: "var(--fg-subtle)", marginBottom: 4 }}>
                      {page != null ? `p.${page}` : "PDF"} · {ann.type || "annotation"}
                    </div>
                    <div style={{ fontSize: 11.5, lineHeight: 1.45, color: "var(--fg)" }}>
                      {ann.text || ann.comment || "Annotation"}
                    </div>
                  </button>
                );
              })}
            </div>
          </aside>
        )}
      </div>
    </div>
  );
  // 全屏时挂到 body，避免被面板的 overflow / transform 困住。
  return fullscreen && typeof document !== "undefined" ? createPortal(viewer, document.body) : viewer;
}

function PdfState({ label, tone = "neutral" }: { label: string; tone?: "neutral" | "error" }) {
  return (
    <div
      style={{
        minHeight: 260,
        display: "grid",
        placeItems: "center",
        color: tone === "error" ? "var(--danger)" : "var(--fg-subtle)",
        fontSize: 12,
      }}
    >
      {label}
    </div>
  );
}

function annotationPage(annotation: ZoteroAnnotation): number | null {
  if (typeof annotation.page === "number") return annotation.page;
  if (typeof annotation.position?.pageIndex === "number") return annotation.position.pageIndex + 1;
  return null;
}
