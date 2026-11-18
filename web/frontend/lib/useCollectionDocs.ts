"use client";
/**
 * 选中集合的论文列表：中栏（两行）与阅读时左栏（一行）共用一份数据、一个查找词和排序。
 *
 * 列表与对话范围同一批：选中集合时取「集合 + 全部子集合」（`descendants`），父集合按子集合分组。
 * 数据放在模块级的小 store 里（useSyncExternalStore），两个组件同时挂着也只请求一次；
 * 上传等操作发出的 DOCUMENTS_CHANGED 事件触发重新加载，加载期间保留旧列表。
 */
import { useCallback, useEffect, useMemo, useSyncExternalStore } from "react";
import { listCollections, listDocuments, type Collection, type KrDocument } from "@/lib/api";
import { DOCUMENTS_CHANGED_EVENT } from "@/lib/documentEvents";
import {
  docIssue,
  filterDocs,
  groupBySubcollection,
  sortDocs,
  SORT_KEYS,
  type DocGroup,
  type SortKey,
} from "@/lib/docList";

const SORT_STORAGE_KEY = "ka-doc-sort";

interface ListState {
  /** 这份列表对应的选中键（带 z: / l: 前缀）；null = 全部文档。 */
  scopeKey: string | null;
  docs: KrDocument[];
  loading: boolean;
  failed: boolean;
}

interface StoreState {
  list: ListState;
  collections: Collection[];
  query: string;
  sort: SortKey;
  onlyIssues: boolean;
  /** 折叠的组（子集合 coll_key）。 */
  collapsed: Record<string, boolean>;
}

function loadSort(): SortKey {
  try {
    const saved = localStorage.getItem(SORT_STORAGE_KEY);
    return (SORT_KEYS as readonly string[]).includes(saved ?? "") ? (saved as SortKey) : "title";
  } catch {
    return "title";
  }
}

let state: StoreState = {
  list: { scopeKey: null, docs: [], loading: true, failed: false },
  collections: [],
  query: "",
  sort: "title",
  onlyIssues: false,
  collapsed: {},
};
let sortLoaded = false;
let started = false; // 已经发起过至少一次加载
let seq = 0;
const listeners = new Set<() => void>();

