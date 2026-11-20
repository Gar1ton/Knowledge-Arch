"use client";
/**
 * 来源栏「图谱」：这次外部文献之间、以及它们与库内文档之间的引用关系。
 * 边只来自参考文献列表（`kacore/domain/source_graph.py`），模型不连线；布局见 `lib/sourceGraphLayout.ts`。
 *
 * 操作与数据流页一致：滚轮以光标为中心缩放、拖空白处平移、右下角放大 / 缩小 / 适应窗口。另外：
 * 拖节点调整位置；悬停或 Tab 聚焦显示全名与连线数，并把不相连的节点调暗；单击选中（右侧显示详情），
 * 库内文档单击打开；双击空白处回到适应窗口。缩放时节点之间按倍数拉开，圆点和文字只略微变大，
 * 挤在一起的节点放大后能分开、字也不会糊。
 */
import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { FitIcon, MinusIcon, PlusIcon } from "@/components/flow/Icons";
import { authorsLabel, type ExternalCapture, type ExternalSourceDetail } from "@/lib/externalApi";
import type { I18nKey } from "@/lib/i18n";
import { layoutGraph, type LayoutPoint, type Point } from "@/lib/sourceGraphLayout";

type T = (key: I18nKey, vars?: Record<string, string | number>) => string;
type View = { s: number; x: number; y: number };
type Drag =
  | { kind: "pan"; sx: number; sy: number; ox: number; oy: number; moved: boolean }
  | { kind: "node"; id: string; sx: number; sy: number; wx: number; wy: number; moved: boolean };
type GNode = { id: string; lib: boolean; src?: ExternalSourceDetail; cited: boolean; label: string; full: string };
type GEdge = ExternalCapture["graph"]["edges"][number];

const MIN_SCALE = 0.3;
const MAX_SCALE = 4;
const MAX_FIT_SCALE = 1.6;   // 节点很少时，适应窗口不把图放得过大
const BUTTON_STEP = 1.25;
const DRAG_SLOP = 4;         // 按下后移动超过这么多像素才算拖动，否则算单击
const LABEL_ALL_MAX = 14;    // 节点不多于此数时标签常显；更多时只常显引用的、选中的、悬停及其相邻的
const LABEL_ALL_ZOOM = 1.5;  // 放大到这个倍数及以上时标签全部常显
const AUTHOR_MAX = 18;       // 标签里作者部分的最大字符数（超出截断、保留年份，悬停看全名）
const FIT_MARGIN = 18;       // 适应窗口时节点与标签离画布边缘的最小距离
const FONT_PX = 12;          // 标签字号（缩小时不再变小，放大时略变大）
const R_CITED = 10;
const R_FOUND = 7.5;
const R_LIB = 8.5;
const TIP_W = 300;
const TIP_INSET = 10;

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

function shortLabel(who: string, year: string): string {
  const w = who.length > AUTHOR_MAX ? `${who.slice(0, AUTHOR_MAX - 1)}…` : who;
  return `${w} ${year}`.trim();
}

function tone(s?: ExternalSourceDetail): string {
  if (!s) return "var(--info)";
  if (s.trust === "retracted") return "var(--danger)";
  if (s.trust === "conflict") return "var(--warn)";
  return s.trust === "verified" ? "var(--fg)" : "var(--fg-muted)";
}

const nodeScale = (s: number) => clamp(Math.sqrt(s), 0.85, 1.35);
const fontPx = (s: number) => FONT_PX * clamp(Math.sqrt(s), 1, 1.3);

/** 估算标签宽度（像素）：中日韩字符按一个字号宽，其余按 0.56 个字号。只用于适应窗口时留边。 */
function textWidth(text: string, px: number): number {
  let w = 0;
  for (const ch of text) w += (ch.codePointAt(0) ?? 0) >= 0x2e80 ? px : px * 0.56;
  return w;
}

/** 每个节点在屏幕上向左 / 右 / 上 / 下各占多少像素（圆点 + 常显的标签）。 */
type Extent = { x: number; y: number; l: number; r: number; v: number };

/**
 * 适应窗口：找最大的缩放倍数，使所有节点连同常显标签都落在画布内（标签宽度是固定像素、
 * 不随缩放变，所以按倍数二分查找），再把可行范围居中。
 */
