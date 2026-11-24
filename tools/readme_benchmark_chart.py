"""生成 README「评测结果」的图（`assets/benchmark-zh.svg` / `-en.svg`）。

数据是 KA_Benchmark s01 的三批结果（KA `38446f4`，2026-11-17 – 11-18），
与实验总账 K2 / K3 / K4 一致；改数字只改下面的 SCENARIOS，然后运行：

    python tools/readme_benchmark_chart.py

图的做法：三个场景上下排成三个小图，共用 1–5 分的横轴（1 是评分最低分，即真实底线）；
每个档位一行横条，行名就是档位，所以只用一个颜色、不需要图例；显著的配对差加 *。
README 里的图不能交互，故为静态 SVG；自带亮 / 暗两套颜色（跟随系统的
prefers-color-scheme），并画了背景板，GitHub 主题与系统主题不一致时也看得清。
配色取自 dataviz 参考调色板（series-1 蓝与中性墨色）。
"""
from __future__ import annotations

from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "assets"

# (档位, 分数或 None=未评测, 相对 default 的配对差或 None, 差值是否显著)
SCENARIOS = [
    {"zh": "ResearchQA · 单篇范围", "en": "ResearchQA · single paper", "n": 1036,
     "rows": [("closed_book", 2.33, None, False), ("default", 4.66, None, False),
              ("enhanced", 4.65, -0.01, False), ("deep_thinking", 4.61, -0.05, False),
              ("fulltext", 4.87, 0.21, True)]},
    {"zh": "ResearchQA · 全库（494 篇论文）", "en": "ResearchQA · full library (494 papers)",
     "n": 1036,
     "rows": [("closed_book", None, None, False), ("default", 4.21, None, False),
              ("enhanced", 4.34, 0.13, True), ("deep_thinking", 4.25, 0.04, False),
              ("fulltext", None, None, False)]},
    {"zh": "MultiHop-RAG · 跨文档（609 篇新闻）",
     "en": "MultiHop-RAG · cross-document (609 articles)", "n": 852,
     "rows": [("closed_book", 2.68, None, False), ("default", 3.53, None, False),
              ("enhanced", 3.69, 0.16, True), ("deep_thinking", 3.57, 0.04, False),
              ("fulltext", None, None, False)]},
]

TEXT = {
    "zh": {"closed_book": "closed_book（不检索）", "fulltext": "fulltext（整篇）",
           "n": "{n:,} 题", "na": "未评测", "ref": "基准",
           "note1": "事实正确性，1–5 分（ResearchQA 官方评审提示词盲评）。",
           "note2": "括号内为相对 default 的同题配对差；* 表示 95% bootstrap 置信区间不跨 0。"},
    "en": {"closed_book": "closed_book (no retrieval)", "fulltext": "fulltext (whole paper)",
           "n": "{n:,} questions", "na": "not evaluated", "ref": "baseline",
           "note1": "Factual accuracy, 1–5 (blind judging with the official ResearchQA prompt).",
           "note2": "Brackets: paired per-question difference vs. default; "
                    "* = 95% bootstrap CI excludes 0."},
}

W = 760
LABEL_X = 196          # 行名右对齐处
X0, X1 = 214, 620      # 分数 1 与 5 的位置
ROW_H, BAR_H = 26, 14
HEAD_H, GAP = 34, 18
PAD = 20


def sx(score: float) -> float:
    return X0 + (score - 1) / 4 * (X1 - X0)


