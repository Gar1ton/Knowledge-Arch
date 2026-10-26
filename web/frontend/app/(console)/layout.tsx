"use client";

import React, { useEffect, useState, useCallback, useRef } from "react";
import { ToastProvider } from "@/components/ui/Toast";
import { I18nContext, Lang, makeT, useI18n } from "@/lib/i18n";
import { initColorTheme } from "@/lib/theme";
import { ConsoleProvider, useConsole } from "@/lib/ConsoleContext";
import { MENU_PREFERENCES_EVENT, onShellEvent, quitApp } from "@/lib/backend";
import { getSettingsSchema } from "@/lib/settingsApi";
import { setColorTheme, THEMES, type ThemeName, useTheme } from "@/lib/theme";
import { applyUiScale, UI_SCALE_KEY, useUiScaleShortcuts } from "@/lib/uiScale";
import { BackendGate } from "@/components/desktop/BackendGate";
import { FirstRunTour } from "@/components/desktop/FirstRunTour";
import { SecretRequestDialog } from "@/components/desktop/SecretRequestDialog";
import { WelcomeScreen, useWelcomeNeeded } from "@/components/desktop/WelcomeScreen";
import { LandingSplash } from "@/components/desktop/LandingShell";
import { PreferencesWindow } from "@/components/prefs/PreferencesWindow";
import { UpdateBanner } from "@/components/desktop/UpdateBanner";
import { FlowPageContent } from "@/components/panels/FlowPageContent";
import { ProgressDock } from "@/components/progress/ProgressDock";
import { TopBar } from "@/components/layout/TopBar";
import { FilePanel } from "@/components/panels/FilePanel";
import { DocumentsPanel } from "@/components/panels/DocumentsPanel";
import { ChatPanel } from "@/components/panels/ChatPanel";
import { NotePanel } from "@/components/panels/NotePanel";

// ─── I18n Provider ────────────────────────────────────────────

function I18nProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    if (typeof localStorage === "undefined") return "en";
    const saved = localStorage.getItem("kr-lang");
    return saved === "zh" ? "zh" : "en";
  });

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    if (typeof localStorage !== "undefined") localStorage.setItem("kr-lang", l);
  }, []);

  useEffect(() => {
    initColorTheme();
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  return (
    <I18nContext.Provider value={{ lang, setLang, t: makeT(lang) }}>
      {children}
    </I18nContext.Provider>
  );
}

// ─── Three-panel canvas ───────────────────────────────────────

const DEFAULT_CHAT_W = 360;
const MIN_CHAT_W = 280;
const DEFAULT_FILE_W = 264;
const MIN_FILE_W = 200;
const MAX_FILE_W = 528;

type DragTarget = "file" | "chat" | null;

function ConsoleCanvas() {
  const { noteDocId, selectedDocId, selectedCollection, view, setView, openPrefs } = useConsole();

  // 菜单 / 托盘 Preferences…（桌面壳）与 ⌘, / Ctrl+,（窗口内）都打开偏好设置。
  // Windows / Linux 没有菜单栏，Ctrl+Q 在这里退出（macOS 的 ⌘Q 由系统菜单处理）。
  useEffect(() => {
    let off: (() => void) | undefined;
    void onShellEvent(MENU_PREFERENCES_EVENT, () => openPrefs("general")).then((f) => { off = f; });
    const isMac = navigator.userAgent.includes("Mac");
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === ",") {
        e.preventDefault();
        openPrefs("general");
      } else if (!isMac && e.ctrlKey && !e.altKey && e.key.toLowerCase() === "q") {
        e.preventDefault();
        void quitApp();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => { off?.(); window.removeEventListener("keydown", onKey); };
  }, [openPrefs]);

  const [chatWidth, setChatWidth] = useState(DEFAULT_CHAT_W);
  const [fileWidth, setFileWidth] = useState(DEFAULT_FILE_W);
  const containerRef = useRef<HTMLDivElement>(null);
  const dragTarget = useRef<DragTarget>(null);
  const startX = useRef(0);
  const startChatWidth = useRef(DEFAULT_CHAT_W);
  const startFileWidth = useRef(DEFAULT_FILE_W);
  const [fileDividerHover, setFileDividerHover] = useState(false);
  const [chatDividerHover, setChatDividerHover] = useState(false);

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!dragTarget.current || !containerRef.current) return;
      if (dragTarget.current === "file") {
        const delta = e.clientX - startX.current;
        const next = Math.max(MIN_FILE_W, Math.min(MAX_FILE_W, startFileWidth.current + delta));
        setFileWidth(next);
      } else {
        // chat: dragging left increases chat width
        const totalPadGap = 40;
        const availableWidth = containerRef.current.clientWidth - fileWidth - totalPadGap;
        const maxChatWidth = availableWidth / 2;
        const delta = startX.current - e.clientX;
        const next = Math.max(MIN_CHAT_W, Math.min(maxChatWidth, startChatWidth.current + delta));
        setChatWidth(next);
      }
    };
    const onMouseUp = () => { dragTarget.current = null; };
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
    return () => {
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
    };
  }, [fileWidth]);

  const handleFileDragStart = useCallback((e: React.MouseEvent) => {
    dragTarget.current = "file";
    startX.current = e.clientX;
    startFileWidth.current = fileWidth;
    e.preventDefault();
  }, [fileWidth]);

  const handleChatDragStart = useCallback((e: React.MouseEvent) => {
    dragTarget.current = "chat";
    startX.current = e.clientX;
    startChatWidth.current = chatWidth;
    e.preventDefault();
  }, [chatWidth]);

  useEffect(() => {
    const isLightRAG = selectedCollection?.startsWith("lr:") ?? false;
    document.documentElement.dataset.mode = isLightRAG ? "lightrag" : "";
  }, [selectedCollection]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden" }}>
      <TopBar />
      <UpdateBanner />

      {view === "flow" ? (
        <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
          <FlowPageContent onClose={() => setView("workspace")} />
        </div>
      ) : (
      <div
        ref={containerRef}
        style={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          // 顶部也留出面板间距：工作台下顶栏不画分界线，面板浮在同一间距里。
          padding: "var(--panel-gap)",
          overflow: "hidden",
        }}
      >
        {/* Left: File OR Note */}
        <div
          style={{
            width: fileWidth,
            flexShrink: 0,
            display: "flex",
            flexDirection: "column",
            minHeight: 0,
          }}
        >
          {(selectedDocId || noteDocId) ? (
            <NotePanel docId={selectedDocId ?? noteDocId!} />
          ) : (
            <FilePanel />
          )}
        </div>

        {/* File drag handle */}
        <div
          onMouseDown={handleFileDragStart}
          onMouseEnter={() => setFileDividerHover(true)}
          onMouseLeave={() => setFileDividerHover(false)}
          style={{
            width: "var(--panel-gap)",
            flexShrink: 0,
            cursor: "col-resize",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            userSelect: "none",
          }}
        >
          <div
            style={{
              width: 2,
              height: 40,
              borderRadius: 1,
              background: fileDividerHover ? "var(--border-strong)" : "var(--border)",
              transition: "background .15s",
            }}
          />
        </div>

        {/* Middle: Documents */}
        <DocumentsPanel />

        {/* Chat drag handle */}
        <div
          onMouseDown={handleChatDragStart}
          onMouseEnter={() => setChatDividerHover(true)}
          onMouseLeave={() => setChatDividerHover(false)}
          style={{
            width: "var(--panel-gap)",
            flexShrink: 0,
            cursor: "col-resize",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            userSelect: "none",
          }}
        >
          <div
            style={{
              width: 2,
              height: 40,
              borderRadius: 1,
              background: chatDividerHover ? "var(--border-strong)" : "var(--border)",
              transition: "background .15s",
            }}
          />
        </div>

        {/* Right: Chat */}
        <ChatPanel width={chatWidth} />
      </div>
      )}

      <PreferencesWindow />
    </div>
  );
}

