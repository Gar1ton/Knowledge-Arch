"use client";
/**
 * 3.0.0 回答的「来源」栏：一次联网补充登记的外部来源与全部工具调用。打开时占据工作台左栏 + 中栏
 * （对话栏不动）；数据来自调用账本（`lib/externalApi.ts`）。
 *
 * 结构（P35 重构）：顶部一行问题与概况；三种视图（`SourcesViews.tsx`：按来源 / 按检索过程 / 按我的集合）；
 * 右侧详情（`SourceDetail.tsx`）；两栏之间可拖动（宽度记在本机）。「按我的集合」第一次打开时才调用一次
 * 模型归类（后端存进账本，之后不再调用）。
 */
import React, { useEffect, useRef, useState } from "react";
import { Panel } from "@/components/ds/Panel";
import { Button } from "@/components/ds/Button";
import { IconButton } from "@/components/ds/IconButton";
import { ResizeHandle, useResizableWidth } from "@/components/ds/ResizeHandle";
import { DirPickerDialog } from "@/components/flow/DirPickerDialog";
import { ImportDialog } from "@/components/panels/ImportDialog";
import { CallDetail, SourceDetail } from "@/components/panels/SourceDetail";
import { CallList, CITE_TOOL, CollectionView, SourceList } from "@/components/panels/SourcesViews";
import { useToast } from "@/components/ui/Toast";
import { useConsole } from "@/lib/ConsoleContext";
import { listCollections } from "@/lib/api";
import { inDesktopShell, pickFolder } from "@/lib/backend";
import {
  exportExternalCapture,
  getExternalCapture,
  placeExternalCapture,
  type ExternalCapture,
  type ExternalSourceDetail,
} from "@/lib/externalApi";
import { useI18n, type I18nKey } from "@/lib/i18n";
import { collectionTree, type CollectionNode } from "@/lib/sourceBlocks";

type View = "sources" | "calls" | "collections";
type Sel = { kind: "source" | "call"; id: string } | null;

const VIEWS: View[] = ["sources", "calls", "collections"];
const LIST_WIDTH_KEY = "kr-src-list-w";

