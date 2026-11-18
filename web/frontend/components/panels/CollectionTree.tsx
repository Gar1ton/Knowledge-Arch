"use client";
/**
 * 左栏的集合树：「全部文档」+ Zotero 与本地两组，一棵树、每行含子集合的篇数。
 * 图谱只是行上的状态点（建好 / 正在建 / 中断），没建不画；集合操作收进行尾 ⋯ 菜单（右键同样打开）。
 */
import React, { useRef, useState } from "react";
import { Icon } from "@/components/ds/Icon";
import { IconButton } from "@/components/ds/IconButton";
import { Popover } from "@/components/ds/Popover";
import type { Collection } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { Z } from "@/lib/zLayers";

export type GraphState = "success" | "building" | "interrupted" | null;

export interface TreeMenuItem {
  key: string;
  icon: string;
  label: string;
  danger?: boolean;
  /** 功能未就绪：画成灰色，点击仍会触发（由调用方提示去哪里配置）。 */
  dimmed?: boolean;
  /** 在这一项之前画分隔线。 */
  separated?: boolean;
  onSelect: () => void;
}

// ─── Row ──────────────────────────────────────────────────────

interface RowProps {
  depth: number;
  label: string;
  active: boolean;
  count?: number;
  hasKids: boolean;
  open: boolean;
  onToggle: () => void;
  onClick: () => void;
  graph?: GraphState;
  menu?: TreeMenuItem[];
}

function GraphDot({ state }: { state: GraphState }) {
  const { t } = useI18n();
  if (!state) return null;
  const color = state === "success" ? "var(--ok)" : state === "building" ? "var(--ann-purple)" : "var(--warn)";
  const title =
    state === "success" ? t("file_graph_built")
    : state === "building" ? t("file_graph_building")
    : t("file_build_interrupted");
  return (
    <span
      title={title}
      style={{
        width: 6,
        height: 6,
        borderRadius: "50%",
        background: color,
        flexShrink: 0,
        animation: state === "building" ? "branchPulse 1.6s ease-in-out infinite" : undefined,
      }}
    />
  );
}

