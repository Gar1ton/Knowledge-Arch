/**
 * 论文列表的纯函数：第二行文字、查找、排序、问题标记、按子集合分组。
 * 中栏的两行列表与阅读时左栏的一行列表共用；不依赖 React，便于单测（tests/frontend/test_doc_list.py）。
 */
import type { Collection, KrDocument } from "@/lib/api";

export type SortKey = "title" | "author" | "year" | "added";
export const SORT_KEYS: readonly SortKey[] = ["title", "author", "year", "added"];

/** 第二行：作者 · 年份。两者都缺时返回 null，由界面写「未识别作者」。 */
export function bylineOf(doc: KrDocument): string | null {
  const m = doc.zotero_meta;
  const creators = (m?.creators ?? []).filter((c) => c && c.trim());
  const who = creators.length > 2 ? `${creators[0]} et al.` : creators.join(", ");
  const parts = [who, m?.year?.trim()].filter((p): p is string => !!p);
  return parts.length ? parts.join(" · ") : null;
}

/** 用户输入的查找词：不分大小写，按子串匹配标题、文件名、作者、年份、标签。空词返回原列表。 */
export function filterDocs(docs: KrDocument[], query: string): KrDocument[] {
  const q = query.trim().toLowerCase();
  if (!q) return docs;
  return docs.filter((d) =>
    [d.title, d.filename, ...(d.zotero_meta?.creators ?? []), d.zotero_meta?.year, ...(d.tags ?? [])]
      .some((s) => typeof s === "string" && s.toLowerCase().includes(q)),
  );
}

function titleOf(doc: KrDocument): string {
  return (doc.title || doc.filename || "").trim();
}

function yearOf(doc: KrDocument): number | null {
  const n = parseInt(doc.zotero_meta?.year ?? "", 10);
  return Number.isFinite(n) ? n : null;
}

function firstAuthorOf(doc: KrDocument): string {
  return (doc.zotero_meta?.creators?.[0] ?? "").trim().toLowerCase();
}

const collator = typeof Intl !== "undefined"
  ? new Intl.Collator(undefined, { numeric: true, sensitivity: "base" })
  : null;
function cmpText(a: string, b: string): number {
  return collator ? collator.compare(a, b) : a < b ? -1 : a > b ? 1 : 0;
}

/**
 * 排序（不改原数组）。标题、作者按字母（数字按数值，「Chapter 2」在「Chapter 10」前）；
 * 年份新的在前；加入时间新的在前（后端按加入时间升序返回，这里倒过来）。缺作者 / 年份的排在最后。
 */
export function sortDocs(docs: KrDocument[], key: SortKey): KrDocument[] {
  const indexed = docs.map((doc, i) => ({ doc, i }));
  indexed.sort((a, b) => {
    if (key === "added") return b.i - a.i;
    if (key === "year") {
      const ya = yearOf(a.doc);
      const yb = yearOf(b.doc);
      if (ya === null || yb === null) {
        if (ya !== yb) return ya === null ? 1 : -1;
      } else if (ya !== yb) {
        return yb - ya;
      }
    } else if (key === "author") {
      const aa = firstAuthorOf(a.doc);
      const ab = firstAuthorOf(b.doc);
      if (!aa || !ab) {
        if (aa !== ab) return aa ? -1 : 1;
      } else {
        const c = cmpText(aa, ab);
        if (c !== 0) return c;
      }
    }
    const c = cmpText(titleOf(a.doc), titleOf(b.doc));
    return c !== 0 ? c : a.i - b.i;
  });
  return indexed.map((x) => x.doc);
}

export type DocIssue = "no_text" | "pending_index" | null;

/** 只有出问题的论文才有标记；字段都来自现有的 /api/documents。 */
export function docIssue(doc: KrDocument): DocIssue {
  if (doc.chunks !== undefined && doc.chunks === 0) return "no_text"; // 没切出文字：问答搜不到
  if (doc.needs_reindex || doc.milvus_covered === false) return "pending_index"; // 等重建向量
  return null;
}

export interface DocGroup {
  /** 子集合的 coll_key；根集合本级的论文用根自己的 key。 */
  key: string;
  /** 从根往下到这个子集合的名字（不含根）；本级为空数组。 */
  path: string[];
  docs: KrDocument[];
}

/** 集合树的先序：从 rootKey 开始，同级按名字排（与左栏的树同序）。 */
export function subtreeOrder(collections: Collection[], rootKey: string): string[] {
  const kids = new Map<string, Collection[]>();
  for (const c of collections) {
    const p = c.parent_key || "";
    if (!kids.has(p)) kids.set(p, []);
    kids.get(p)!.push(c);
  }
  for (const list of kids.values()) list.sort((a, b) => cmpText(a.name, b.name));
  const order: string[] = [];
  const seen = new Set<string>();
  const walk = (key: string) => {
    if (seen.has(key)) return;
    seen.add(key);
    order.push(key);
    for (const ch of kids.get(key) ?? []) if (ch.coll_key) walk(ch.coll_key);
  };
  walk(rootKey);
  return order;
}

/**
 * 父集合的列表按子集合分组。一篇论文只出现一次：落在树序最靠前的那个归属里；
 * 归属不在这棵子树里的（旧数据按名字列出来的）放进根本级。组内保持传入的顺序（先排序再分组）。
 */
export function groupBySubcollection(
  docs: KrDocument[],
  rootKey: string,
  collections: Collection[],
): DocGroup[] {
  const order = subtreeOrder(collections, rootKey);
  const pos = new Map(order.map((k, i) => [k, i]));
  const byKey = new Map(collections.map((c) => [c.coll_key ?? "", c]));
  const groups = new Map<string, KrDocument[]>();
  for (const d of docs) {
    const home = (d.collection_keys ?? [])
      .filter((k) => pos.has(k))
      .sort((a, b) => pos.get(a)! - pos.get(b)!)[0] ?? rootKey;
    if (!groups.has(home)) groups.set(home, []);
    groups.get(home)!.push(d);
  }
  const pathOf = (key: string): string[] => {
    const names: string[] = [];
    let node = byKey.get(key);
    const guard = new Set<string>();
    while (node && node.coll_key && node.coll_key !== rootKey && !guard.has(node.coll_key)) {
      guard.add(node.coll_key);
      names.unshift(node.name);
      node = byKey.get(node.parent_key ?? "");
    }
    return names;
  };
  return order
    .filter((k) => groups.has(k))
    .map((k) => ({ key: k, path: k === rootKey ? [] : pathOf(k), docs: groups.get(k)! }));
}
