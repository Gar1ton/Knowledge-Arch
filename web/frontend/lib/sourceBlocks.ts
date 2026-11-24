/**
 * lib/sourceBlocks.ts — 来源栏用的纯函数（不依赖界面）。
 *
 * - parseRecordBlock：书目记录块 → 字段表。块是我们自己的检索工具按固定格式拼的
 *   （后端 `kacore/adapters/tools/base.py` 的 `record_block`：一行一个「Label: value」，空值的行省略），
 *   这里只按这个格式拆，不判断内容是什么；不是这种格式的块（如 PDF 段落）返回 null，按正文显示。
 * - collectionTree：集合列表 → 按父子顺序展开的树，路径写法与后端归类时一致
 *   （`kacore/api_external.py` 的 `_collection_outline`：集合名从根到叶用「 › 」连接），归类结果才能对上。
 * - findPassage：在一段文字里找到被引用的那句（忽略空白、大小写与直 / 弯引号的差别），用于高亮；
 *   找不到就返回 null，不猜位置。
 */

import type { Collection } from "@/lib/api";

export type RecordField = { label: string; value: string };
export type RecordBlock = { fields: RecordField[]; abstract: string };

// 「Label: value」：标签是我们工具里写死的英文短词（Title / Open-access PDF / Cited by …）。
const LINE = /^([A-Z][A-Za-z-]*(?: [A-Za-z-]+){0,3}): (.*)$/;

export function parseRecordBlock(text: string): RecordBlock | null {
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  if (lines.length < 2) return null;
  const fields: RecordField[] = [];
  let abstract = "";
  for (const line of lines) {
    const m = LINE.exec(line);
    if (!m) return null;
    if (m[1] === "Abstract") abstract = m[2];
    else fields.push({ label: m[1], value: m[2] });
  }
  return { fields, abstract };
}

/** 块的一行标题：书目记录取 Title，否则取正文开头。 */
export function blockTitle(text: string): string {
  const rec = parseRecordBlock(text);
  const title = rec?.fields.find((f) => f.label === "Title")?.value;
  return title || text.replace(/\s+/g, " ").trim().slice(0, 120);
}

export type CollectionNode = { key: string; name: string; path: string; depth: number };

export function collectionTree(collections: Collection[]): CollectionNode[] {
  const byKey = new Map<string, Collection>();
  for (const c of collections) if (c.coll_key) byKey.set(c.coll_key, c);
  const children = new Map<string, Collection[]>();
  for (const c of collections) {
    if (!c.coll_key) continue;
    const parent = c.parent_key && byKey.has(c.parent_key) ? c.parent_key : "";
    children.set(parent, [...(children.get(parent) ?? []), c]);
  }
  const out: CollectionNode[] = [];
  const seen = new Set<string>();
  const walk = (parent: string, prefix: string, depth: number) => {
    for (const c of children.get(parent) ?? []) {
      const key = c.coll_key as string;
      if (seen.has(key)) continue;
      seen.add(key);
      const path = prefix ? `${prefix} › ${c.name}` : c.name;
      out.push({ key, name: c.name, path, depth });
      walk(key, path, depth + 1);
    }
  };
  walk("", "", 0);
  return out;
}

const QUOTES: Record<string, string> = { "'": "['‘’]", "‘": "['‘’]", "’": "['‘’]", '"': '["“”]', "“": '["“”]', "”": '["“”]' };

function pattern(passage: string): RegExp | null {
  const words = passage.trim().split(/\s+/).filter(Boolean);
  if (!words.length) return null;
  const esc = (w: string) =>
    [...w].map((ch) => QUOTES[ch] ?? ch.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("");
  return new RegExp(words.map(esc).join("\\s+"), "i");
}

/** [前文, 命中, 后文]；找不到返回 null。 */
export function findPassage(text: string, passage: string): [string, string, string] | null {
  const re = pattern(passage);
  const m = re ? re.exec(text) : null;
  if (!m) return null;
  return [text.slice(0, m.index), m[0], text.slice(m.index + m[0].length)];
}