function fitView(items: (s: number) => Extent[], w: number, h: number): View {
  const probe = items(1);
  if (!probe.length || !w || !h) return { s: 1, x: w / 2, y: h / 2 };
  const range = (s: number) => {
    const ex = items(s);
    const loX = Math.max(...ex.map((e) => FIT_MARGIN + e.l - e.x * s));
    const hiX = Math.min(...ex.map((e) => w - FIT_MARGIN - e.r - e.x * s));
    const loY = Math.max(...ex.map((e) => FIT_MARGIN + e.v - e.y * s));
    const hiY = Math.min(...ex.map((e) => h - FIT_MARGIN - e.v - e.y * s));
    return { loX, hiX, loY, hiY, ok: loX <= hiX && loY <= hiY };
  };
  let lo = MIN_SCALE, hi = MAX_FIT_SCALE;
  if (range(hi).ok) lo = hi;
  else {
    for (let i = 0; i < 24; i++) {
      const mid = (lo + hi) / 2;
      if (range(mid).ok) lo = mid; else hi = mid;
    }
  }
  const r = range(lo);
  return { s: lo, x: (r.loX + r.hiX) / 2, y: (r.loY + r.hiY) / 2 };
}

export function SourceGraph({ data, sel, onSel, onOpenLibrary, t }: {
  data: ExternalCapture; sel: string | null; onSel: (id: string) => void;
  onOpenLibrary: (docId: string) => void; t: T;
}) {
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "");
  const boxRef = useRef<HTMLDivElement | null>(null);
  const drag = useRef<Drag | null>(null);
  const viewRef = useRef<View>({ s: 1, x: 0, y: 0 });
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [userView, setUserView] = useState<View | null>(null);   // null = 跟随「适应窗口」
  const [moved, setMoved] = useState<Record<string, Point>>({});
  const [hover, setHover] = useState<string | null>(null);
  const [dragging, setDragging] = useState<Drag["kind"] | null>(null);
  const [onlyCited, setOnlyCited] = useState(false);

  const { nodes, edges } = useMemo(() => {
    const shown = data.sources.filter((s) => !onlyCited || s.cited.length > 0);
    const keep = new Set(shown.map((s) => s.source_id));
    const es = data.graph.edges.filter((e) => keep.has(e.source) && (e.kind === "cites_library" || keep.has(e.target)));
    const ns: GNode[] = shown.map((s) => {
      const who = authorsLabel(s), year = String(s.meta.year ?? "");
      return { id: s.source_id, lib: false, src: s, cited: s.cited.length > 0,
        label: shortLabel(who, year), full: `${who} ${year}`.trim() };
    });
    for (const id of new Set(es.filter((e) => e.kind === "cites_library").map((e) => e.target))) {
      const title = data.graph.library_titles?.[id] || t("src_library_node");
      ns.push({ id, lib: true, cited: false, label: shortLabel(title, ""), full: title });
    }
    return { nodes: ns, edges: es };
  }, [data, onlyCited, t]);

  const base = useMemo(() => layoutGraph(nodes.map((n) => n.id), edges), [nodes, edges]);
  const posOf = useCallback((id: string): LayoutPoint => {
    const b = base.get(id) ?? { x: 0, y: 0, right: true };
    return moved[id] ? { ...moved[id], right: b.right } : b;
  }, [moved, base]);
  const fitted = useMemo(() => fitView((s) => nodes.map((n) => {
    const p = posOf(n.id);
    const r = (n.lib ? R_LIB : n.cited ? R_CITED : R_FOUND) * nodeScale(s);
    const tw = n.cited || nodes.length <= LABEL_ALL_MAX ? 6 + textWidth(n.label, fontPx(s)) : 0;
    return { x: p.x, y: p.y, l: r + (p.right ? 0 : tw), r: r + (p.right ? tw : 0), v: Math.max(r, fontPx(s) * 0.7) };
  }), size.w, size.h), [nodes, posOf, size]);
  const view = userView ?? fitted;
  useEffect(() => { viewRef.current = view; });

  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);
  const adj = useMemo(() => {
    const m = new Map<string, Set<string>>();
    const add = (a: string, b: string) => { if (!m.has(a)) m.set(a, new Set()); m.get(a)!.add(b); };
    for (const e of edges) { add(e.source, e.target); add(e.target, e.source); }
    return m;
  }, [edges]);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      setSize({ w: Math.round(entry.contentRect.width), h: Math.round(entry.contentRect.height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const zoomAt = useCallback((clientX: number, clientY: number, factor: number) => {
    const el = boxRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const px = clientX - rect.left, py = clientY - rect.top;
    const cur = viewRef.current;
    const s = clamp(cur.s * factor, MIN_SCALE, MAX_SCALE);
    const k = s / cur.s;
    const next = { s, x: px - (px - cur.x) * k, y: py - (py - cur.y) * k };
    viewRef.current = next;
    setUserView(next);
  }, []);

  // React 的 onWheel 是被动监听，没法阻止外层滚动；这里挂原生非被动监听。
  // 鼠标滚轮一格约 ±100，触控板双指捏合带 ctrlKey、幅度小，按指数换算成平滑倍数。
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      zoomAt(e.clientX, e.clientY, Math.exp(-clamp(e.deltaY, -60, 60) * (e.ctrlKey ? 0.01 : 0.002)));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  const zoomCenter = (factor: number) => {
    const rect = boxRef.current?.getBoundingClientRect();
    if (rect) zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, factor);
  };

  const activate = (id: string) => {
    if (id.startsWith("lib:")) onOpenLibrary(id.slice(4));
    else onSel(id);
  };

  const onPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return;
    const id = (e.target as Element).closest("[data-node]")?.getAttribute("data-node") ?? null;
    e.currentTarget.setPointerCapture(e.pointerId);
    const cur = viewRef.current;
    if (id) {
      const p = posOf(id);
      drag.current = { kind: "node", id, sx: e.clientX, sy: e.clientY, wx: p.x, wy: p.y, moved: false };
    } else {
      drag.current = { kind: "pan", sx: e.clientX, sy: e.clientY, ox: cur.x, oy: cur.y, moved: false };
    }
  };

  const onPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.sx, dy = e.clientY - d.sy;
    if (!d.moved) {
      if (Math.hypot(dx, dy) < DRAG_SLOP) return;
      d.moved = true;
      setDragging(d.kind);
      setUserView(viewRef.current);   // 开始拖动后视图不再跟随「适应窗口」
    }
    const cur = viewRef.current;
    if (d.kind === "pan") {
      const next = { ...cur, x: d.ox + dx, y: d.oy + dy };
      viewRef.current = next;
      setUserView(next);
    } else {
      setMoved((m) => ({ ...m, [d.id]: { x: d.wx + dx / cur.s, y: d.wy + dy / cur.s } }));
    }
  };

  const onPointerUp = (e: React.PointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    drag.current = null;
    setDragging(null);
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      // 指针离开视口时可能已经释放
    }
    if (d && d.kind === "node" && !d.moved) activate(d.id);
  };

  const fit = () => setUserView(null);
  const toggleOnlyCited = () => {
    setOnlyCited((v) => !v);
    setMoved({});
    setUserView(null);
  };

  const k = nodeScale(view.s);
  const fpx = fontPx(view.s);
  const screen = (id: string): Point => {
    const p = posOf(id);
    return { x: p.x * view.s + view.x, y: p.y * view.s + view.y };
  };
  const radius = (n: GNode) => (n.lib ? R_LIB : n.cited ? R_CITED : R_FOUND) * k;
  const focus = dragging === "pan" ? null : hover;
  const near = focus ? adj.get(focus) ?? new Set<string>() : null;
  const allLabels = nodes.length <= LABEL_ALL_MAX || view.s >= LABEL_ALL_ZOOM;
  const lit = focus ?? sel;
  const tipNode = focus && !dragging ? byId.get(focus) ?? null : null;

  return (
    <div className="src-graph">
      <div className="src-graph-bar">
        <Legend t={t} inLibrary={data.sources.some((s) => s.meta.in_library_doc_id)} />
        <span style={{ flex: 1 }} />
        <label className="src-graph-toggle">
          <input type="checkbox" checked={onlyCited} onChange={toggleOnlyCited} />
          {t("src_graph_only_cited")}
        </label>
      </div>
      <div ref={boxRef} className={`src-graph-canvas ${dragging === "pan" ? "is-panning" : ""}`}>
        <div className="flow-grid-bg" style={{
          backgroundSize: `${26 * view.s}px ${26 * view.s}px`,
          backgroundPosition: `${view.x}px ${view.y}px`,
        }} />
        {size.w > 0 && (
          <svg width={size.w} height={size.h} role="img" aria-label={t("src_tab_graph")}
            onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            onDoubleClick={(e) => { if (!(e.target as Element).closest("[data-node]")) fit(); }}>
            <defs>
              {(["a", "h"] as const).map((v) => (
                <marker key={v} id={`${uid}-${v}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8"
                  markerHeight="8" markerUnits="userSpaceOnUse" orient="auto">
                  <path d="M0,1 L10,5 L0,9 z" fill={v === "h" ? "var(--fg)" : "var(--fg-subtle)"} />
                </marker>
              ))}
            </defs>
            {edges.map((e, i) => {
              const na = byId.get(e.source), nb = byId.get(e.target);
              if (!na || !nb) return null;
              const a = screen(e.source), b = screen(e.target);
              const vx = b.x - a.x, vy = b.y - a.y, len = Math.hypot(vx, vy);
              if (len < 1) return null;
              const hot = lit !== null && (e.source === lit || e.target === lit);
              const cls = `src-gedge src-gedge--${e.kind} ${hot ? "hot" : ""} ${focus && !hot ? "dim" : ""}`;
              if (e.kind === "coupling") {
                const off = len * 0.18;
                const cx = (a.x + b.x) / 2 - (vy / len) * off, cy = (a.y + b.y) / 2 + (vx / len) * off;
                return (
                  <path key={i} className={cls} d={`M${a.x},${a.y} Q${cx},${cy} ${b.x},${b.y}`}
                    strokeWidth={1.5 + Math.min(e.weight - 1, 4)}>
                    <title>{t("src_graph_coupling_tip", { n: e.weight })}</title>
                  </path>
                );
              }
              const ux = vx / len, uy = vy / len, ra = radius(na) + 2, rb = radius(nb) + 3;
              return (
                <line key={i} className={cls} x1={a.x + ux * ra} y1={a.y + uy * ra} x2={b.x - ux * rb} y2={b.y - uy * rb}
                  markerEnd={`url(#${uid}-${hot ? "h" : "a"})`} />
              );
            })}
            {nodes.map((n) => {
              const p = screen(n.id), r = radius(n), color = tone(n.src);
              const right = posOf(n.id).right;
              const showLabel = allLabels || n.cited || n.id === sel || n.id === focus || Boolean(near?.has(n.id));
              const filled = n.lib || Boolean(n.src?.meta.in_library_doc_id);
              const cls = ["src-gnode", n.cited && "cited", sel === n.id && "on", focus === n.id && "hot",
                focus && n.id !== focus && !near?.has(n.id) && "dim"].filter(Boolean).join(" ");
              return (
                <g key={n.id} data-node={n.id} className={cls} transform={`translate(${p.x},${p.y})`}
                  tabIndex={0} role="button" aria-label={n.lib ? n.full : `${n.full} ${n.src?.meta.title ?? ""}`}
                  onPointerEnter={() => setHover(n.id)} onPointerLeave={() => setHover((h) => (h === n.id ? null : h))}
                  onFocus={() => setHover(n.id)} onBlur={() => setHover((h) => (h === n.id ? null : h))}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(n.id); }
                  }}>
                  <circle className="src-ghit" r={Math.max(r + 8, 16)} />
                  {sel === n.id && <circle className="src-gring" r={r + 5} />}
                  {n.lib
                    ? <rect className="src-gdot" x={-r} y={-r} width={2 * r} height={2 * r} rx={3} fill={color} stroke={color} />
                    : <circle className="src-gdot" r={r} fill={filled ? color : "var(--surface)"} stroke={color}
                        strokeDasharray={n.cited ? undefined : "3 2.5"} />}
                  {showLabel && (
                    <text x={right ? r + 6 : -(r + 6)} y={4} textAnchor={right ? "start" : "end"}
                      style={{ fontSize: fpx }}>{n.label}</text>
                  )}
                </g>
              );
            })}
          </svg>
        )}
        {tipNode && <Tip node={tipNode} at={screen(tipNode.id)} box={size} edges={edges} t={t} />}
      </div>
      <div className="src-graph-foot">
        <div className="src-graph-foot-text">
          <div>{edges.length ? t("src_graph_note") : t("src_graph_empty")}</div>
          <div>{t("src_graph_hint")}</div>
        </div>
        <div className="flow-zoom-control src-graph-zoom">
          <button type="button" onClick={() => zoomCenter(BUTTON_STEP)} aria-label={t("src_graph_zoom_in")}
            title={t("src_graph_zoom_in")}><PlusIcon /></button>
          <span className="flow-zoom-value">{Math.round(view.s * 100)}%</span>
          <button type="button" onClick={() => zoomCenter(1 / BUTTON_STEP)} aria-label={t("src_graph_zoom_out")}
            title={t("src_graph_zoom_out")}><MinusIcon /></button>
          <button type="button" onClick={fit} aria-label={t("src_graph_fit")} title={t("src_graph_fit")}><FitIcon /></button>
        </div>
      </div>
    </div>
  );
}

