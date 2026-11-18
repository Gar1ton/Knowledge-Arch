"use client";
import React, { useEffect, useRef, useState } from "react";
import { Panel } from "@/components/ds/Panel";
import { Badge } from "@/components/ds/Badge";
import { Eyebrow } from "@/components/ds/Eyebrow";
import { IconButton } from "@/components/ds/IconButton";
import { Icon } from "@/components/ds/Icon";
import { useConsole, type CiteTarget } from "@/lib/ConsoleContext";
import {
  ApiError,
  getDocument,
  getDocumentAnnotations,
  KrDocument,
  listDocumentChunks,
  reextractDocument,
  ZoteroAnnotation,
} from "@/lib/api";
import { useToast } from "@/components/ui/Toast";
import { PdfViewer } from "@/components/panels/PdfViewer";
import { DocList, DocListToolbar, DocStepper } from "@/components/panels/DocList";
import { scopeName, useCollectionDocs } from "@/lib/useCollectionDocs";
import { useI18n } from "@/lib/i18n";
import { parseChunkText, ChunkTextPart } from "@/lib/chunkText";
import { findChunkByText } from "@/lib/pdfLocate";

type ReadingDataState = {
  docId: string;
  chunks: ChunkItem[];
  annotations: ZoteroAnnotation[];
  loading: boolean;
};


/** 选中的文档不在当前列表里时（引用跳到别的集合、恢复上次打开的文档）单独取回。 */
type FetchedDocState = {
  docId: string;
  doc: KrDocument | null;
};

// ─── ReadingView ──────────────────────────────────────────────

interface ChunkItem {
  chunk_id: string;
  ordinal: number;
  page?: number;
  text: string;
}

const NO_CHUNKS: ChunkItem[] = [];
const NO_ANNOTATIONS: ZoteroAnnotation[] = [];

function ChunkHeading({ part }: { part: Extract<ChunkTextPart, { type: "heading" }> }) {
  const isThesis = part.kind === "thesis";
  return (
    <div
      style={{
        display: "flex",
        alignItems: "baseline",
        gap: 8,
        flexWrap: "wrap",
        margin: "2px 0 7px",
      }}
    >
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          minHeight: 20,
          padding: isThesis ? "2px 8px" : "1px 7px",
          borderRadius: "var(--radius-sm)",
          border: "1px solid var(--accent-border)",
          background: "var(--accent-soft)",
          color: "var(--accent)",
          fontFamily: "var(--font-mono)",
          fontSize: isThesis ? 12 : 10.5,
          fontWeight: 800,
          lineHeight: 1,
        }}
      >
        {part.label}
      </span>
      {part.title && (
        <span
          style={{
            color: "var(--heading)",
            fontSize: part.kind === "section" ? 14 : 13.5,
            fontWeight: part.kind === "subsection" ? 650 : 700,
            lineHeight: 1.35,
          }}
        >
          {part.title}
        </span>
      )}
    </div>
  );
}

function ChunkText({ text }: { text: string }) {
  const parts = parseChunkText(text);
  if (parts.length === 0) return null;

  return (
    <div style={{ fontSize: 13, lineHeight: 1.7, color: "var(--fg)" }}>
      {parts.map((part, index) => {
        if (part.type === "heading") {
          return (
            <div key={`${part.type}-${index}`} style={{ marginTop: index === 0 ? 0 : 12 }}>
              <ChunkHeading part={part} />
            </div>
          );
        }

        return (
          <p
            key={`${part.type}-${index}`}
            style={{
              margin: index === 0 ? 0 : "7px 0 0",
              color: "var(--fg)",
              whiteSpace: "pre-wrap",
            }}
          >
            {part.text}
          </p>
        );
      })}
    </div>
  );
}

