"use client";
/**
 * 3.0.0 回答的「来源」栏：一次联网补充登记的外部来源与全部工具调用——引用段落、出处（调用原文）、体系、导出。
 * 打开时占据工作台左栏 + 中栏（对话栏不动）；数据来自调用账本（`lib/externalApi.ts`）。
 * 每条来源都指向一次工具调用的原文块（可追溯核验）；「体系」= 用户自己的集合树，第一次打开该页签时
 * 才调用一次模型归类（后端缓存，之后不再调用）。
 */
import React, { useEffect, useMemo, useState } from "react";
import { Panel } from "@/components/ds/Panel";
import { Badge } from "@/components/ds/Badge";
import { Button } from "@/components/ds/Button";
import { IconButton } from "@/components/ds/IconButton";
import { DirPickerDialog } from "@/components/flow/DirPickerDialog";
import { useToast } from "@/components/ui/Toast";
import { useConsole } from "@/lib/ConsoleContext";
import { inDesktopShell, pickFolder } from "@/lib/backend";
import {
  argumentsLabel,
  authorsLabel,
  exportExternalCapture,
  getExternalCapture,
  placeExternalCapture,
  type ExternalCapture,
  type ExternalSourceDetail,
  type ToolCallRecord,
} from "@/lib/externalApi";
import { useI18n, type I18nKey } from "@/lib/i18n";

type Tab = "list" | "tree";
type Sel = { kind: "source" | "call"; id: string } | null;

function placementKey(s: ExternalSourceDetail): string {
  if (s.placement?.path) return `p:${s.placement.path}`;
  if (s.placement?.new_direction) return `n:${s.placement.new_direction}`;
  return "none";
}

export function SourcesPanel() {
  const { sourcePanel, setSourcePanel } = useConsole();
  const { t } = useI18n();
  const { toast } = useToast();
  const [loaded, setLoaded] = useState<{ id: string; data?: ExternalCapture; error?: string } | null>(null);
  const [tab, setTab] = useState<Tab>("list");
  const [sel, setSel] = useState<Sel>(null);
  const [placing, setPlacing] = useState(false);
  const [filter, setFilter] = useState<string | null>(null);
  const [picker, setPicker] = useState(false);
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

  const openTree = async () => {
    setTab("tree");
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

  const sources = useMemo(
    () => (data?.sources ?? []).filter((s) => !filter || placementKey(s) === filter),
    [data, filter],
  );
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
          <div className="src-bar">
            <span className="src-seg" role="tablist">
              {(["list", "tree"] as Tab[]).map((v) => (
                <button key={v} type="button" role="tab" aria-selected={tab === v}
                  className={tab === v ? "on" : ""}
                  onClick={() => (v === "tree" ? void openTree() : setTab(v))}>
                  {t(`src_tab_${v}` as I18nKey)}
                </button>
              ))}
            </span>
            {filter && (
              <Badge tone="info">
                {filterLabel(filter, t)}{" "}
                <button type="button" className="src-link" onClick={() => setFilter(null)} aria-label={t("src_clear_filter")}>×</button>
              </Badge>
            )}
            <span style={{ flex: 1 }} />
            <span className="src-meta">
              {t("src_panel_meta", {
                time: new Date(data.capture.captured_at).toLocaleString(),
                question: data.capture.question,
              })}
            </span>
          </div>
          <div className="src-grid">
            <div className="src-main">
              {tab === "list" && (
                <>
                  <div className="src-group">{t("src_group_sources", { n: sources.length })}</div>
                  {sources.map((s) => (
                    <SourceRow key={s.source_id} s={s} t={t}
                      sel={sel?.kind === "source" && sel.id === s.source_id}
                      onSel={() => setSel({ kind: "source", id: s.source_id })} />
                  ))}
                  <div className="src-group">{t("src_group_calls", { n: data.calls.length })}</div>
                  {data.calls.map((c) => (
                    <CallRow key={c.call_id} c={c} t={t}
                      sel={sel?.kind === "call" && sel.id === c.call_id}
                      onSel={() => setSel({ kind: "call", id: c.call_id })} />
                  ))}
                </>
              )}
              {tab === "tree" && (
                <PlacementTree data={data} placing={placing} t={t}
                  onPick={(key) => { setFilter(key); setTab("list"); }} />
              )}
            </div>
            <div className="src-detail">
              {selSource && <SourceDetail s={selSource} call={data.calls.find((c) => c.call_id === selSource.call_id)} t={t} />}
              {selCall && <CallDetail c={selCall} t={t} />}
              {!selSource && !selCall && <div className="src-note">{t("src_detail_empty")}</div>}
            </div>
          </div>
        </>
      )}
      {picker && (
        <DirPickerDialog t={t} onClose={() => setPicker(false)}
          onSelect={(path) => { setPicker(false); void doExport(path); }} />
      )}
    </Panel>
  );
}

type T = (key: I18nKey, vars?: Record<string, string | number>) => string;

function filterLabel(key: string, t: T): string {
  if (key === "none") return t("src_tree_none");
  if (key.startsWith("n:")) return t("src_placement_new", { name: key.slice(2) });
  return key.slice(2);
}

function where(s: ExternalSourceDetail, t: T): string {
  return s.locator || t("src_block", { n: s.block + 1 });
}

