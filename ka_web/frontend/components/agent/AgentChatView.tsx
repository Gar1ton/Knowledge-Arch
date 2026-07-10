"use client";

/**
 * 对话 Agent 全屏视图（独立版）：复现 AstrBot 下的使用模式——
 * 自然语言对话（RAG 注入 + research/notion 工具）与 /ka 运营命令共用一个输入框。
 * 美术与登录页一致（半调城市背景 + 大写报头）；右上角可一键切换到控制台画布。
 */

import React, { useEffect, useRef, useState } from "react";
import { useHalftoneCanvas } from "@/components/auth/LoginScreen";
import loginStyles from "@/components/auth/LoginScreen.module.css";
import {
  AgentChatEvent,
  ApiError,
  ChatMessage,
  clearChatHistory,
  getChatHistory,
  streamAgentChat,
} from "@/lib/api";

const AGENT_CONV_KEY = "kr_agent_conversation_id";

function getOrCreateAgentConvId(): string {
  if (typeof window === "undefined") return `agent-${Date.now()}`;
  const stored = localStorage.getItem(AGENT_CONV_KEY);
  if (stored) return stored;
  const id = `agent-${crypto.randomUUID()}`;
  localStorage.setItem(AGENT_CONV_KEY, id);
  return id;
}

interface AgentMessage {
  role: "user" | "assistant" | "stage";
  content: string;
}

const SUGGESTIONS = ["/ka help", "/ka status", "/ka research on", "/ka agent on"];

const S: Record<string, React.CSSProperties> = {
  column: {
    position: "relative",
    zIndex: 1,
    width: "min(760px, calc(100vw - 32px))",
    height: "100vh",
    margin: "0 auto",
    display: "flex",
    flexDirection: "column",
    padding: "28px 0 22px",
    boxSizing: "border-box",
  },
  card: {
    flex: 1,
    minHeight: 0,
    display: "flex",
    flexDirection: "column",
    background: "rgba(12,12,12,.82)",
    border: "1px solid rgba(195,192,186,.22)",
    borderRadius: 14,
    boxShadow: "0 24px 80px rgba(0,0,0,.55)",
    overflow: "hidden",
    backdropFilter: "blur(6px)",
  },
  messages: { flex: 1, minHeight: 0, overflow: "auto", padding: "18px 18px 8px" },
  userBubble: {
    maxWidth: "84%",
    background: "#c3c0ba",
    color: "#111",
    borderRadius: "10px 10px 3px 10px",
    padding: "9px 13px",
    fontSize: 13,
    lineHeight: 1.65,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
  },
  assistantBubble: {
    maxWidth: "92%",
    background: "rgba(30,30,30,.92)",
    border: "1px solid rgba(195,192,186,.16)",
    color: "#e8e6e1",
    borderRadius: "10px 10px 10px 3px",
    padding: "10px 14px",
    fontSize: 13,
    lineHeight: 1.72,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
  },
  stageLine: {
    color: "rgba(195,192,186,.72)",
    fontSize: 11.5,
    fontStyle: "italic",
    padding: "2px 4px",
  },
  composer: {
    flexShrink: 0,
    borderTop: "1px solid rgba(195,192,186,.18)",
    padding: "10px 12px 12px",
    display: "flex",
    flexDirection: "column",
    gap: 8,
  },
  textarea: {
    width: "100%",
    resize: "none",
    border: "1px solid rgba(195,192,186,.25)",
    borderRadius: 10,
    outline: "none",
    background: "rgba(20,20,20,.9)",
    color: "#e8e6e1",
    fontSize: 13,
    lineHeight: 1.6,
    padding: "9px 12px",
    boxSizing: "border-box",
    fontFamily: "inherit",
  },
  chip: {
    fontSize: 11,
    color: "rgba(195,192,186,.85)",
    border: "1px solid rgba(195,192,186,.28)",
    borderRadius: 999,
    padding: "3px 10px",
    background: "transparent",
    cursor: "pointer",
  },
  headerBtn: {
    fontSize: 11,
    letterSpacing: ".14em",
    textTransform: "uppercase" as const,
    color: "#c3c0ba",
    border: "1px solid rgba(195,192,186,.35)",
    borderRadius: 999,
    padding: "6px 14px",
    background: "rgba(12,12,12,.6)",
    cursor: "pointer",
  },
  sendBtn: {
    fontSize: 12,
    fontWeight: 700,
    letterSpacing: ".08em",
    color: "#111",
    background: "#c3c0ba",
    border: "none",
    borderRadius: 999,
    padding: "7px 20px",
    cursor: "pointer",
  },
};