function ReadingView({
  doc,
  mode,
  onViewInMd,
}: {
  doc: KrDocument;
  mode: "md" | "pdf";
  onViewInMd: (target: CiteTarget) => void;
}) {
  const { highlightedChunk, setHighlightedChunk } = useConsole();
  const { t } = useI18n();
  const { toast } = useToast();
  const chunkRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [readingData, setReadingData] = useState<ReadingDataState>(() => ({
    docId: doc.doc_id,
    chunks: [],
    annotations: [],
    loading: true,
  }));
  const [reextracting, setReextracting] = useState(false);
  const isReadingDataCurrent = readingData.docId === doc.doc_id;
  const chunks = isReadingDataCurrent ? readingData.chunks : NO_CHUNKS;
  const annotations = isReadingDataCurrent ? readingData.annotations : NO_ANNOTATIONS;
  const chunkLoading = isReadingDataCurrent ? readingData.loading : true;
  const meta = doc.zotero_meta;

  async function handleReextract() {
    setReextracting(true);
    try {
      const result = await reextractDocument(doc.doc_id);
      toast(t("documents_reextract_done", { n: result.chunk_count }), "ok");
    } catch (err) {
      toast(err instanceof Error ? err.message : t("documents_reextract_failed"), "error");
    } finally {
      setReextracting(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([
      listDocumentChunks(doc.doc_id),
      getDocumentAnnotations(doc.doc_id),
    ]).then(([chunkResult, annotationResult]) => {
      if (cancelled) return;
      setReadingData({
        docId: doc.doc_id,
        chunks: chunkResult.status === "fulfilled" ? chunkResult.value : [],
        annotations: annotationResult.status === "fulfilled" ? annotationResult.value : [],
        loading: false,
      });
    });
    return () => {
      cancelled = true;
    };
  }, [doc.doc_id]);

  // 分块界面的引用定位：等分块加载完再找（跨文档跳转时先渲染的是空列表）；
  // chunk_id 因重切换了修订号时按来源文字回找。找不到也要清掉，否则状态一直挂着。
  useEffect(() => {
    if (mode !== "md" || !highlightedChunk || highlightedChunk.docId !== doc.doc_id || chunkLoading) return;
    let targetId = highlightedChunk.chunkId;
    if (!chunkRefs.current[targetId] && highlightedChunk.text) {
      const index = findChunkByText(chunks.map((c) => c.text), highlightedChunk.text);
      if (index >= 0) targetId = chunks[index].chunk_id;
    }
    const el = chunkRefs.current[targetId];
    if (el) {
      el.scrollIntoView({ block: "center" });
      el.style.animation = "none";
      void el.offsetWidth; // force reflow
      el.style.animation = "citeFlash 1.6s ease-out forwards";
    }
    const timer = setTimeout(() => setHighlightedChunk(null), el ? 1800 : 0);
    return () => clearTimeout(timer);
  }, [mode, highlightedChunk, doc.doc_id, chunkLoading, chunks, setHighlightedChunk]);

  const docHeader = (
    <>
      <h1
        style={{
          fontSize: 21,
          fontWeight: 700,
          color: "var(--heading)",
          letterSpacing: "-.02em",
          lineHeight: 1.3,
          margin: "8px 0 10px",
        }}
      >
        {doc.title ?? doc.filename}
      </h1>
      {meta?.creators && meta.creators.length > 0 && (
        <div style={{ fontSize: 12.5, color: "var(--fg-muted)", marginBottom: 12 }}>
          {meta.creators.join(", ")}
        </div>
      )}
      <div
        style={{
          display: "flex",
          gap: 7,
          flexWrap: "wrap",
          marginBottom: 18,
          paddingBottom: 18,
          borderBottom: "1px solid var(--border)",
        }}
      >
        {meta?.venue && <Badge tone="neutral">{meta.venue}</Badge>}
        {meta?.year && <Badge tone="neutral">{meta.year}</Badge>}
        {meta?.item_type && <Badge tone="neutral">{meta.item_type}</Badge>}
        {meta?.doi && <Badge tone="accent">DOI {meta.doi}</Badge>}
        {doc.lightrag_index_status?.status === "built" && (
          <Badge tone="violet">
            <Icon name="graph" size={10} /> {t("documents_graph_built")}
          </Badge>
        )}
        {doc.ext?.toLowerCase() === "pdf" && !doc.read_only && (
          <button
            type="button"
            disabled={reextracting}
            onClick={handleReextract}
            style={{
              fontSize: 10.5,
              fontWeight: 600,
              padding: "2px 8px",
              borderRadius: "var(--radius-sm)",
              border: "1px solid var(--border)",
              background: "transparent",
              color: reextracting ? "var(--fg-subtle)" : "var(--fg-muted)",
              cursor: reextracting ? "not-allowed" : "pointer",
              letterSpacing: ".03em",
            }}
          >
            {reextracting ? t("documents_reextracting") : t("documents_reextract")}
          </button>
        )}
      </div>
    </>
  );

  // PDF 模式：标题已在面包屑，不再显示文档头（作者、徽章、重新抽取都在 MD 模式），阅读区占满面板。
  if (mode === "pdf") {
    const locate = highlightedChunk && highlightedChunk.docId === doc.doc_id ? highlightedChunk : null;
    return (
      <div style={{ flex: 1, minHeight: 0, padding: 4, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <PdfViewer
          docId={doc.doc_id}
          annotations={annotations}
          locate={locate}
          onLocateHandled={() => setHighlightedChunk(null)}
          onViewInMd={onViewInMd}
        />
      </div>
    );
  }

  return (
    <div style={{ maxWidth: 720, margin: "0 auto", padding: "6px 12px 60px" }}>
      {docHeader}
      {meta?.abstract && (
        <>
          <Eyebrow style={{ marginBottom: 8 }}>{t("documents_abstract")}</Eyebrow>
          <p style={{ fontSize: 13.5, lineHeight: 1.75, color: "var(--fg)", margin: "0 0 24px" }}>
            {meta.abstract}
          </p>
        </>
      )}
      <Eyebrow style={{ marginBottom: 8 }}>
        {t("documents_source_chunks", { n: chunks.length })}
      </Eyebrow>
      {chunkLoading ? (
        <div style={{ fontSize: 12.5, color: "var(--fg-subtle)", padding: "16px 0" }}>
          {t("documents_detail_loading")}
        </div>
      ) : chunks.length === 0 ? (
        <div style={{ fontSize: 12.5, color: "var(--fg-subtle)", padding: "16px 0" }}>
          {t("documents_no_chunks")}
        </div>
      ) : (
        chunks.map((c) => (
          <div
            key={c.chunk_id}
            ref={(el) => { chunkRefs.current[c.chunk_id] = el; }}
            style={{
              padding: "11px 13px",
              borderRadius: "var(--radius-md)",
              marginBottom: 8,
              border: "1px solid var(--border)",
              background: "var(--surface)",
              transition: "background-color .2s",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5 }}>
              <span
                style={{ fontSize: 10, fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--accent)" }}
              >
                #{c.ordinal}
              </span>
              {c.page != null && (
                <span style={{ fontSize: 10, color: "var(--fg-subtle)" }}>
                  {t("documents_page_label", { page: c.page })} · {c.chunk_id}
                </span>
              )}
            </div>
            <ChunkText text={c.text} />
          </div>
        ))
      )}
    </div>
  );
}

// ─── DocumentsPanel ───────────────────────────────────────────

export function DocumentsPanel() {
  const { t } = useI18n();
  const {
    selectedCollection,
    selectedDocId,
    setSelectedDocId,
    highlightedChunk,
    setHighlightedChunk,
    jumpToCitation,
    setNoteDocId,
    readingMode,
    setReadingMode,
  } = useConsole();

  // 选中集合（含全部子集合）的论文：与对话范围同一批；阅读时左栏共用这一份。
  const model = useCollectionDocs(selectedCollection);
  const colName = scopeName(selectedCollection);
  const { docs, loading } = model;
  const [fetched, setFetched] = useState<FetchedDocState | null>(null);
  // 「在 MD 中查看该段」只对那一篇、那一次生效：记下文档，下一次普通跳转或切模式即取消。
  const [mdOverrideDocId, setMdOverrideDocId] = useState<string | null>(null);
  const [seenCiteNonce, setSeenCiteNonce] = useState<number | null>(null);
  if (highlightedChunk && highlightedChunk.nonce !== seenCiteNonce) {
    setSeenCiteNonce(highlightedChunk.nonce);
    setMdOverrideDocId(highlightedChunk.preferMd ? highlightedChunk.docId : null);
  }
  const listedDoc = selectedDocId ? docs.find((d) => d.doc_id === selectedDocId) ?? null : null;
  const fetchedDoc = fetched && fetched.docId === selectedDocId ? fetched.doc : null;
  const activeDoc = listedDoc ?? fetchedDoc;
  const activeDocId = activeDoc?.doc_id ?? "";
  // 最近读过的一篇：返回列表时停在它那里（集合换了就作废）。
  const [lastRead, setLastRead] = useState<{ scope: string | null; docId: string } | null>(null);
  if (activeDocId && (lastRead?.docId !== activeDocId || lastRead.scope !== selectedCollection)) {
    setLastRead({ scope: selectedCollection, docId: activeDocId });
  }
  const revealDocId = lastRead && lastRead.scope === selectedCollection ? lastRead.docId : null;
  // 只有 PDF 原件才有 PDF 阅读模式（文本原件在库里也叫 original.pdf，后端 ext 已按真实类型给出）。
  const canReadPdf = activeDoc?.ext?.toLowerCase() === "pdf";
  const mode = canReadPdf && readingMode === "pdf" && mdOverrideDocId !== activeDocId ? "pdf" : "md";

  // 选中的文档不在当前集合列表里：单独取回再打开；已被删除（404）则回到列表。
  useEffect(() => {
    if (!selectedDocId || loading || listedDoc || fetched?.docId === selectedDocId) return;
    let cancelled = false;
    getDocument(selectedDocId)
      .then((doc) => {
        if (!cancelled) setFetched({ docId: selectedDocId, doc });
      })
      .catch((err) => {
        if (cancelled) return;
        setFetched({ docId: selectedDocId, doc: null });
        if (err instanceof ApiError && err.status === 404) setSelectedDocId(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedDocId, loading, listedDoc, fetched, setSelectedDocId]);

  function openDoc(doc: KrDocument) {
    setSelectedDocId(doc.doc_id);
    setMdOverrideDocId(null);
  }

  function viewInMd(target: CiteTarget) {
    const { nonce, ...rest } = target;
    void nonce;
    jumpToCitation({ ...rest, preferMd: true });
  }

  function backToList() {
    setSelectedDocId(null);
    setHighlightedChunk(null);
  }

  function switchReadingMode(nextMode: "md" | "pdf") {
    setReadingMode(nextMode);
    setMdOverrideDocId(null);
  }

  const isReading = !!activeDoc;
  const title = colName ?? t("docs_all");
  const countLabel = loading || model.failed
    ? null
    : model.grouped
      ? t("doclist_count_with_sub", { n: docs.length })
      : t("documents_count", { n: docs.length });

  const crumbs = isReading
    ? [
        { label: t("panel_documents"), onClick: backToList },
        { label: colName ?? activeDoc!.collection, onClick: backToList, separator: "  " },
        { label: activeDoc!.title ?? activeDoc!.filename ?? "" },
      ]
    : [{ label: title }];

  const modeSwitch = canReadPdf ? (
    <div
      style={{
        display: "flex",
        gap: 2,
        background: "var(--bg-inset)",
        borderRadius: "var(--radius-md)",
        padding: 2,
      }}
    >
      {(["md", "pdf"] as const).map((m) => (
        <button
          key={m}
          onClick={() => switchReadingMode(m)}
          style={{
            fontSize: 11,
            fontWeight: 600,
            padding: "4px 10px",
            borderRadius: "var(--radius-sm)",
            border: "none",
            cursor: "pointer",
            fontFamily: "var(--font-sans)",
            textTransform: "uppercase",
            letterSpacing: ".04em",
            background: mode === m ? "var(--surface)" : "transparent",
            color: mode === m ? "var(--accent)" : "var(--fg-muted)",
            boxShadow: mode === m ? "var(--shadow-card)" : "none",
          }}
        >
          {m}
        </button>
      ))}
    </div>
  ) : null;

  const right = isReading ? (
    <>
      <DocStepper model={model} docId={activeDocId} onGo={openDoc} />
      {modeSwitch}
    </>
  ) : (
    <>
      {countLabel && (
        <span style={{ fontSize: 11.5, color: "var(--fg-muted)", marginRight: 4, whiteSpace: "nowrap" }}>
          {countLabel}
        </span>
      )}
      <IconButton name="sync" label={t("documents_refresh")} onClick={model.reload} />
    </>
  );

  return (
    <Panel
      title={isReading ? undefined : t("panel_documents")}
      crumbs={crumbs}
      right={right}
      flush
      style={{ flex: 1, minWidth: 0 }}
      bodyStyle={
        isReading && mode === "pdf"
          ? { padding: 0, overflow: "hidden", display: "flex", flexDirection: "column" }
          : isReading
            ? { padding: "14px 0" }
            : { padding: 0 }
      }
    >
      {isReading ? (
        <ReadingView doc={activeDoc!} mode={mode} onViewInMd={viewInMd} />
      ) : (
        <>
          {!loading && !model.failed && docs.length > 0 && <DocListToolbar model={model} />}
          <DocList
            model={model}
            density="two-line"
            activeDocId={selectedDocId}
            revealDocId={revealDocId}
            onOpen={openDoc}
            onNote={(docId) => setNoteDocId(docId)}
          />
        </>
      )}
    </Panel>
  );
}
