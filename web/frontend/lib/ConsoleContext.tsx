"use client";
import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { getConsoleScopeState, upsertConsoleScopeState } from "@/lib/api";

/** 主窗口的页面：工作台（三栏）或数据流。 */
export type ConsoleView = "workspace" | "flow";
/** 偏好设置窗口的分组。 */
export type PrefsGroup =
  | "general" | "appearance" | "storage" | "local_models" | "components" | "diagnostics";

/** 中间栏的阅读模式：PDF 原件或分块（MD）。全局一个，记在本机；非 PDF 文档总是分块。 */
export type ReadingMode = "md" | "pdf";

const READING_MODE_KEY = "ka-reading-mode";

function loadReadingMode(): ReadingMode {
  try {
    return localStorage.getItem(READING_MODE_KEY) === "md" ? "md" : "pdf";
  } catch {
    return "pdf";
  }
}

/**
 * 一次引用跳转的目标。除 id 外带上来源自身的文字、页码与图框：分块重切后 chunk_id 会换修订号，
 * 这些字段让 PDF 定位与 MD 回找不依赖 id。`nonce` 每次跳转都不同，重复点同一条也会重新定位。
 * `preferMd`：这一次在分块界面打开（PDF 里定位不到时的「在 MD 中查看该段」），不改全局模式。
 */
export interface CiteTarget {
  docId: string;
  chunkId: string;
  text?: string;
  pages?: number[];
  kind?: string;
  bbox?: number[] | null;
  nonce: number;
  preferMd?: boolean;
}

type HighlightedChunk = CiteTarget;

interface ConsoleContextValue {
  selectedCollection: string | null;
  setSelectedCollection: (c: string | null) => void;

  selectedDocId: string | null;
  setSelectedDocId: (id: string | null) => void;

  highlightedChunk: HighlightedChunk | null;
  setHighlightedChunk: (h: HighlightedChunk | null) => void;
  /** 跳到引用原文：打开该文档（不论左侧选的是哪个集合）并交给中间栏定位。 */
  jumpToCitation: (target: Omit<CiteTarget, "nonce">) => void;

  readingMode: ReadingMode;
  setReadingMode: (mode: ReadingMode) => void;

  noteDocId: string | null;
  setNoteDocId: (id: string | null) => void;

  view: ConsoleView;
  setView: (v: ConsoleView) => void;

  /** 打开中的偏好设置分组；null = 窗口关闭。 */
  prefsGroup: PrefsGroup | null;
  openPrefs: (group?: PrefsGroup) => void;
  closePrefs: () => void;
}

const ConsoleContext = createContext<ConsoleContextValue>({
  selectedCollection: null,
  setSelectedCollection: () => {},
  selectedDocId: null,
  setSelectedDocId: () => {},
  highlightedChunk: null,
  setHighlightedChunk: () => {},
  jumpToCitation: () => {},
  readingMode: "pdf",
  setReadingMode: () => {},
  noteDocId: null,
  setNoteDocId: () => {},
  view: "workspace",
  setView: () => {},
  prefsGroup: null,
  openPrefs: () => {},
  closePrefs: () => {},
});

export function ConsoleProvider({ children }: { children: React.ReactNode }) {
  const [selectedCollection, setSelectedCollection] = useState<string | null>(null);
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);
  const [highlightedChunk, setHighlightedChunk] = useState<HighlightedChunk | null>(null);
  const [readingMode, setReadingModeState] = useState<ReadingMode>(loadReadingMode);
  const citeNonceRef = useRef(0);
  const setReadingMode = useCallback((mode: ReadingMode) => {
    setReadingModeState(mode);
    try {
      localStorage.setItem(READING_MODE_KEY, mode);
    } catch {
      // 本机存储不可用（隐私模式等）：本次会话内仍然生效。
    }
  }, []);
  const jumpToCitation = useCallback((target: Omit<CiteTarget, "nonce">) => {
    citeNonceRef.current += 1;
    setSelectedDocId(target.docId);
    setHighlightedChunk({ ...target, nonce: citeNonceRef.current });
  }, []);
  const [noteDocId, setNoteDocId] = useState<string | null>(null);
  const [view, setView] = useState<ConsoleView>("workspace");
  const [prefsGroup, setPrefsGroup] = useState<PrefsGroup | null>(null);
  const openPrefs = useCallback((group: PrefsGroup = "general") => setPrefsGroup(group), []);
  const closePrefs = useCallback(() => setPrefsGroup(null), []);
  const hydratedRef = useRef(false);

  useEffect(() => {
    let alive = true;
    getConsoleScopeState("global", "console")
      .then((state) => {
        if (!alive) return;
        if (state) {
          setSelectedCollection(state.selected_collection || null);
          setSelectedDocId(state.selected_doc_id || null);
          setNoteDocId(state.note_doc_id || null);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (alive) hydratedRef.current = true;
      });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!hydratedRef.current || !selectedCollection) return;
    let alive = true;
    const timer = window.setTimeout(() => {
      getConsoleScopeState("collection", selectedCollection)
        .then((state) => {
          if (!alive || !state) return;
          if (state.selected_doc_id) setSelectedDocId(state.selected_doc_id);
          if (state.note_doc_id) setNoteDocId(state.note_doc_id);
        })
        .catch(() => {});
    }, 0);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [selectedCollection]);

  useEffect(() => {
    if (!hydratedRef.current || !selectedDocId) return;
    let alive = true;
    const timer = window.setTimeout(() => {
      getConsoleScopeState("document", selectedDocId)
        .then((state) => {
          if (!alive || !state) return;
          if (state.note_doc_id) setNoteDocId(state.note_doc_id);
        })
        .catch(() => {});
    }, 0);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [selectedDocId]);

  useEffect(() => {
    if (!hydratedRef.current) return;
    const timer = window.setTimeout(() => {
      const baseState = {
        selected_collection: selectedCollection ?? "",
        selected_doc_id: selectedDocId ?? "",
        note_doc_id: noteDocId ?? "",
        right_panel: noteDocId ? "notes" : selectedDocId ? "document" : selectedCollection ? "collection" : "",
        reading_mode: "",
        payload: highlightedChunk
          ? { highlighted_chunk: { docId: highlightedChunk.docId, chunkId: highlightedChunk.chunkId } }
          : {},
      };
      const writes = [
        upsertConsoleScopeState({ scope_type: "global", scope_key: "console", ...baseState }),
      ];
      if (selectedCollection) {
        writes.push(
          upsertConsoleScopeState({
            scope_type: "collection",
            scope_key: selectedCollection,
            ...baseState,
          }),
        );
      }
      if (selectedDocId) {
        writes.push(
          upsertConsoleScopeState({
            scope_type: "document",
            scope_key: selectedDocId,
            ...baseState,
          }),
        );
      }
      Promise.all(writes).catch(() => {});
    }, 450);
    return () => window.clearTimeout(timer);
  }, [selectedCollection, selectedDocId, noteDocId, highlightedChunk]);

  return (
    <ConsoleContext.Provider
      value={{
        selectedCollection, setSelectedCollection,
        selectedDocId, setSelectedDocId,
        highlightedChunk, setHighlightedChunk, jumpToCitation,
        readingMode, setReadingMode,
        noteDocId, setNoteDocId,
        view, setView,
        prefsGroup, openPrefs, closePrefs,
      }}
    >
      {children}
    </ConsoleContext.Provider>
  );
}

export function useConsole() {
  return useContext(ConsoleContext);
}
