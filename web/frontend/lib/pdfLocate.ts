/**
 * 把一个分块（引用来源）定位到 PDF 文字层上（纯函数，无 DOM / pdf.js 依赖）。
 *
 * 为什么不能直接查找：分块文字是清洗过的 Markdown（断行连字符已合并、标题加了标记、
 * 图内文字挪走），和 PDF 文字层不会逐字相同。这里两边都只保留字母与数字再比较，
 * 用分块开头和结尾的一小段作锚点，在分块所在的页（前后各放宽一页）里找，命中后把
 * 起点锚到终点锚之间整段（可跨页）映射回文本项区间，交给文字层高亮。
 *
 * 只认分块自身文字，不做模糊打分；两端锚点都找不到时返回 null，由界面退回「跳到页」。
 */

import { toItemRanges, type PdfMatch, type PdfPageText } from "./pdfSearch";

/** 锚点长度（规范化后的字符数，约 8 个英文词）：够区分，又不至于跨过太多清洗差异。 */
const ANCHOR_CHARS = 40;
/** 一端最多试几个锚点（每次向分块内部挪一个锚点长度）。 */
const ANCHOR_TRIES = 6;
/** 命中区间长度超过分块长度的这个倍数，视为两端锚到了不同位置，只留起点一端。 */
const MAX_SPAN_RATIO = 3;

const KEEP = /[\p{L}\p{N}]/u;

/** 规范化：NFKC（拆连字）、小写、只留字母数字；`map[i]` 是第 i 个规范化字符在原文中的位置。 */
export function normalizeForLocate(text: string): { norm: string; map: number[] } {
  let norm = "";
  const map: number[] = [];
  for (let i = 0; i < text.length; i += 1) {
    const folded = text[i].normalize("NFKC").toLowerCase();
    for (const ch of folded) {
      if (KEEP.test(ch)) {
        norm += ch;
        map.push(i);
      }
    }
  }
  return { norm, map };
}

/** 候选页：分块记录的页码前后各放宽一页；没有页码时全部页。 */
export function candidatePages(pages: number[] | undefined, pageCount: number): number[] {
  const known = (pages ?? []).filter((p) => Number.isInteger(p) && p >= 1 && p <= pageCount);
  if (known.length === 0) return Array.from({ length: pageCount }, (_, i) => i + 1);
  const lo = Math.max(1, Math.min(...known) - 1);
  const hi = Math.min(pageCount, Math.max(...known) + 1);
  return Array.from({ length: hi - lo + 1 }, (_, i) => lo + i);
}

type Stream = { norm: string; page: number[]; offset: number[] };

/** 各候选页规范化后首尾相接；记下每个字符来自哪一页、在该页原文中的位置。 */
function buildStream(pages: PdfPageText[]): Stream {
  let norm = "";
  const page: number[] = [];
  const offset: number[] = [];
  for (const pageText of pages) {
    const { norm: n, map } = normalizeForLocate(pageText.text);
    norm += n;
    for (let i = 0; i < n.length; i += 1) {
      page.push(pageText.page);
      offset.push(map[i]);
    }
  }
  return { norm, page, offset };
}

/** 从分块开头（或结尾）依次取锚点，返回第一个命中的「流位置 + 锚点在分块里的位置」。 */
function findAnchor(
  stream: string,
  chunk: string,
  fromEnd: boolean,
  searchFrom: number,
): { at: number; chunkAt: number } | null {
  const size = Math.min(ANCHOR_CHARS, chunk.length);
  if (size < 12) return null;
  for (let k = 0; k < ANCHOR_TRIES; k += 1) {
    const chunkAt = fromEnd ? chunk.length - size - k * size : k * size;
    if (chunkAt < 0 || chunkAt + size > chunk.length) break;
    const at = stream.indexOf(chunk.slice(chunkAt, chunkAt + size), searchFrom);
    if (at >= 0) return { at, chunkAt };
  }
  return null;
}

/** 从锚点起点逐字往前比，直到两边对不上（分块开头的标题、清洗差异处就此停住，不越界高亮）。 */
function extendBack(stream: string, chunk: string, at: number, chunkAt: number): number {
  let d = 0;
  while (d < chunkAt && at - d - 1 >= 0 && stream[at - d - 1] === chunk[chunkAt - d - 1]) d += 1;
  return at - d;
}

/** 从锚点起点逐字往后比，返回对得上的那段终点（左闭右开）。 */
function extendForward(stream: string, chunk: string, at: number, chunkAt: number): number {
  let d = 0;
  while (chunkAt + d < chunk.length && at + d < stream.length && stream[at + d] === chunk[chunkAt + d]) d += 1;
  return at + d;
}

/**
 * 在候选页文本里定位分块，返回按页拆开的高亮区间（第一个即起点）；定位不到返回 null。
 *
 * 起点从起点锚逐字往前扩、终点从终点锚逐字往后扩，扩到两边对不上为止；只有一端命中时两头都从它扩。
 * 中间有清洗差异不影响：区间取起点到终点整段。
 */
export function locateChunk(pages: PdfPageText[], chunkText: string): PdfMatch[] | null {
  const chunk = normalizeForLocate(chunkText).norm;
  if (!chunk) return null;
  const stream = buildStream(pages);
  if (!stream.norm) return null;

  const head = findAnchor(stream.norm, chunk, false, 0);
  const tail = findAnchor(stream.norm, chunk, true, head ? head.at : 0);
  let start: number;
  let end: number;
  if (head && tail) {
    start = extendBack(stream.norm, chunk, head.at, head.chunkAt);
    end = extendForward(stream.norm, chunk, tail.at, tail.chunkAt);
    if (end <= start || end - start > chunk.length * MAX_SPAN_RATIO) {
      end = extendForward(stream.norm, chunk, head.at, head.chunkAt);
    }
  } else if (head) {
    start = extendBack(stream.norm, chunk, head.at, head.chunkAt);
    end = extendForward(stream.norm, chunk, head.at, head.chunkAt);
  } else if (tail) {
    start = extendBack(stream.norm, chunk, tail.at, tail.chunkAt);
    end = extendForward(stream.norm, chunk, tail.at, tail.chunkAt);
  } else {
    return null;
  }
  end = Math.min(end, stream.norm.length);
  if (end <= start) return null;
  return toPageMatches(pages, stream, start, end);
}

/** 流区间 [start, end) → 每页一个 PdfMatch（原文区间取该页首尾字符的原位置）。 */
function toPageMatches(pages: PdfPageText[], stream: Stream, start: number, end: number): PdfMatch[] | null {
  const matches: PdfMatch[] = [];
  let i = start;
  while (i < end) {
    const pageNo = stream.page[i];
    let j = i;
    while (j + 1 < end && stream.page[j + 1] === pageNo) j += 1;
    const pageText = pages.find((p) => p.page === pageNo);
    if (pageText) {
      const ranges = toItemRanges(pageText, stream.offset[i], stream.offset[j] + 1);
      if (ranges.length > 0) matches.push({ page: pageNo, ranges });
    }
    i = j + 1;
  }
  return matches.length > 0 ? matches : null;
}

/** MD 视图里按文字找回分块（引用的 chunk_id 因重切换了修订号时用）：返回起点锚所在的分块下标。 */
export function findChunkByText(chunkTexts: string[], targetText: string): number {
  const target = normalizeForLocate(targetText).norm;
  if (target.length < 12) return -1;
  const anchor = target.slice(0, Math.min(ANCHOR_CHARS, target.length));
  return chunkTexts.findIndex((text) => normalizeForLocate(text).norm.includes(anchor));
}
