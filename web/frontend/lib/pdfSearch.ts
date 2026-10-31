/**
 * PDF 阅读器的关键词查找（纯函数，无 DOM / pdf.js 依赖）。
 *
 * 输入是 pdf.js `getTextContent()` 逐项的字符串（与 TextLayer 的 `textContentItemsStr` 同序同长），
 * 输出是「页 + 文本项内偏移」形式的命中，供文字层逐项包高亮。
 *
 * 匹配规则：不区分大小写；查询里的连续空白可匹配任意空白（含换行处补的一个空格），
 * 因此跨行的词组也能找到。不做模糊匹配、不忽略变音符号。
 */

/** 一页的可检索文本：各文本项首尾相接，带 EOL 的项后补一个空格（不属于任何项）。 */
export type PdfPageText = {
  page: number;
  text: string;
  /** itemStarts[i] = 第 i 项在 text 中的起点；补的空格不计入任何项。 */
  itemStarts: number[];
  itemLengths: number[];
};

/** 文本项内的一段命中区间（左闭右开）。 */
export type PdfItemRange = { item: number; start: number; end: number };

/** 一次命中：可能跨多个文本项。 */
export type PdfMatch = { page: number; ranges: PdfItemRange[] };

export type PdfTextItem = { str?: string; hasEOL?: boolean };

/** 把一页的文本项拼成可检索文本；跳过 markedContent 等没有 str 的项（与 TextLayer 一致）。 */
export function buildPageText(page: number, items: PdfTextItem[]): PdfPageText {
  let text = "";
  const itemStarts: number[] = [];
  const itemLengths: number[] = [];
  for (const item of items) {
    if (typeof item.str !== "string") continue;
    itemStarts.push(text.length);
    itemLengths.push(item.str.length);
    text += item.str;
    if (item.hasEOL) text += " ";
  }
  return { page, text, itemStarts, itemLengths };
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** 查询 → 正则；空白折叠为 `\s+`。空查询返回 null。 */
export function queryPattern(query: string): RegExp | null {
  const words = query.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return null;
  return new RegExp(words.map(escapeRegExp).join("\\s+"), "giu");
}

/** text 中 [start, end) 映射回各文本项的局部区间；落在补的空格上的部分丢弃。 */
function toItemRanges(pageText: PdfPageText, start: number, end: number): PdfItemRange[] {
  const ranges: PdfItemRange[] = [];
  const { itemStarts, itemLengths } = pageText;
  for (let i = 0; i < itemStarts.length; i += 1) {
    const itemStart = itemStarts[i];
    const itemEnd = itemStart + itemLengths[i];
    if (itemEnd <= start) continue;
    if (itemStart >= end) break;
    const from = Math.max(start, itemStart) - itemStart;
    const to = Math.min(end, itemEnd) - itemStart;
    if (to > from) ranges.push({ item: i, start: from, end: to });
  }
  return ranges;
}

/** 按页序、页内出现顺序返回全部命中。 */
export function findMatches(pages: PdfPageText[], query: string): PdfMatch[] {
  const pattern = queryPattern(query);
  if (!pattern) return [];
  const matches: PdfMatch[] = [];
  for (const pageText of pages) {
    pattern.lastIndex = 0;
    for (const hit of pageText.text.matchAll(pattern)) {
      if (!hit[0]) continue;
      const start = hit.index ?? 0;
      const ranges = toItemRanges(pageText, start, start + hit[0].length);
      if (ranges.length > 0) matches.push({ page: pageText.page, ranges });
    }
  }
  return matches;
}

/** 文字层全为空（扫描件、没有文字层）时查找无从谈起，界面据此提示。 */
export function hasSearchableText(pages: PdfPageText[]): boolean {
  return pages.some((page) => page.text.trim().length > 0);
}