/** 悬停信息卡放在离该节点最远的画布角落，不挡住正在高亮的相邻节点与连线。 */
function Tip({ node, at, box, edges, t }: {
  node: GNode; at: Point; box: { w: number; h: number }; edges: GEdge[]; t: T;
}) {
  const corners = [{ x: 0, y: 0 }, { x: box.w, y: 0 }, { x: 0, y: box.h }, { x: box.w, y: box.h }];
  const far = corners.reduce((a, c) => (Math.hypot(c.x - at.x, c.y - at.y) > Math.hypot(a.x - at.x, a.y - at.y) ? c : a));
  const place: React.CSSProperties = {
    width: Math.min(TIP_W, box.w - 2 * TIP_INSET),
    ...(far.x === 0 ? { left: TIP_INSET } : { right: TIP_INSET }),
    ...(far.y === 0 ? { top: TIP_INSET } : { bottom: TIP_INSET }),
  };
  const s = node.src;
  const out = edges.filter((e) => e.source === node.id && e.kind !== "coupling").length;
  const into = edges.filter((e) => e.target === node.id && e.kind !== "coupling").length;
  const shared = edges.filter((e) => e.kind === "coupling" && (e.source === node.id || e.target === node.id)).length;
  return (
    <div className="src-gtip" style={place}>
      {s ? (
        <>
          <b>{authorsLabel(s)} ({s.meta.year || "n.d."})</b>
          <div>{s.meta.title || s.source_id}</div>
          {s.meta.venue && <div className="m">{s.meta.venue}</div>}
          <div className="m">
            {t(`src_trust_${s.trust}` as I18nKey)} · {t("src_graph_tip_links", { out, into, shared })}
          </div>
        </>
      ) : (
        <>
          <b>{t("src_library_node")}</b>
          <div>{node.full}</div>
          <div className="m">{t("src_graph_tip_open")}</div>
        </>
      )}
    </div>
  );
}