export function AgentChatView({ onEnterConsole }: { onEnterConsole: () => void }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  useHalftoneCanvas(canvasRef);

  const [conversationId, setConversationId] = useState<string>("");
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [streamingText, setStreamingText] = useState("");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const cid = getOrCreateAgentConvId();
    setConversationId(cid);
    getChatHistory(cid)
      .then((rows: ChatMessage[]) =>
        setMessages(
          rows
            .filter((m) => m.role === "user" || m.role === "assistant")
            .map((m) => ({ role: m.role as "user" | "assistant", content: m.content })),
        ),
      )
      .catch(() => {});
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, streamingText]);

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy || !conversationId) return;
    setInput("");
    setError("");
    setBusy(true);
    setMessages((prev) => [...prev, { role: "user", content: message }]);
    let sawAssistant = false;
    let liveDelta = "";
    try {
      await streamAgentChat(message, conversationId, (event: AgentChatEvent) => {
        if (event.type === "delta" && event.text) {
          liveDelta += event.text;
          setStreamingText(liveDelta);
        } else if (event.type === "stage" && event.text) {
          setMessages((prev) => [...prev, { role: "stage", content: event.text! }]);
        } else if (event.type === "assistant") {
          sawAssistant = true;
          liveDelta = "";
          setStreamingText("");
          const content = event.content ?? "";
          if (content) setMessages((prev) => [...prev, { role: "assistant", content }]);
        } else if (event.type === "error") {
          setError(event.message ?? "对话处理失败");
        }
      });
      if (!sawAssistant && liveDelta) {
        setMessages((prev) => [...prev, { role: "assistant", content: liveDelta }]);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "对话请求失败，请稍后重试");
    } finally {
      setStreamingText("");
      setBusy(false);
    }
  }

  function resetConversation() {
    if (conversationId) clearChatHistory(conversationId).catch(() => {});
    const id = `agent-${crypto.randomUUID()}`;
    localStorage.setItem(AGENT_CONV_KEY, id);
    setConversationId(id);
    setMessages([]);
    setStreamingText("");
    setError("");
  }

  return (
    <div className={loginStyles.screen}>
      <canvas ref={canvasRef} className={loginStyles.backdropCanvas} aria-hidden="true" />
      <div className={loginStyles.vignette} aria-hidden="true" />

      <div style={S.column}>
        {/* Masthead（与登录页同款报头）+ 控制台切换 */}
        <header
          className={loginStyles.masthead}
          style={{ display: "flex", alignItems: "baseline", gap: 16 }}
        >
          <div style={{ flex: 1 }}>
            <h1 className={loginStyles.title} style={{ margin: 0 }}>
              KNOWLEDGE ARCH
            </h1>
            <p className={loginStyles.subtitle} style={{ margin: "4px 0 0" }}>
              RESEARCH AGENT · DIALOGUE
            </p>
          </div>
          <button type="button" style={S.headerBtn} onClick={resetConversation}>
            新对话
          </button>
          <button type="button" style={S.headerBtn} onClick={onEnterConsole}>
            控制台 →
          </button>
        </header>
        <div className={loginStyles.rule} aria-hidden="true" style={{ margin: "14px 0" }} />

        {/* Chat card */}
        <section style={S.card} aria-label="Knowledge Arch agent chat">
          <div ref={scrollRef} style={S.messages}>
            {messages.length === 0 && !streamingText && (
              <div
                style={{
                  height: "100%",
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: 10,
                  color: "rgba(195,192,186,.6)",
                  fontSize: 12.5,
                  textAlign: "center",
                  padding: "0 40px",
                }}
              >
                <span style={{ fontSize: 26 }}>◈</span>
                <span>
                  与知识库对话：直接提问（自动探查范围并检索），或输入 /ka 命令进行运营控制。
                </span>
              </div>
            )}
            {messages.map((m, i) =>
              m.role === "stage" ? (
                <div key={i} style={{ ...S.stageLine, margin: "6px 0" }}>
                  {m.content}
                </div>
              ) : (
                <div
                  key={i}
                  style={{
                    display: "flex",
                    justifyContent: m.role === "user" ? "flex-end" : "flex-start",
                    marginBottom: 12,
                  }}
                >
                  <div style={m.role === "user" ? S.userBubble : S.assistantBubble}>
                    {m.content}
                  </div>
                </div>
              ),
            )}
            {streamingText && (
              <div style={{ display: "flex", justifyContent: "flex-start", marginBottom: 12 }}>
                <div style={S.assistantBubble}>{streamingText}▍</div>
              </div>
            )}
            {busy && !streamingText && (
              <div style={{ ...S.stageLine, margin: "6px 0" }}>思考中…</div>
            )}
            {error && (
              <div style={{ ...S.stageLine, color: "#d99", margin: "6px 0" }}>⚠ {error}</div>
            )}
          </div>

          {/* Composer */}
          <div style={S.composer}>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" style={S.chip} onClick={() => setInput(s)}>
                  {s}
                </button>
              ))}
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void send(input);
              }}
              style={{ display: "flex", gap: 8, alignItems: "flex-end" }}
            >
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void send(input);
                  }
                }}
                placeholder="向知识库提问，或输入 /ka 命令…（Enter 发送，Shift+Enter 换行）"
                rows={2}
                style={S.textarea}
              />
              <button type="submit" style={{ ...S.sendBtn, opacity: busy ? 0.5 : 1 }} disabled={busy}>
                发送
              </button>
            </form>
          </div>
        </section>

        <p className={loginStyles.footer} style={{ marginTop: 14 }}>
          KNOWLEDGE ARCH · STANDALONE
        </p>
      </div>
    </div>
  );
}
