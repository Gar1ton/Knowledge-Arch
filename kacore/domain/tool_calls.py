"""联网工具、工具调用与外部引用的数据类型，以及可追溯核验（domain 层·纯数据与纯函数，零依赖）。

为何存在：3.0.0 起每个联网功能都是一个形状照 MCP 的工具（名字、描述、参数格式、返回内容块），
由 distributor（用户配置的研究模型）按问题挑工具调用；AI 助手经 skill 也调用同一批工具。
核心代码只认识本文件的类型，不认识任何具体来源——来源的 HTTP、限速、额度、字段转换都关在
`kacore/adapters/tools/` 的各个工具文件里。

外部引用怎么核验（用户 11-22 选「可追溯核验」）：模型用 `cite` 工具登记一条引用时，必须指明段落取自
哪次调用；`trace_check` 逐字核对段落出现在那次调用的某一块里、书目字段出现在本次运行
某次调用的原文里。
只做格式规整（NFKC、合并空白、忽略大小写），不判断含义，也不判断文献真伪。
"""
from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from kacore.domain.llm_generation import GenerationResult

# ── 工具声明（照 MCP 的 tool 定义）─────────────────────────

SETTING_SECRET = "secret"
SETTING_STR = "str"
SETTING_INT = "int"
SETTING_KINDS = (SETTING_SECRET, SETTING_STR, SETTING_INT)


@dataclass(frozen=True)
class ToolSetting:
    """工具自己声明的一项设置；设置页与工具面板据此生成，核心代码不写任何工具名。

    required=True 的设置没填时，这个工具不提供给模型（工具面板标「缺设置」）。
    """

    name: str
    kind: str = SETTING_STR
    default: object = None
    required: bool = False