function Legend({ t, inLibrary }: { t: T; inLibrary: boolean }) {
  const swatch = (child: React.ReactNode) => (
    <svg width="18" height="14" viewBox="0 0 18 14" aria-hidden="true">{child}</svg>
  );
  return (
    <span className="src-legend">
      <span>{swatch(<circle cx="9" cy="7" r="5" fill="var(--surface)" stroke="var(--fg)" strokeWidth="2" />)}{t("src_graph_lg_cited")}</span>
      <span>{swatch(<circle cx="9" cy="7" r="4.5" fill="var(--surface)" stroke="var(--fg-muted)" strokeWidth="1.6" strokeDasharray="2.5 2" />)}{t("src_graph_lg_found")}</span>
      {inLibrary && <span>{swatch(<circle cx="9" cy="7" r="5" fill="var(--fg)" />)}{t("src_graph_lg_in_library")}</span>}
      <span>{swatch(<rect x="4" y="2" width="10" height="10" rx="2" fill="var(--info)" />)}{t("src_library_node")}</span>
      <span>{swatch(<circle cx="9" cy="7" r="5" fill="var(--surface)" stroke="var(--danger)" strokeWidth="2" />)}{t("src_graph_lg_retracted")}</span>
      <span>{swatch(<><line x1="1" y1="7" x2="12" y2="7" stroke="var(--fg-subtle)" strokeWidth="1.4" /><path d="M11,3.5 L17,7 L11,10.5 z" fill="var(--fg-subtle)" /></>)}{t("src_graph_lg_cites")}</span>
      <span>{swatch(<path d="M1,11 Q9,1 17,11" fill="none" stroke="var(--info)" strokeOpacity="0.55" strokeWidth="2.5" />)}{t("src_graph_lg_coupling")}</span>
    </span>
  );
}