function SourceRow({ s, sel, onSel, t }: { s: ExternalSourceDetail; sel: boolean; onSel: () => void; t: T }) {
  return (
    <div className={`src-row ${sel ? "on" : ""}`} role="button" tabIndex={0} onClick={onSel}
      onKeyDown={(e) => { if (e.key === "Enter") onSel(); }}>
      <div className="src-row-main">
        <span className="src-badges">
          <Badge tone={s.cited ? "ok" : "neutral"}>{s.source_id} · {s.cited ? t("src_used") : t("src_not_used")}</Badge>
        </span>
        <b>{authorsLabel(s)} ({s.fields.year || "n.d."})</b> {s.fields.title || s.source_id}
      </div>
      <div className="src-row-sub">{s.tool} · {s.call_label} · {where(s, t)}</div>
    </div>
  );
}

function CallRow({ c, sel, onSel, t }: { c: ToolCallRecord; sel: boolean; onSel: () => void; t: T }) {
  return (
    <div className={`src-row ${sel ? "on" : ""}`} role="button" tabIndex={0} onClick={onSel}
      onKeyDown={(e) => { if (e.key === "Enter") onSel(); }}>
      <div className="src-row-main">
        <span className="src-badges">
          <Badge tone={c.is_error ? "warn" : "neutral"}>{c.label}</Badge>
        </span>
        <b className="mono">{c.tool}</b> <span className="src-meta">{argumentsLabel(c.arguments)}</span>
      </div>
      <div className="src-row-sub">
        {c.is_error ? `${t("src_call_error")}: ${c.blocks[0]?.text ?? ""}` : t("tools_blocks", { n: c.blocks.length })}
        {" · "}{c.elapsed_ms} ms
      </div>
    </div>
  );
}

function PlacementTree({ data, placing, t, onPick }: {
  data: ExternalCapture; placing: boolean; t: T; onPick: (key: string) => void;
}) {
  const counts = new Map<string, number>();
  data.sources.forEach((s) => counts.set(placementKey(s), (counts.get(placementKey(s)) ?? 0) + 1));
  const paths = [...counts.keys()].filter((k) => k.startsWith("p:")).sort();
  const news = [...counts.keys()].filter((k) => k.startsWith("n:")).sort();
  const node = (key: string, label: string) => (
    <div key={key} className="src-tnode">
      <button type="button" onClick={() => onPick(key)}>{label}</button>
      <span className="src-n">{counts.get(key) ?? 0}</span>
    </div>
  );
  return (
    <div className="src-tree">
      {placing && <div className="src-note">{t("src_tree_placing")}</div>}
      <div className="src-group">{t("src_tree_collections")}</div>
      {paths.length ? paths.map((k) => node(k, k.slice(2))) : <div className="src-note">{t("src_tree_empty")}</div>}
      <div className="src-group">{t("src_tree_new")}</div>
      {news.length ? news.map((k) => node(k, k.slice(2))) : <div className="src-note">—</div>}
      <div className="src-group" />
      {counts.get("none") ? node("none", t("src_tree_none")) : null}
      <div className="src-explain">{t("src_tree_explain")}</div>
    </div>
  );
}

function SourceDetail({ s, call, t }: { s: ExternalSourceDetail; call?: ToolCallRecord; t: T }) {
  const meta = [s.fields.year || "n.d.", s.fields.venue, s.fields.identifier].filter(Boolean).join(" · ");
  return (
    <div className="src-det">
      <div>
        <h3>{s.fields.title || s.source_id}</h3>
        <div className="src-meta">{(s.fields.creators ?? []).join("; ")}</div>
        <div className="src-meta">{meta}</div>
      </div>
      <div className="src-actions">
        <Button size="sm" variant="primary" disabled title={t("src_import_soon")}>{t("src_import")}</Button>
        {s.fields.url && <a className="src-link" href={s.fields.url} target="_blank" rel="noreferrer">{t("src_open_link")}</a>}
      </div>
      <section className="src-box">
        <span className="src-h">{t("src_detail_passage")}</span>
        <blockquote className="src-quote"><b>{where(s, t)}</b> {s.passage}</blockquote>
      </section>
      {(s.placement?.path || s.placement?.new_direction) && (
        <section className="src-box">
          <span className="src-h">{t("src_detail_placement")}</span>
          <b>{s.placement.path || t("src_placement_new", { name: s.placement.new_direction ?? "" })}</b>
          {s.placement.reason && <p>{s.placement.reason}</p>}
        </section>
      )}
      {call && <CallBlocks c={call} highlight={s.block} title={t("src_detail_origin", { call: call.label })} t={t} />}
    </div>
  );
}

function CallDetail({ c, t }: { c: ToolCallRecord; t: T }) {
  return (
    <div className="src-det">
      <div>
        <h3 className="mono">{c.label} · {c.tool}</h3>
        <div className="src-meta">{new Date(c.started_at).toLocaleString()} · {c.elapsed_ms} ms</div>
      </div>
      <section className="src-box">
        <span className="src-h">{t("src_call_args")}</span>
        <pre className="src-pre">{JSON.stringify(c.arguments, null, 2)}</pre>
      </section>
      <CallBlocks c={c} highlight={-1} title={t("src_detail_origin", { call: c.label })} t={t} />
    </div>
  );
}

function CallBlocks({ c, highlight, title, t }: { c: ToolCallRecord; highlight: number; title: string; t: T }) {
  return (
    <section className="src-box">
      <span className="src-h">{c.is_error ? t("src_call_error") : title}</span>
      {c.blocks.map((b, i) => (
        <pre key={i} className={`src-pre ${i === highlight ? "on" : ""}`}>
          <b>{b.locator || t("src_block", { n: i + 1 })}</b>{"\n"}{b.text}
        </pre>
      ))}
    </section>
  );
}