function setState(patch: Partial<StoreState>) {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot(): StoreState {
  return state;
}

/** 选中键的集合名（去掉 z: / l: / lr: 前缀）。 */
export function scopeName(scopeKey: string | null): string | undefined {
  return scopeKey ? scopeKey.replace(/^(z:|l:|lr:)/, "") : undefined;
}

/** 选中键对应的集合对象：名字相同时按前缀里的来源挑（与对话一样，最终按名字认第一个）。 */
export function collectionOfScope(
  scopeKey: string | null,
  collections: Collection[],
): Collection | undefined {
  const name = scopeName(scopeKey);
  if (!name) return undefined;
  const origin = scopeKey?.startsWith("z:") ? "zotero" : scopeKey?.startsWith("l:") ? "local" : null;
  const named = collections.filter((c) => c.name === name);
  return named.find((c) => !origin || (c.origin ?? "local") === origin) ?? named[0];
}

function load(scopeKey: string | null, force: boolean) {
  const cur = state.list;
  if (!force && started && cur.scopeKey === scopeKey) return;
  started = true;
  const id = ++seq;
  const sameScope = cur.scopeKey === scopeKey;
  setState({
    list: sameScope
      ? { ...cur, loading: cur.docs.length === 0 || cur.failed, failed: false }
      : { scopeKey, docs: [], loading: true, failed: false },
    // 换集合时清掉查找词与筛选：上一集合的词在新集合里多半什么都找不到。
    ...(sameScope ? {} : { query: "", onlyIssues: false }),
  });
  const name = scopeName(scopeKey);
  Promise.all([
    listDocuments(name ? { collection: name, descendants: true } : undefined),
    listCollections().catch(() => null),
  ])
    .then(([docs, collections]) => {
      if (id !== seq) return;
      setState({
        list: { scopeKey, docs, loading: false, failed: false },
        ...(collections ? { collections } : {}),
      });
    })
    .catch(() => {
      if (id !== seq) return;
      setState({ list: { scopeKey, docs: [], loading: false, failed: true } });
    });
}

let changeListenerInstalled = false;
function installChangeListener() {
  if (changeListenerInstalled || typeof window === "undefined") return;
  changeListenerInstalled = true;
  window.addEventListener(DOCUMENTS_CHANGED_EVENT, () => load(state.list.scopeKey, true));
}

export interface CollectionDocsModel {
  /** 未经查找、排序的全部论文（含子集合）。 */
  docs: KrDocument[];
  loading: boolean;
  failed: boolean;
  reload: () => void;
  collections: Collection[];
  /** 选中的集合；全部文档时为 undefined。 */
  root: Collection | undefined;
  /** 选中的集合有子集合时按子集合分组；否则只有一组。 */
  grouped: boolean;
  groups: DocGroup[];
  /** 查找、筛选、排序之后的顺序（上一篇 / 下一篇按它走）。 */
  ordered: KrDocument[];
  issueCount: number;
  query: string;
  setQuery: (q: string) => void;
  sort: SortKey;
  setSort: (s: SortKey) => void;
  onlyIssues: boolean;
  setOnlyIssues: (v: boolean) => void;
  collapsed: Record<string, boolean>;
  toggleGroup: (key: string) => void;
}

export function useCollectionDocs(scopeKey: string | null): CollectionDocsModel {
  const s = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  useEffect(() => {
    installChangeListener();
    if (!sortLoaded) {
      sortLoaded = true;
      setState({ sort: loadSort() });
    }
  }, []);

  useEffect(() => {
    load(scopeKey, false);
  }, [scopeKey]);

  const current: ListState =
    s.list.scopeKey === scopeKey ? s.list : { scopeKey, docs: [], loading: true, failed: false };

  const view = useMemo(() => {
    const root = collectionOfScope(scopeKey, s.collections);
    const issueCount = current.docs.filter((d) => docIssue(d) !== null).length;
    let shown = filterDocs(current.docs, s.query);
    if (s.onlyIssues) shown = shown.filter((d) => docIssue(d) !== null);
    shown = sortDocs(shown, s.sort);
    const hasChildren = !!root?.coll_key && s.collections.some((c) => c.parent_key === root.coll_key);
    const groups: DocGroup[] = hasChildren
      ? groupBySubcollection(shown, root!.coll_key!, s.collections)
      : [{ key: root?.coll_key ?? "", path: [], docs: shown }];
    return {
      root,
      grouped: hasChildren,
      groups,
      ordered: groups.flatMap((g) => g.docs),
      issueCount,
    };
  }, [scopeKey, current.docs, s.collections, s.query, s.onlyIssues, s.sort]);

  const reload = useCallback(() => load(scopeKey, true), [scopeKey]);
  const setQuery = useCallback((query: string) => setState({ query }), []);
  const setSort = useCallback((sort: SortKey) => {
    setState({ sort });
    try {
      localStorage.setItem(SORT_STORAGE_KEY, sort);
    } catch {
      // 本机存储不可用：本次会话内仍然生效。
    }
  }, []);
  const setOnlyIssues = useCallback((onlyIssues: boolean) => setState({ onlyIssues }), []);
  const toggleGroup = useCallback(
    (key: string) => setState({ collapsed: { ...state.collapsed, [key]: !state.collapsed[key] } }),
    [],
  );

  return {
    docs: current.docs,
    loading: current.loading,
    failed: current.failed,
    reload,
    collections: s.collections,
    ...view,
    query: s.query,
    setQuery,
    sort: s.sort,
    setSort,
    onlyIssues: s.onlyIssues,
    setOnlyIssues,
    collapsed: s.collapsed,
    toggleGroup,
  };
}
