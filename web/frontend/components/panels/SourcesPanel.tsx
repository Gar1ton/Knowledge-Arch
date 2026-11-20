"use client";
/**
 * 3.0.0 回答的「来源」栏：一次联网补充用到的外部文献——核验、引用段落、出处、体系、引用图谱、导出。
 * 打开时占据工作台左栏 + 中栏（对话栏不动）；数据来自出处账本（`lib/externalApi.ts`）。
 * 「体系」= 用户自己的集合树，第一次打开该页签时才调用一次模型归类（后端缓存，之后不再调用）。
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
  exportExternalCapture,
  getExternalCapture,
  placeExternalCapture,
  type ExternalCapture,
  type ExternalSourceDetail,
  type Trust,
} from "@/lib/externalApi";
import { useI18n, type I18nKey } from "@/lib/i18n";

type Tab = "list" | "tree" | "graph";
const CITABLE: Trust[] = ["verified", "single"];
const TRUST_TONE: Record<Trust, "ok" | "neutral" | "warn" | "danger"> = {
  verified: "ok", single: "neutral", conflict: "warn", retracted: "danger", unresolved: "neutral",
};
const ENGINE: Record<string, string> = {
  crossref: "Crossref", openalex: "OpenAlex", openlibrary: "Open Library", arxiv: "arXiv",
  semantic_scholar: "Semantic Scholar", datacite: "DataCite",
};

function authorsLabel(s: ExternalSourceDetail): string {
  const raw = (s.meta.creators?.length ? s.meta.creators : s.meta.creator_names) ?? [];
  const names = raw.map((n) => String(n).split(",")[0].trim()).filter(Boolean);
  if (!names.length) return "Anon.";
  if (names.length === 1) return names[0];
  if (names.length === 2) return `${names[0]} and ${names[1]}`;
  return `${names[0]} et al.`;
}

function placementKey(s: ExternalSourceDetail): string {
  if (!CITABLE.includes(s.trust)) return "none";
  if (s.placement?.path) return `p:${s.placement.path}`;
  if (s.placement?.new_direction) return `n:${s.placement.new_direction}`;
  return "none";
}

export function SourcesPanel() {
  const { sourcePanel, setSourcePanel, setSelectedDocId } = useConsole();
  const { t } = useI18n();
  const { toast } = useToast();
  const [loaded, setLoaded] = useState<{ id: string; data?: ExternalCapture; error?: string } | null>(null);
  const [tab, setTab] = useState<Tab>("list");
  const [sel, setSel] = useState<string | null>(null);
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
        setSel(sourcePanel?.focus ?? d.sources.find((s) => s.cited.length)?.source_id ?? null);
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
    const needs = data.sources.some((s) => CITABLE.includes(s.trust) && !s.placement?.path && !s.placement?.new_direction);
    if (!needs) return;
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
  const selected = data?.sources.find((s) => s.source_id === sel) ?? null;

  const right = (
    <>
      {data && <Button size="sm" variant="outline" onClick={() => void startExport()}>{t("src_export")}</Button>}
      <IconButton name="x" label={t("src_close")} onClick={() => setSourcePanel(null)} />
    </>
  );

  return (
    <Panel
      title={data ? t("src_panel_title", { n: data.capture.ordinal }) : t("src_panel_title_plain")}
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
              {(["list", "tree", "graph"] as Tab[]).map((v) => (
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
                engines: data.capture.engines.map((e) => ENGINE[e] ?? e).join(" · "),
              })}
            </span>
          </div>
          <div className="src-grid">
            <div className="src-main">
              {tab === "list" && <SourceList sources={sources} sel={sel} onSel={setSel} t={t} />}
              {tab === "tree" && (
                <PlacementTree data={data} placing={placing} t={t}
                  onPick={(key) => { setFilter(key); setTab("list"); }} />
              )}
              {tab === "graph" && (
                <SourceGraph data={data} sel={sel} onSel={setSel} t={t}
                  onOpenLibrary={(docId) => { setSourcePanel(null); setSelectedDocId(docId); }} />
              )}
            </div>
            <div className="src-detail">
              {selected ? <SourceDetail s={selected} t={t} /> : <div className="src-note">{t("src_detail_empty")}</div>}
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

function TrustBadge({ trust, t }: { trust: Trust; t: T }) {
  return <Badge tone={TRUST_TONE[trust]}>{t(`src_trust_${trust}` as I18nKey)}</Badge>;
}

function SourceRow({ s, sel, onSel, t }: { s: ExternalSourceDetail; sel: boolean; onSel: () => void; t: T }) {
  const pages = s.cited.filter((c) => c.page != null).map((c) => `p.${c.page}`);
  const where = !s.cited.length
    ? t("src_not_cited")
    : pages.length
      ? t("src_cited_pages", { n: s.cited.length, pages: pages.join("、") })
      : t("src_cited_abstract", { n: s.cited.length });
  const ranks = s.hits.map((h) => `${ENGINE[h.engine] ?? h.engine} #${h.rank}`).join(" · ");
  return (
    <div className={`src-row ${sel ? "on" : ""}`} role="button" tabIndex={0} onClick={onSel}
      onKeyDown={(e) => { if (e.key === "Enter") onSel(); }}>
      <div className="src-row-main">
        <span className="src-badges">
          <TrustBadge trust={s.trust} t={t} />
          {String(s.meta.item_type || "").includes("preprint") && <Badge tone="warn">{t("src_preprint")}</Badge>}
          {s.meta.in_library_doc_id && <Badge tone="info">{t("src_in_library")}</Badge>}
        </span>
        <b>{authorsLabel(s)} ({s.meta.year || "n.d."})</b> {s.meta.title || s.source_id}
      </div>
      <div className="src-row-sub">
        {where} · {ranks}
        {s.drift && <span className="src-drift"> · {t("src_drift", { a: s.drift[0], b: s.drift[1] })}</span>}
      </div>
    </div>
  );
}

function SourceList({ sources, sel, onSel, t }: {
  sources: ExternalSourceDetail[]; sel: string | null; onSel: (id: string) => void; t: T;
}) {
  const cited = sources.filter((s) => s.cited.length);
  const rest = sources.filter((s) => !s.cited.length);
  return (
    <>
      <div className="src-group">{t("src_group_cited", { n: cited.length })}</div>
      {cited.map((s) => <SourceRow key={s.source_id} s={s} sel={sel === s.source_id} onSel={() => onSel(s.source_id)} t={t} />)}
      <div className="src-group">{t("src_group_retrieved", { n: rest.length })}</div>
      {rest.map((s) => <SourceRow key={s.source_id} s={s} sel={sel === s.source_id} onSel={() => onSel(s.source_id)} t={t} />)}
    </>
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

// 图谱节点标签里作者部分的最大字符数（超出截断、保留年份，悬停显示全名）。
const GRAPH_LABEL_MAX = 18;

function SourceGraph({ data, sel, onSel, onOpenLibrary, t }: {
  data: ExternalCapture; sel: string | null; onSel: (id: string) => void;
  onOpenLibrary: (docId: string) => void; t: T;
}) {
  const ids = [...data.sources.map((s) => s.source_id), ...data.graph.library_nodes];
  const W = 700, H = 420, R = 165, cx = W / 2, cy = H / 2;
  const pos = new Map(ids.map((id, i) => {
    const a = (2 * Math.PI * i) / Math.max(ids.length, 1) - Math.PI / 2;
    return [id, [cx + R * Math.cos(a), cy + R * Math.sin(a)] as [number, number]];
  }));
  const byId = new Map(data.sources.map((s) => [s.source_id, s]));
  const color = (s?: ExternalSourceDetail) =>
    !s ? "var(--fg)" : s.trust === "retracted" ? "var(--danger)" : s.trust === "conflict" ? "var(--warn)"
      : s.trust === "verified" ? "var(--fg)" : "var(--fg-subtle)";
  return (
    <div className="src-graph">
      <div className="src-legend">{t("src_graph_legend")}</div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t("src_tab_graph")}>
        <defs>
          <marker id="src-arrow" viewBox="0 0 8 8" refX="14" refY="4" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="var(--fg-subtle)" />
          </marker>
        </defs>
        {data.graph.edges.map((e, i) => {
          const a = pos.get(e.source), b = pos.get(e.target);
          if (!a || !b) return null;
          return e.kind === "coupling"
            ? <line key={i} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke="var(--border-strong)" strokeWidth={1 + Math.min(e.weight, 6)} />
            : <line key={i} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} stroke="var(--fg-subtle)" strokeWidth={1.2} markerEnd="url(#src-arrow)" />;
        })}
        {ids.map((id) => {
          const [x, y] = pos.get(id)!;
          const s = byId.get(id);
          const lib = id.startsWith("lib:");
          const who = lib ? t("src_library_node") : authorsLabel(s!).replace(" et al.", " 等");
          const year = lib ? "" : String(s?.meta.year ?? "");
          const full = `${who} ${year}`.trim();
          const label = `${who.length > GRAPH_LABEL_MAX ? `${who.slice(0, GRAPH_LABEL_MAX - 1)}…` : who} ${year}`.trim();
          return (
            <g key={id} className="src-gnode" onClick={() => (lib ? onOpenLibrary(id.slice(4)) : onSel(id))}>
              <circle cx={x} cy={y} r={sel === id ? 10 : 8} fill={lib || s?.meta.in_library_doc_id ? color(s) : "var(--surface)"}
                stroke={color(s)} strokeWidth={2} strokeDasharray={!lib && s && !s.cited.length ? "3 2" : undefined} />
              <text x={x + (x >= cx ? 13 : -13)} y={y + 4} textAnchor={x >= cx ? "start" : "end"}
                className={sel === id ? "on" : ""}><title>{full}</title>{label}</text>
            </g>
          );
        })}
      </svg>
      <div className="src-note">{data.graph.edges.length ? t("src_graph_note") : t("src_graph_empty")}</div>
    </div>
  );
}

function SourceDetail({ s, t }: { s: ExternalSourceDetail; t: T }) {
  const authors = (s.meta.creators?.length ? s.meta.creators : s.meta.creator_names) ?? [];
  return (
    <div className="src-det">
      <div>
        <h3>{s.meta.title || s.source_id}</h3>
        <div className="src-meta">{authors.join("; ")} · {s.meta.year || "n.d."} · {s.meta.venue || "—"}</div>
        <div className="src-meta mono">{s.source_id}</div>
      </div>
      <div className="src-actions">
        <Button size="sm" variant="primary" disabled title={t("src_import_soon")}>{t("src_import")}</Button>
        {s.meta.url && <a className="src-link" href={String(s.meta.url)} target="_blank" rel="noreferrer">{t("src_open_link")}</a>}
      </div>
      <section className="src-box">
        <span className="src-h">{t("src_detail_trust")}</span>
        <TrustBadge trust={s.trust} t={t} />
        <p>{t(`src_trust_${s.trust}_desc` as I18nKey)}</p>
      </section>
      <section className="src-box">
        <span className="src-h">{t("src_detail_cited")}</span>
        {s.cited.length ? s.cited.map((c) => (
          <blockquote key={c.ord} className="src-quote">
            <b>{c.page != null ? `p.${c.page}` : t("src_abstract")}</b> {c.passage}
          </blockquote>
        )) : <p>{t("src_cited_none")}</p>}
        {s.cited.some((c) => c.file_sha256) && (
          <p className="src-meta">{t("src_cited_sha", { sha: s.cited.find((c) => c.file_sha256)!.file_sha256.slice(0, 12) })}</p>
        )}
      </section>
      {(s.placement?.path || s.placement?.new_direction) && (
        <section className="src-box">
          <span className="src-h">{t("src_detail_placement")}</span>
          <b>{s.placement.path || t("src_placement_new", { name: s.placement.new_direction ?? "" })}</b>
          {s.placement.reason && <p>{s.placement.reason}</p>}
        </section>
      )}
      <section className="src-box">
        <span className="src-h">{t("src_detail_hits")}</span>
        <table className="src-kv"><tbody>
          {s.hits.map((h, i) => (
            <tr key={i}><td>{ENGINE[h.engine] ?? h.engine}</td><td>#{h.rank}</td><td className="mono">{h.query}</td></tr>
          ))}
        </tbody></table>
        {s.history.length > 1 && (
          <p className="src-meta">{t("src_detail_history")}: {s.history.map((h) => `#${h.rank}`).join(" → ")}</p>
        )}
      </section>
      <section className="src-box">
        <span className="src-h">{t("src_detail_fields")}</span>
        <table className="src-kv"><tbody>
          {s.fields.map((f, i) => <tr key={i}><td>{f.field}</td><td>{ENGINE[f.engine] ?? f.engine}</td></tr>)}
        </tbody></table>
      </section>
      {s.meta.abstract && (
        <section className="src-box">
          <span className="src-h">{t("src_detail_abstract")}</span>
          <p>{String(s.meta.abstract)}</p>
        </section>
      )}
    </div>
  );
}