// ─── Console Shell（后端闸门 → 欢迎页 → 控制台）──────────────────

/** 界面偏好以后端 settings.json 为准（可随 R2 快照换机）：就绪后把已设置的值应用到本机界面。 */
function usePrefsFromBackend(epoch: number) {
  const { setLang } = useI18n();
  const { setTheme } = useTheme();
  useEffect(() => {
    let alive = true;
    getSettingsSchema("prefs")
      .then(({ items }) => {
        if (!alive) return;
        for (const item of items) {
          if (!item.is_set) continue;
          if (item.key === "prefs.general.language" && (item.value === "zh" || item.value === "en")) {
            setLang(item.value as Lang);
          } else if (item.key === "prefs.appearance.theme_mode" && typeof item.value === "string") {
            setTheme(item.value);
          } else if (
            item.key === "prefs.appearance.palette" &&
            (THEMES as readonly string[]).includes(String(item.value))
          ) {
            setColorTheme(item.value as ThemeName);
          } else if (item.key === UI_SCALE_KEY) {
            void applyUiScale(item.value).catch(() => undefined);
          }
        }
      })
      .catch(() => undefined);
    return () => { alive = false; };
  }, [epoch, setLang, setTheme]);
}

function ReadyShell({ epoch }: { epoch: number }) {
  const { t } = useI18n();
  const [welcomeNeeded, dismissWelcome] = useWelcomeNeeded(epoch);
  usePrefsFromBackend(epoch);

  if (welcomeNeeded === null) {
    return <LandingSplash subtitle={t("landing_sub_starting")} status={t("panel_loading")} />;
  }
  if (welcomeNeeded) {
    return (
      <>
        <WelcomeScreen onDone={dismissWelcome} />
        <SecretRequestDialog />
      </>
    );
  }
  return (
    <ConsoleProvider>
      <ConsoleCanvas />
      <ProgressDock />
      <SecretRequestDialog />
      <FirstRunTour />
    </ConsoleProvider>
  );
}

function ConsoleShell() {
  useUiScaleShortcuts();
  return <BackendGate>{(epoch) => <ReadyShell key={epoch} epoch={epoch} />}</BackendGate>;
}

// ─── Layout Export ────────────────────────────────────────────

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  void children;
  return (
    <ToastProvider>
      <I18nProvider>
        <ConsoleShell />
      </I18nProvider>
    </ToastProvider>
  );
}