function Row({ depth, label, active, count, hasKids, open, onToggle, onClick, graph, menu }: RowProps) {
  const { t } = useI18n();
  const [hover, setHover] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLSpanElement>(null);
  const showMenuButton = !!menu?.length && (hover || active || menuOpen);
  return (
    <div
      onClick={onClick}
      onContextMenu={(e) => {
        if (!menu?.length) return;
        e.preventDefault();
        setMenuOpen(true);
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      title={label}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 6,
        minHeight: 26,
        padding: `3px 6px 3px ${6 + depth * 14}px`,
        margin: "1px 0",
        borderRadius: "var(--radius-md)",
        cursor: "pointer",
        userSelect: "none",
        backgroundColor: active ? "var(--select-bg)" : hover ? "var(--bg-inset)" : "rgba(0,0,0,0)",
        color: active ? "var(--select-fg)" : "var(--fg)",
        transition: "background-color .12s, color .12s",
      }}
    >
      <span
        onClick={(e) => {
          if (!hasKids) return;
          e.stopPropagation();
          onToggle();
        }}
        style={{ width: 14, display: "inline-flex", justifyContent: "center", flexShrink: 0 }}
      >
        {hasKids && (
          <Icon
            name="chevR"
            size={12}
            style={{
              transform: open ? "rotate(90deg)" : undefined,
              transition: "transform .15s",
              color: active ? "var(--select-muted)" : "var(--fg-subtle)",
            }}
          />
        )}
      </span>
      <span
        style={{
          flex: 1,
          minWidth: 0,
          fontSize: 12.5,
          fontWeight: active ? 600 : 450,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </span>
      <GraphDot state={graph ?? null} />
      {count != null && (
        <span
          style={{
            fontSize: 10.5,
            fontWeight: 600,
            fontFamily: "var(--font-mono)",
            color: active ? "var(--select-muted)" : "var(--fg-subtle)",
            flexShrink: 0,
          }}
        >
          {count}
        </span>
      )}
      {menu?.length ? (
        <span ref={menuRef} style={{ display: "inline-flex", flexShrink: 0, width: 20, justifyContent: "center" }}>
          {showMenuButton && (
            <IconButton
              name="dots"
              size={13}
              side="left"
              label={t("file_action_more")}
              active={menuOpen}
              onClick={(e) => { e.stopPropagation(); setMenuOpen((v) => !v); }}
              style={{ width: 20, height: 20, color: active ? "var(--select-fg)" : "var(--fg-muted)", background: "transparent" }}
            />
          )}
        </span>
      ) : null}
      {menu?.length ? (
        <Popover
          open={menuOpen}
          anchorRef={menuRef}
          onClose={() => setMenuOpen(false)}
          side="right"
          align="start"
          gap={6}
          zIndex={Z.dropdown}
          style={{
            minWidth: 168,
            padding: 4,
            background: "var(--surface)",
            border: "1px solid var(--border-strong)",
            borderRadius: "var(--radius-md)",
            boxShadow: "var(--shadow-pop)",
          }}
        >
          {/* 浮层经 portal 渲染，React 事件仍会冒泡到行上：拦下，免得点菜单空白处选中集合。 */}
          <div onClick={(e) => e.stopPropagation()} onContextMenu={(e) => e.stopPropagation()}>
          {menu.map((item) => (
            <React.Fragment key={item.key}>
              {item.separated && <div style={{ height: 1, background: "var(--border)", margin: "4px 2px" }} />}
              <MenuRow
                item={item}
                onPick={() => {
                  setMenuOpen(false);
                  item.onSelect();
                }}
              />
            </React.Fragment>
          ))}
          </div>
        </Popover>
      ) : null}
    </div>
  );
}

function MenuRow({ item, onPick }: { item: TreeMenuItem; onPick: () => void }) {
  const [hover, setHover] = useState(false);
  return (
    <div
      role="menuitem"
      onClick={(e) => { e.stopPropagation(); onPick(); }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "6px 10px",
        borderRadius: "var(--radius-sm)",
        cursor: "pointer",
        fontSize: 12.5,
        whiteSpace: "nowrap",
        opacity: item.dimmed ? 0.45 : 1,
        background: hover ? "var(--bg-inset)" : "transparent",
        color: item.danger ? "var(--danger)" : "var(--fg)",
      }}
    >
      <Icon name={item.icon} size={14} style={{ color: item.danger ? "var(--danger)" : "var(--fg-muted)", flexShrink: 0 }} />
      {item.label}
    </div>
  );
}

function SectionHead({ label, actions }: { label: string; actions?: React.ReactNode }) {
  const { lang } = useI18n();
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, padding: "10px 4px 3px 8px" }}>
      <span
        style={{
          flex: 1,
          fontSize: lang === "zh" ? 11 : 10.5,
          fontWeight: 700,
          letterSpacing: lang === "zh" ? 0 : ".07em",
          textTransform: lang === "zh" ? "none" : "uppercase",
          color: "var(--fg-subtle)",
        }}
      >
        {label}
      </span>
      {actions && <div style={{ display: "flex", gap: 1 }}>{actions}</div>}
    </div>
  );
}

// ─── Tree ─────────────────────────────────────────────────────

export interface CollectionTreeProps {
  collections: Collection[];
  /** 全库 active 论文数；未取到时为 null（不显示数字）。 */
  libraryTotal: number | null;
  /** 选中键：`z:名字` / `l:名字`；null = 全部文档。 */
  selectedCollection: string | null;
  onSelect: (key: string | null) => void;
  graphStateOf: (collectionName: string) => GraphState;
  menuFor: (c: Collection) => TreeMenuItem[];
  zoteroActions?: React.ReactNode;
  localActions?: React.ReactNode;
  emptyZoteroHint: string;
}