def bar(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """只有数据端（右端）是圆角，起点贴着底线是直角。"""
    r = min(r, w, h / 2)
    e = x + w
    return (f'<path class="bar" d="M{x:.1f},{y:.1f} H{e - r:.1f} '
            f'Q{e:.1f},{y:.1f} {e:.1f},{y + r:.1f} V{y + h - r:.1f} '
            f'Q{e:.1f},{y + h:.1f} {e - r:.1f},{y + h:.1f} H{x:.1f} Z"/>')


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def grid_lines(top: float, bottom: float) -> list[str]:
    """一个小图的竖向网格：只画在条形区域里，不穿过小图标题。"""
    return [f'<line class="{"axis" if v == 1 else "grid"}" x1="{sx(v):.1f}" y1="{top:.1f}" '
            f'x2="{sx(v):.1f}" y2="{bottom:.1f}"/>' for v in (1, 2, 3, 4, 5)]


def value_text(name: str, score: float, diff: float | None, sig: bool, t: dict[str, str]) -> str:
    cls = "value sig" if sig else "value"
    out = f'<tspan class="{cls}">{score:.2f}{"*" if sig else ""}</tspan>'
    if diff is not None:
        d = f"{diff:+.2f}".replace("-", "−") + ("*" if sig else "")
        out += f'<tspan class="{"diff sig" if sig else "diff"}" dx="6">({d})</tspan>'
    elif name == "default":
        out += f'<tspan class="diff" dx="6">{esc(t["ref"])}</tspan>'
    return out


def render(lang: str) -> str:
    t = TEXT[lang]
    grid: list[str] = []
    parts: list[str] = []
    y = PAD
    for sc in SCENARIOS:
        parts.append(f'<text class="head" x="{PAD}" y="{y + 18}">{esc(sc[lang])}</text>')
        parts.append(f'<text class="muted" x="{W - PAD}" y="{y + 18}" text-anchor="end">'
                     f'{esc(t["n"].format(n=sc["n"]))}</text>')
        y += HEAD_H
        grid += grid_lines(y - 2, y + ROW_H * len(sc["rows"]) + 2)
        for name, score, diff, sig in sc["rows"]:
            cy = y + ROW_H / 2
            parts.append(f'<text class="label" x="{LABEL_X}" y="{cy + 4:.1f}" text-anchor="end">'
                         f'{esc(t.get(name, name))}</text>')
            if score is None:
                parts.append(f'<text class="muted" x="{X0 + 6}" y="{cy + 4:.1f}">'
                             f'{esc(t["na"])}</text>')
            else:
                end = sx(score)
                parts.append(bar(X0, cy - BAR_H / 2, end - X0, BAR_H))
                parts.append(f'<text x="{end + 8:.1f}" y="{cy + 4:.1f}">'
                             f'{value_text(name, score, diff, sig, t)}</text>')
            y += ROW_H
        y += GAP
    axis_y = y - GAP + 18
    for v in (1, 2, 3, 4, 5):
        grid.append(f'<text class="muted" x="{sx(v):.1f}" y="{axis_y}" '
                    f'text-anchor="middle">{v}</text>')
    y = axis_y + 26
    notes = (f'<text class="muted" x="{PAD}" y="{y}">{esc(t["note1"])}</text>'
             f'<text class="muted" x="{PAD}" y="{y + 18}">{esc(t["note2"])}</text>')
    h = y + 18 + PAD
    style = """
  text { font-family: system-ui, -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei",
         "Noto Sans CJK SC", sans-serif; font-size: 12.5px; fill: #52514e; }
  .bg { fill: #fcfcfb; stroke: rgba(11,11,11,0.10); }
  .head { font-size: 13.5px; font-weight: 600; fill: #0b0b0b; }
  .label { fill: #52514e; }
  .muted { fill: #898781; font-size: 11.5px; }
  .value { fill: #0b0b0b; font-weight: 500; }
  .value.sig, .diff.sig { font-weight: 700; fill: #0b0b0b; }
  .diff { fill: #52514e; font-size: 11.5px; }
  .bar { fill: #2a78d6; }
  .grid { stroke: #e1e0d9; stroke-width: 1; }
  .axis { stroke: #c3c2b7; stroke-width: 1; }
  @media (prefers-color-scheme: dark) {
    text, .label { fill: #c3c2b7; }
    .bg { fill: #1a1a19; stroke: rgba(255,255,255,0.10); }
    .head, .value, .value.sig, .diff.sig { fill: #f0efec; }
    .diff { fill: #c3c2b7; }
    .muted { fill: #898781; }
    .bar { fill: #3987e5; }
    .grid { stroke: #2c2c2a; }
    .axis { stroke: #383835; }
  }"""
    title = ("评测结果：各检索档位的事实正确性" if lang == "zh"
             else "Benchmarks: factual accuracy by retrieval mode")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" '
            f'viewBox="0 0 {W} {h}" role="img" aria-label="{esc(title)}">\n'
            f'<title>{esc(title)}</title>\n<style>{style}\n</style>\n'
            f'<rect class="bg" x="0.5" y="0.5" width="{W - 1}" height="{h - 1}" rx="10"/>\n'
            + "\n".join(grid) + "\n" + "\n".join(parts) + "\n" + notes + "\n</svg>\n")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for lang in ("zh", "en"):
        (OUT / f"benchmark-{lang}.svg").write_text(render(lang), encoding="utf-8")
        print(f"wrote {OUT / f'benchmark-{lang}.svg'}")


if __name__ == "__main__":
    main()
