"use client";
import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { getConsoleScopeState, upsertConsoleScopeState } from "@/lib/api";

/** 主窗口的页面：工作台（三栏）或数据流。 */
export type ConsoleView = "workspace" | "flow";
/** 偏好设置窗口的分组。 */
export type PrefsGroup =
  | "general" | "appearance" | "storage" | "local_models" | "components" | "diagnostics";

interface HighlightedChunk {
  docId: string;
  chunkId: string;
}

interface ConsoleContextValue {
  selectedCollection: string | null;
  setSelectedCollection: (c: string | null) => void;

  selectedDocId: string | null;
  setSelectedDocId: (id: string | null) => void;

  highlightedChunk: HighlightedChunk | null;
  setHighlightedChunk: (h: HighlightedChunk | null) => void;

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
        payload: highlightedChunk ? { highlighted_chunk: highlightedChunk } : {},
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
        highlightedChunk, setHighlightedChunk,
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
