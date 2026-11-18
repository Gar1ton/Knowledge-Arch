"use client";
/**
 * 集合相关的对话框：删除集合（输入名字确认）、新建集合 / 子集合、改名。
 * 从 FilePanel 拆出（文件大小红线），行为不变。
 */
import React, { useState } from "react";
import { createPortal } from "react-dom";
import { Z } from "@/lib/zLayers";
import { Icon } from "@/components/ds/Icon";
import { renameCollection, createCollection, Collection } from "@/lib/api";
import { useToast } from "@/components/ui/Toast";
import { useI18n } from "@/lib/i18n";

// ─── Delete Collection Confirmation Dialog ─────────────────────

interface DeleteDialogProps {
  collectionName: string;
  defaultCollectionName: string;
  onConfirm: () => void;
  onCancel: () => void;
  deleting: boolean;
}

export function DeleteCollectionDialog({ collectionName, defaultCollectionName, onConfirm, onCancel, deleting }: DeleteDialogProps) {
  const { t } = useI18n();
  const [inputValue, setInputValue] = useState("");
  const canConfirm = inputValue === collectionName && !deleting;
  const [hintBefore, hintAfter] = t("file_delete_input_hint").split("{name}");

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") onCancel();
    if (e.key === "Enter" && canConfirm) onConfirm();
  }

  if (typeof document === "undefined") return null;

  return createPortal(
    <div
      onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: Z.dialog,
        background: "rgba(22,23,26,.46)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        animation: "overlayIn .15s ease",
      }}
    >
      <div
        style={{
          background: "var(--surface)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-2xl)",
          boxShadow: "var(--shadow-pop)",
          width: 380,
          maxWidth: "calc(100vw - 32px)",
          padding: "22px 22px 18px",
          animation: "modalIn .18s cubic-bezier(.2,.7,.2,1)",
          display: "flex",
          flexDirection: "column",
          gap: 14,
        }}
      >
        {/* Header */}
        <div style={{ display: "flex", alignItems: "flex-start", gap: 11 }}>
          <span
            style={{
              width: 34,
              height: 34,
              borderRadius: "var(--radius-lg)",
              background: "var(--danger-soft)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
              color: "var(--danger)",
            }}
          >
            <Icon name="trash" size={16} />
          </span>
          <div>
            <div style={{ fontSize: 14, fontWeight: 700, color: "var(--heading)", lineHeight: 1.3 }}>
              {t("file_delete_title")}
            </div>
            <div style={{ fontSize: 12, color: "var(--fg-muted)", marginTop: 4, lineHeight: 1.5 }}>
              {t("file_delete_warning", { default: defaultCollectionName })}
            </div>
          </div>
        </div>

        {/* Name confirmation input */}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label
            style={{ fontSize: 12, fontWeight: 500, color: "var(--fg-muted)" }}
            htmlFor="delete-collection-input"
          >
            {hintBefore}<code style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              background: "var(--bg-inset)",
              padding: "1px 5px",
              borderRadius: "var(--radius-sm)",
              color: "var(--danger)",
            }}>{collectionName}</code>{hintAfter}
          </label>
          <input
            id="delete-collection-input"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={collectionName}
            autoFocus
            autoComplete="off"
            spellCheck={false}
            style={{
              borderColor: inputValue && inputValue !== collectionName ? "var(--danger)" : undefined,
            }}
          />
        </div>

        {/* Actions */}
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button
            type="button"
            onClick={onCancel}
            disabled={deleting}
            style={{
              background: "var(--bg-inset)",
              border: "1px solid var(--border-strong)",
              color: "var(--fg-muted)",
              borderRadius: "var(--radius-xl)",
              padding: "7px 16px",
              fontSize: 12.5,
              fontWeight: 500,
              cursor: "pointer",
              fontFamily: "inherit",
              transition: "background .12s",
            }}
          >
            {t("btn_cancel")}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={!canConfirm}
            style={{
              background: canConfirm ? "var(--danger)" : "var(--bg-inset)",
              border: "1px solid transparent",
              color: canConfirm ? "#fff" : "var(--fg-subtle)",
              borderRadius: "var(--radius-xl)",
              padding: "7px 16px",
              fontSize: 12.5,
              fontWeight: 600,
              cursor: canConfirm ? "pointer" : "not-allowed",
              fontFamily: "inherit",
              transition: "background .15s, color .15s",
              display: "flex",
              alignItems: "center",
              gap: 6,
            }}
          >
            {deleting ? (
              <>
                <span style={{
                  width: 10, height: 10, borderRadius: "50%",
                  border: "2px solid rgba(255,255,255,0.4)",
                  borderTopColor: "#fff",
                  animation: "spin .6s linear infinite",
                  display: "inline-block",
                }} />
                {t("file_delete_loading")}
              </>
            ) : t("docs_delete_collection_btn")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

// ─── New Collection Dialog ─────────────────────────────────────

interface NewCollectionDialogProps {
  onClose: () => void;
  onCreated: () => void;
  parentKey?: string;
  parentName?: string;
}

export function NewCollectionDialog({ onClose, onCreated, parentKey, parentName }: NewCollectionDialogProps) {
  const { t } = useI18n();
  const { toast } = useToast();
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [creating, setCreating] = useState(false);

  async function handleCreate() {
    if (!name.trim()) return;
    setCreating(true);
    try {
      await createCollection(name.trim(), desc.trim() || undefined, parentKey);
      toast(t("file_new_collection_success").replace("{name}", name.trim()), "ok");
      onCreated();
      onClose();
    } catch (err: unknown) {
      toast(t("file_new_collection_error").replace("{message}", err instanceof Error ? err.message : String(err)), "error");
    } finally {
      setCreating(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") onClose();
    if (e.key === "Enter" && name.trim() && !creating) handleCreate();
  }

  if (typeof document === "undefined") return null;

  return createPortal(
    <div
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      style={{
        position: "fixed", inset: 0, zIndex: Z.dialog,
        background: "rgba(22,23,26,.46)",
        display: "flex", alignItems: "center", justifyContent: "center",
        animation: "overlayIn .15s ease",
      }}
    >
      <div
        style={{
          background: "var(--surface)", border: "1px solid var(--border)",
          borderRadius: "var(--radius-2xl)", boxShadow: "var(--shadow-pop)",
          width: 360, maxWidth: "calc(100vw - 32px)",
          padding: "22px 22px 18px",
          animation: "modalIn .18s cubic-bezier(.2,.7,.2,1)",
          display: "flex", flexDirection: "column", gap: 14,
        }}
      >
        <div style={{ fontSize: 14, fontWeight: 700, color: "var(--heading)" }}>
          {t("file_new_collection_title")}
        </div>
        {parentName && (
          <div style={{ fontSize: 11.5, color: "var(--fg-subtle)" }}>
            ↳ {parentName}
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t("file_new_collection_name_placeholder")}
            autoFocus
            autoComplete="off"
            spellCheck={false}
          />
          <input
            value={desc}
            onChange={(e) => setDesc(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t("file_new_collection_desc_placeholder")}
            autoComplete="off"
          />
        </div>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button
            type="button" onClick={onClose} disabled={creating}
            style={{
              background: "var(--bg-inset)", border: "1px solid var(--border-strong)",
              color: "var(--fg-muted)", borderRadius: "var(--radius-xl)",
              padding: "7px 16px", fontSize: 12.5, fontWeight: 500,
              cursor: "pointer", fontFamily: "inherit",
            }}
          >
            {t("btn_cancel")}
          </button>
          <button
            type="button" onClick={handleCreate} disabled={!name.trim() || creating}
            style={{
              background: name.trim() && !creating ? "var(--accent)" : "var(--bg-inset)",
              border: "1px solid transparent",
              color: name.trim() && !creating ? "var(--accent-fg, #fff)" : "var(--fg-subtle)",
              borderRadius: "var(--radius-xl)", padding: "7px 16px",
              fontSize: 12.5, fontWeight: 600,
              cursor: name.trim() && !creating ? "pointer" : "not-allowed",
              fontFamily: "inherit",
              display: "flex", alignItems: "center", gap: 6,
            }}
          >
            {creating ? (
              <>
                <span style={{
                  width: 10, height: 10, borderRadius: "50%",
                  border: "2px solid rgba(255,255,255,0.4)", borderTopColor: "#fff",
                  animation: "spin .6s linear infinite", display: "inline-block",
                }} />
                {t("file_new_collection_creating")}
              </>
            ) : t("file_new_collection_create")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

// ─── Rename Collection Dialog ──────────────────────────────────

interface RenameCollectionDialogProps {
  collection: Collection;
  onClose: () => void;
  onRenamed: (newName: string) => void;
}

export function RenameCollectionDialog({ collection, onClose, onRenamed }: RenameCollectionDialogProps) {
  const { t } = useI18n();
  const { toast } = useToast();
  const [name, setName] = useState(collection.name);
  const [saving, setSaving] = useState(false);

  async function handleSave() {
    const next = name.trim();
    if (!next || next === collection.name) { onClose(); return; }
    setSaving(true);
    try {
      await renameCollection(collection.coll_key || "", next);
      onRenamed(next);
      onClose();
    } catch (err: unknown) {
      toast(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setSaving(false);
    }
  }

  if (typeof document === "undefined") return null;
  return createPortal(
    <div
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      style={{
        position: "fixed", inset: 0, zIndex: Z.dialog, background: "rgba(22,23,26,.46)",
        display: "flex", alignItems: "center", justifyContent: "center", animation: "overlayIn .15s ease",
      }}
    >
      <div style={{
        background: "var(--surface)", border: "1px solid var(--border)",
        borderRadius: "var(--radius-2xl)", boxShadow: "var(--shadow-pop)",
        width: 360, maxWidth: "calc(100vw - 32px)", padding: "22px 22px 18px",
        display: "flex", flexDirection: "column", gap: 14,
      }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: "var(--heading)" }}>
          {t("file_rename_collection_title")}
        </div>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") onClose();
            if (e.key === "Enter" && !saving) handleSave();
          }}
          autoFocus
          autoComplete="off"
          spellCheck={false}
        />
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button type="button" onClick={onClose} disabled={saving} style={{
            background: "var(--bg-inset)", border: "1px solid var(--border-strong)",
            color: "var(--fg-muted)", borderRadius: "var(--radius-xl)",
            padding: "7px 16px", fontSize: 12.5, fontWeight: 500, cursor: "pointer", fontFamily: "inherit",
          }}>
            {t("btn_cancel")}
          </button>
          <button type="button" onClick={handleSave} disabled={!name.trim() || saving} style={{
            background: name.trim() && !saving ? "var(--accent)" : "var(--bg-inset)",
            border: "1px solid transparent",
            color: name.trim() && !saving ? "var(--accent-fg, #fff)" : "var(--fg-subtle)",
            borderRadius: "var(--radius-xl)", padding: "7px 16px", fontSize: 12.5, fontWeight: 600,
            cursor: name.trim() && !saving ? "pointer" : "not-allowed", fontFamily: "inherit",
          }}>
            {t("file_rename_collection_save")}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
