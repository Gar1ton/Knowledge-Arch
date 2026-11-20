/**
 * 来源图谱的布局（纯函数）。
 *
 * 1. 按连线把节点分成相连的簇；每个簇内部用 Fruchterman–Reingold 力导向排开（初始位置在圆上、
 *    迭代次数固定、不用随机数——同一组节点与边每次得到同样的位置，刷新不跳）。
 * 2. 没有任何连线的节点不参与力导向（否则会被斥力推到很远，把整张图撑大、主簇缩成一团），
 *    按顺序排成紧凑的网格。
 * 3. 各簇按节点数从多到少，逐行摆放（行宽取总面积的平方根附近，整体接近横向矩形），簇之间留间距。
 * 结果是世界坐标，界面再按视口缩放平移（`components/panels/SourceGraph.tsx`）。
 */

export type Point = { x: number; y: number };
/** right：标签放在节点右侧（簇内按节点在簇中心的哪一侧决定，标签朝外；孤立节点网格一律朝右）。 */
export type LayoutPoint = Point & { right: boolean };
export type LayoutEdge = { source: string; target: string; weight: number };

// 节点间的理想距离（世界坐标单位）、迭代次数、簇内向心力系数。节点数在几十以内，计算量很小。
const IDEAL = 110;
const ITERATIONS = 300;
const GRAVITY = 0.08;
// 孤立节点网格的格宽 / 格高（横向留出标签的位置）与簇之间的间距；行宽相对总面积平方根的倍数。
const CELL_W = 190;
const CELL_H = 64;
const GAP = 90;
const ROW_ASPECT = 1.6;

type Box = { ids: string[]; pts: Point[]; right: boolean[]; w: number; h: number };

function forceComponent(ids: string[], links: [number, number, number][]): Point[] {
  const n = ids.length;
  const r0 = IDEAL * Math.max(1, Math.sqrt(n) / 1.5);
  const xs = ids.map((_, i) => r0 * Math.cos((2 * Math.PI * i) / n - Math.PI / 2));
  const ys = ids.map((_, i) => r0 * Math.sin((2 * Math.PI * i) / n - Math.PI / 2));
  let temp = r0 / 3;
  const cool = temp / ITERATIONS;
  const dx = new Float64Array(n), dy = new Float64Array(n);
  for (let it = 0; it < ITERATIONS; it++) {
    dx.fill(0);
    dy.fill(0);
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let vx = xs[i] - xs[j], vy = ys[i] - ys[j];
        let d = Math.hypot(vx, vy);
        if (d < 0.01) { vx = 0.01 * (i - j); vy = 0.01; d = Math.hypot(vx, vy); }
        const f = (IDEAL * IDEAL) / d;
        dx[i] += (vx / d) * f; dy[i] += (vy / d) * f;
        dx[j] -= (vx / d) * f; dy[j] -= (vy / d) * f;
      }
    }
    for (const [a, b, w] of links) {
      const vx = xs[a] - xs[b], vy = ys[a] - ys[b];
      const d = Math.max(Math.hypot(vx, vy), 0.01);
      // 共同引用越多拉得越紧，但只按对数增长，免得一条重边把整簇拉歪。
      const f = ((d * d) / IDEAL) * (1 + Math.log(w));
      dx[a] -= (vx / d) * f; dy[a] -= (vy / d) * f;
      dx[b] += (vx / d) * f; dy[b] += (vy / d) * f;
    }
    for (let i = 0; i < n; i++) {
      dx[i] -= GRAVITY * xs[i];
      dy[i] -= GRAVITY * ys[i];
      const len = Math.hypot(dx[i], dy[i]);
      if (len > 0) {
        const step = Math.min(len, temp);
        xs[i] += (dx[i] / len) * step;
        ys[i] += (dy[i] / len) * step;
      }
    }
    temp = Math.max(temp - cool, 0.5);
  }
  return ids.map((_, i) => ({ x: xs[i], y: ys[i] }));
}

function boxOf(ids: string[], pts: Point[], grid: boolean): Box {
  const minX = Math.min(...pts.map((p) => p.x)), minY = Math.min(...pts.map((p) => p.y));
  const shifted = pts.map((p) => ({ x: p.x - minX, y: p.y - minY }));
  const w = Math.max(...shifted.map((p) => p.x)), h = Math.max(...shifted.map((p) => p.y));
  return { ids, pts: shifted, right: shifted.map((p) => grid || p.x >= w / 2), w, h };
}

export function layoutGraph(ids: string[], edges: LayoutEdge[]): Map<string, LayoutPoint> {
  const out = new Map<string, LayoutPoint>();
  if (ids.length === 0) return out;
  const index = new Map(ids.map((id, i) => [id, i]));
  const parent = ids.map((_, i) => i);
  const find = (i: number): number => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  const links: [number, number, number][] = [];
  for (const e of edges) {
    const a = index.get(e.source), b = index.get(e.target);
    if (a === undefined || b === undefined || a === b) continue;
    links.push([a, b, Math.max(1, e.weight)]);
    parent[find(a)] = find(b);
  }

  const groups = new Map<number, number[]>();
  ids.forEach((_, i) => {
    const root = find(i);
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root)!.push(i);
  });
  const boxes: Box[] = [];
  const lonely: string[] = [];
  for (const members of groups.values()) {
    if (members.length === 1) { lonely.push(ids[members[0]]); continue; }
    const local = new Map(members.map((m, k) => [m, k]));
    const inner = links.filter(([a]) => local.has(a)).map(([a, b, w]) => [local.get(a)!, local.get(b)!, w] as [number, number, number]);
    const cids = members.map((m) => ids[m]);
    boxes.push(boxOf(cids, forceComponent(cids, inner), false));
  }
  boxes.sort((a, b) => b.ids.length - a.ids.length);
  if (lonely.length) {
    const cols = Math.max(1, Math.ceil(Math.sqrt(lonely.length * 1.5)));
    boxes.push(boxOf(lonely, lonely.map((_, i) => ({ x: (i % cols) * CELL_W, y: Math.floor(i / cols) * CELL_H })), true));
  }

  // 逐行摆放各簇。
  const area = boxes.reduce((s, b) => s + (b.w + GAP) * (b.h + GAP), 0);
  const rowWidth = Math.max(Math.sqrt(area * ROW_ASPECT), ...boxes.map((b) => b.w));
  let x = 0, y = 0, rowH = 0;
  for (const b of boxes) {
    if (x > 0 && x + b.w > rowWidth) { x = 0; y += rowH + GAP; rowH = 0; }
    b.ids.forEach((id, i) => out.set(id, { x: x + b.pts[i].x, y: y + b.pts[i].y, right: b.right[i] }));
    x += b.w + GAP;
    rowH = Math.max(rowH, b.h);
  }

  // 整体居中到原点。
  const all = [...out.values()];
  const cx = (Math.min(...all.map((p) => p.x)) + Math.max(...all.map((p) => p.x))) / 2;
  const cy = (Math.min(...all.map((p) => p.y)) + Math.max(...all.map((p) => p.y))) / 2;
  for (const [id, p] of out) out.set(id, { ...p, x: p.x - cx, y: p.y - cy });
  return out;
}