@dataclass(frozen=True)
class ToolSpec:
    """name 只含小写字母、数字与下划线（供应商对工具名的共同限制）；description 与 input_schema
    原样交给模型，所以写给模型看（英文）。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    settings: tuple[ToolSetting, ...] = ()
    default_enabled: bool = True


# ── 调用结果（照 MCP 的 text content）──────────────────────


@dataclass(frozen=True)
class Block:
    """一块文本。locator 是块在原文里的位置说明（如 "page 5"），page 是原文页码（从 1 起，
    没有页的为 None），都由工具给出；引用的位置与页码取自这里，不由模型填。"""

    text: str
    locator: str = ""
    page: int | None = None


@dataclass
class ToolResult:
    """一次调用的返回。is_error=True 时 blocks 是给模型看的错误说明。"""

    blocks: list[Block]
    is_error: bool = False

    @classmethod
    def error(cls, message: str) -> ToolResult:
        return cls([Block(message)], is_error=True)


def render_blocks(blocks: Sequence[Block]) -> str:
    """交给模型的文本：每块带序号与位置，模型据此在 cite 里指明出处。"""
    parts = []
    for i, block in enumerate(blocks, start=1):
        where = f" ({block.locator})" if block.locator else ""
        parts.append(f"[block {i}{where}]\n{block.text}")
    return "\n\n".join(parts) if parts else "(no content)"


def clip_blocks(blocks: Sequence[Block], max_chars: int) -> list[Block]:
    """按顺序保留块，总字数到 max_chars 为止（最后一块截断）。账本存的就是模型看到的这些。"""
    out: list[Block] = []
    room = max_chars
    for block in blocks:
        if room <= 0:
            break
        text = block.text if len(block.text) <= room else block.text[:room]
        out.append(Block(text, block.locator, block.page))
        room -= len(text)
    return out


@dataclass
class ToolCall:
    """账本里的一次调用（含 cite 本身：被拒绝的登记也留痕）。"""

    call_id: str
    capture_id: str
    ord: int
    tool: str
    arguments: dict[str, Any]
    blocks: list[Block]
    is_error: bool = False
    started_at: str = ""
    elapsed_ms: int = 0


# ── 与模型的对话轮次（供应商中立；各连接自己翻译成 API 形态）──


@dataclass(frozen=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class UserTurn:
    text: str


@dataclass(frozen=True)
class AssistantTurn:
    """raw 是该连接自己的原始助手消息（如思考块），回放时原样交回；其他连接忽略它。"""

    text: str
    calls: tuple[ToolCallRequest, ...] = ()
    raw: Any = None


@dataclass(frozen=True)
class ResultTurn:
    call_id: str
    text: str
    is_error: bool = False


Turn = UserTurn | AssistantTurn | ResultTurn


@dataclass(frozen=True)
class ToolTurn:
    """一次带工具的模型调用的结果：calls 为空表示模型不再调用工具。result 带文本、状态与用量。"""

    result: GenerationResult
    calls: tuple[ToolCallRequest, ...] = ()
    raw: Any = None

    @property
    def text(self) -> str:
        return self.result.text

    def as_turn(self) -> AssistantTurn:
        return AssistantTurn(self.result.text, self.calls, self.raw)


# ── 外部引用与可追溯核验 ────────────────────────────────────

CITE_TOOL = "cite"
CITE_FIELDS = ("title", "creators", "year", "venue", "identifier", "url")

ACTOR_UI = "ui"
ACTOR_AGENT = "agent"
ACTOR_TEST = "test"
ACTORS = (ACTOR_UI, ACTOR_AGENT, ACTOR_TEST)

CITE_SPEC = ToolSpec(
    name=CITE_TOOL,
    description=(
        "Register one external source you want to cite in the answer. Copy the passage and every "
        "bibliographic field exactly as they appear in earlier tool results; Knowledge Arch checks "
        "them word for word and rejects anything it cannot find. `call` is the id of the tool "
        "call whose result holds the passage. Fields you cannot copy from a result must be left "
        "out. Register each source once."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "call": {"type": "string", "description": "Tool call id that holds the passage."},
            "passage": {"type": "string",
                        "description": "The supporting text, copied verbatim (1-3 sentences)."},
            "title": {"type": "string"},
            "creators": {"type": "array", "items": {"type": "string"},
                         "description": "Author names as written in the result."},
            "year": {"type": "string"},
            "venue": {"type": "string"},
            "identifier": {"type": "string", "description": "DOI, ISBN, arXiv id or similar."},
            "url": {"type": "string"},
        },
        "required": ["call", "passage", "title"],
    },
)


@dataclass
class CiteRequest:
    call: str
    passage: str
    fields: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_arguments(cls, arguments: Mapping[str, Any]) -> CiteRequest:
        fields: dict[str, Any] = {}
        for name in CITE_FIELDS:
            value = arguments.get(name)
            if name == "creators":
                names = [str(v).strip() for v in value or [] if str(v).strip()] \
                    if isinstance(value, list) else []
                if names:
                    fields[name] = names
            elif value not in (None, "") and str(value).strip():
                fields[name] = str(value).strip()
        return cls(str(arguments.get("call") or "").strip(),
                   str(arguments.get("passage") or "").strip(), fields)

    def field_values(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for name, value in self.fields.items():
            if isinstance(value, list):
                out.extend((name, v) for v in value)
            else:
                out.append((name, value))
        return out


@dataclass(frozen=True)
class TraceCheck:
    """ok=False 时 missing 列出对不上的部分（"call" / "passage" / 字段名）。"""

    ok: bool
    call_id: str = ""
    block: int = -1
    missing: tuple[str, ...] = ()


# 排版变体，两边同样处理：弯引号 → 直引号，去掉 Markdown 强调的星号。真实模型冒烟（11-22）里
# 被拒的引用多半是这两种：原文 novel’s / “Turn to Affect” / **In**terviewing，
# 模型抄成直引号、去了星号。
_TYPOGRAPHY = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'",
                             "“": '"', "”": '"', "„": '"', "‟": '"', "*": None})


def normalize_text(text: str) -> str:
    """比较前的格式规整：NFKC、统一引号、去强调星号、合并空白、忽略大小写。"""
    folded = unicodedata.normalize("NFKC", text or "").translate(_TYPOGRAPHY)
    return " ".join(folded.casefold().split())


def trace_check(cite: CiteRequest, calls: Mapping[str, ToolCall]) -> TraceCheck:
    """段落必须逐字出现在 cite.call 的某一块里；位置取那一块的 locator，不由模型填。
    每个书目字段都必须出现在本次运行某次非 cite 调用的原文里。"""
    source = calls.get(cite.call)
    if source is None or source.is_error or source.tool == CITE_TOOL:
        return TraceCheck(False, missing=("call",))
    passage = normalize_text(cite.passage)
    block = next((i for i, b in enumerate(source.blocks)
                  if passage and passage in normalize_text(b.text)), None)
    if block is None:
        return TraceCheck(False, call_id=source.call_id, missing=("passage",))
    seen = "\n".join(normalize_text(b.text) for c in calls.values()
                     if c.tool != CITE_TOOL and not c.is_error for b in c.blocks)
    missing = tuple(dict.fromkeys(name for name, value in cite.field_values()
                                  if normalize_text(value) not in seen))
    if missing:
        return TraceCheck(False, call_id=source.call_id, block=block, missing=missing)
    return TraceCheck(True, source.call_id, block)


# ── 账本记录 ────────────────────────────────────────────────


@dataclass
class Capture:
    """一次运行（一个回答的联网补充，或助手 / 工具面板的一组调用）。"""

    capture_id: str
    conv_id: str
    msg_idx: int
    question: str
    actor: str = ACTOR_UI
    captured_at: str = ""


@dataclass
class Citation:
    """一条通过核验的外部引用。ord 从 1 起，即来源栏的 E1、E2…；cited = 回答正文用到了它。"""

    capture_id: str
    ord: int
    call_id: str
    block: int
    fields: dict[str, Any]
    passage: str
    locator: str = ""
    page: int | None = None
    cited: bool = False
    placement: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "ACTORS",
    "ACTOR_AGENT",
    "ACTOR_TEST",
    "ACTOR_UI",
    "CITE_FIELDS",
    "CITE_SPEC",
    "CITE_TOOL",
    "SETTING_INT",
    "SETTING_KINDS",
    "SETTING_SECRET",
    "SETTING_STR",
    "AssistantTurn",
    "Block",
    "Capture",
    "Citation",
    "CiteRequest",
    "ResultTurn",
    "ToolCall",
    "ToolCallRequest",
    "ToolResult",
    "ToolSetting",
    "ToolSpec",
    "ToolTurn",
    "TraceCheck",
    "Turn",
    "UserTurn",
    "clip_blocks",
    "normalize_text",
    "render_blocks",
    "trace_check",
]