export function CollectionTree({
  collections,
  libraryTotal,
  selectedCollection,
  onSelect,
  graphStateOf,
  menuFor,
  zoteroActions,
  localActions,
  emptyZoteroHint,
}: CollectionTreeProps) {
  const { t } = useI18n();
  const [openKeys, setOpenKeys] = useState<Record<string, boolean>>({});

  const childrenByParent = React.useMemo(() => {
    const m: Record<string, Collection[]> = {};
    for (const c of collections) (m[c.parent_key || ""] ||= []).push(c);
    for (const k of Object.keys(m)) m[k].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    return m;
  }, [collections]);

  const prefixOf = (c: Collection) => (c.origin === "zotero" ? "z:" : "l:");
  const keyOf = (c: Collection) => `${prefixOf(c)}${c.name}`;

  // 选中的集合（含恢复上次的选择）在折叠的父集合里时，展开它的祖先，让它看得见。
  // 选择或集合列表变了才算一次（渲染期间调整，不放进 effect）。
  const revealKey = `${selectedCollection ?? ""}|${collections.length}`;
  const [revealed, setRevealed] = useState("");
  if (revealed !== revealKey) {
    setRevealed(revealKey);
    const byKey = new Map(collections.map((c) => [c.coll_key ?? "", c]));
    const sel = selectedCollection ? collections.find((c) => keyOf(c) === selectedCollection) : undefined;
    const ancestors: string[] = [];
    let p = sel ? byKey.get(sel.parent_key ?? "") : undefined;
    while (p?.coll_key && !ancestors.includes(p.coll_key)) {
      ancestors.push(p.coll_key);
      p = byKey.get(p.parent_key ?? "");
    }
    if (ancestors.some((k) => !openKeys[k])) {
      const next = { ...openKeys };
      for (const k of ancestors) next[k] = true;
      setOpenKeys(next);
    }
  }

  function roots(list: Collection[]): Collection[] {
    const keys = new Set(list.map((c) => c.coll_key || ""));
    return list
      .filter((c) => !c.parent_key || !keys.has(c.parent_key))
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  }

  function renderNode(c: Collection, depth: number): React.ReactNode {
    const nodeKey = c.coll_key || keyOf(c);
    const kids = childrenByParent[c.coll_key || ""] ?? [];
    const open = !!openKeys[nodeKey];
    const active = selectedCollection === keyOf(c);
    return (
      <div key={nodeKey}>
        <Row
          depth={depth}
          label={c.name}
          active={active}
          count={c.doc_count_total ?? c.doc_count}
          hasKids={kids.length > 0}
          open={open}
          onToggle={() => setOpenKeys((o) => ({ ...o, [nodeKey]: !o[nodeKey] }))}
          onClick={() => {
            onSelect(keyOf(c));
            if (kids.length && !open) setOpenKeys((o) => ({ ...o, [nodeKey]: true }));
          }}
          graph={c.is_system ? null : graphStateOf(c.name)}
          menu={c.is_system ? undefined : menuFor(c)}
        />
        {open && kids.map((ch) => renderNode(ch, depth + 1))}
      </div>
    );
  }

  const zotero = collections.filter((c) => c.origin === "zotero");
  const local = collections.filter((c) => c.origin !== "zotero");

  return (
    <div style={{ padding: "6px 8px 14px" }}>
      <Row
        depth={0}
        label={t("file_all_documents")}
        active={selectedCollection === null}
        count={libraryTotal ?? undefined}
        hasKids={false}
        open={false}
        onToggle={() => {}}
        onClick={() => onSelect(null)}
      />

      <SectionHead label={t("file_section_zotero")} actions={zoteroActions} />
      {roots(zotero).map((c) => renderNode(c, 0))}
      {zotero.length === 0 && (
        <div style={{ padding: "4px 10px 4px 26px", fontSize: 11, color: "var(--fg-subtle)" }}>{emptyZoteroHint}</div>
      )}

      <SectionHead label={t("file_section_local")} actions={localActions} />
      {roots(local).map((c) => renderNode(c, 0))}
    </div>
  );
}