export function SourcesPanel() {
  const { sourcePanel, setSourcePanel } = useConsole();
  const { t } = useI18n();
  const { toast } = useToast();
  const [loaded, setLoaded] = useState<{ id: string; data?: ExternalCapture; error?: string } | null>(null);
  const [view, setView] = useState<View>("sources");
  const [sel, setSel] = useState<Sel>(null);
  const [placing, setPlacing] = useState(false);
  const [nodes, setNodes] = useState<CollectionNode[] | null>(null);
  const [picker, setPicker] = useState(false);
  const [importing, setImporting] = useState<ExternalSourceDetail | null>(null);
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const list = useResizableWidth(LIST_WIDTH_KEY, 460, 280);
  const captureId = sourcePanel?.captureId ?? "";

  // 组件按 captureId 挂载（layout 里 key={captureId}），换一次联网补充时状态自然重置。
  useEffect(() => {
    if (!captureId) return;
    let alive = true;
    getExternalCapture(captureId)
      .then((d) => {
        if (!alive) return;
        setLoaded({ id: captureId, data: d });
        const first = sourcePanel?.focus ?? d.sources.find((s) => s.cited)?.source_id ?? d.sources[0]?.source_id;
        setSel(first ? { kind: "source", id: first } : null);
      })
      .catch((e) => {
        if (alive) setLoaded({ id: captureId, error: e instanceof Error ? e.message : String(e) });
      });
    return () => { alive = false; };
  }, [captureId, sourcePanel?.focus]);
  const data = loaded?.id === captureId ? loaded.data ?? null : null;
  const error = loaded?.id === captureId ? loaded.error ?? "" : "";

  const openCollections = async () => {
    setView("collections");
    if (!nodes) {
      listCollections()
        .then((cs) => setNodes(collectionTree(cs.filter((c) => !c.is_system))))
        .catch(() => setNodes([]));
    }
    if (!data || placing) return;
    if (!data.sources.some((s) => !s.placement?.path && !s.placement?.new_direction)) return;
    setPlacing(true);
    try {
      setLoaded({ id: captureId, data: await placeExternalCapture(captureId) });
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setPlacing(false);
    }
  };
  const pickView = (v: View) => {
    if (v === "collections") { void openCollections(); return; }
    setView(v);
    if (v === "calls" && sel?.kind !== "call" && data) {
      const s = data.sources.find((x) => x.source_id === sel?.id);
      const call = s?.call_id ?? data.calls[0]?.call_id;
      if (call) setSel({ kind: "call", id: call });
    }
  };
  const selectSource = (id: string) => {
    setSel({ kind: "source", id });
    if (view !== "sources") setView("sources");
  };

  const doExport = async (folder: string) => {
    try {
      const res = await exportExternalCapture(captureId, folder);
      toast(t("src_export_done", { path: res.files[0] ?? folder }), "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    }
  };
  const startExport = async () => {
    if (inDesktopShell()) {
      const folder = await pickFolder(t("src_export"));
      if (folder) await doExport(folder);
    } else {
      setPicker(true);
    }
  };

  const selSource = sel?.kind === "source" ? data?.sources.find((s) => s.source_id === sel.id) ?? null : null;
  const selCall = sel?.kind === "call" ? data?.calls.find((c) => c.call_id === sel.id) ?? null : null;

  const right = (
    <>
      {data && <Button size="sm" variant="outline" onClick={() => void startExport()}>{t("src_export")}</Button>}
      <IconButton name="x" label={t("src_close")} onClick={() => setSourcePanel(null)} />
    </>
  );

  return (
    <Panel
      title={t("src_panel_title_plain")}
      right={right}
      flush
      style={{ flex: 1, minWidth: 0 }}
      bodyStyle={{ padding: 0, display: "flex", flexDirection: "column", minHeight: 0 }}
    >
      {error && <div className="src-note src-note--error">{t("src_error")}: {error}</div>}
      {!data && !error && <div className="src-note">{t("src_loading")}</div>}
      {data && (
        <>
          <div className="src-head">
            <span className="src-question" title={data.capture.question}>{data.capture.question}</span>
            <span className="src-summary">
              {new Date(data.capture.captured_at).toLocaleString()}
              {" · "}
              {t("src_summary", {
                sources: data.sources.length,
                cited: data.sources.filter((s) => s.cited).length,
                calls: data.calls.filter((c) => c.tool !== CITE_TOOL).length,
              })}
            </span>
          </div>
          <div className="src-tabs" role="tablist">
            {VIEWS.map((v) => (
              <button key={v} type="button" role="tab" aria-selected={view === v}
                className={`src-tab ${view === v ? "is-on" : ""}`} onClick={() => pickView(v)}>
                {t(`src_view_${v}` as I18nKey)}
              </button>
            ))}
          </div>
          <div className="src-body" ref={bodyRef}>
            <div className="src-list" style={{ width: list.width }}>
              {view === "sources" && (
                <SourceList data={data} t={t} selected={sel?.kind === "source" ? sel.id : null} onSelect={selectSource} />
              )}
              {view === "calls" && (
                <CallList data={data} t={t} selected={sel?.kind === "call" ? sel.id : null}
                  onSelect={(id) => setSel({ kind: "call", id })} onSelectSource={selectSource} />
              )}
              {view === "collections" && (
                <CollectionView data={data} nodes={nodes} placing={placing} t={t} onSelectSource={selectSource} />
              )}
            </div>
            <ResizeHandle containerRef={bodyRef} width={list.width} onResize={list.commit}
              onEnd={list.save} onReset={list.reset} label={t("src_resize")} />
            <div className="src-detail">
              {selSource && (
                <SourceDetail s={selSource} t={t} onImport={() => setImporting(selSource)}
                  call={data.calls.find((c) => c.call_id === selSource.call_id)} />
              )}
              {selCall && <CallDetail key={selCall.call_id} c={selCall} t={t} />}
              {!selSource && !selCall && <div className="src-note">{t("src_detail_empty")}</div>}
            </div>
          </div>
        </>
      )}
      {importing && (
        <ImportDialog captureId={captureId} source={importing}
          onClose={(changed) => {
            setImporting(null);
            if (changed) getExternalCapture(captureId).then((d) => setLoaded({ id: captureId, data: d })).catch(() => {});
          }} />
      )}
      {picker && (
        <DirPickerDialog t={t} onClose={() => setPicker(false)}
          onSelect={(path) => { setPicker(false); void doExport(path); }} />
      )}
    </Panel>
  );
}
