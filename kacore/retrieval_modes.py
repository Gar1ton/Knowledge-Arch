"""召回模式公共常量、能力表与兼容规范化（core 横切支撑）。

后端 API、外部 agent 证据接口与前端共同消费这些值，避免公开模式名在多入口漂移。
v0.30.1 将 ``high_precision`` 更名为语义准确的 ``graph_mixed``；旧输入仅兼容一版，
所有内部控制流与响应统一使用新值。

3.0.0-preview.2（P34）起每个模式的规则写在 `MODES` 能力表里，是唯一真相源：问答入口的校验、
「这个模式能不能联网补充」、前端对话栏的可选项都从这张表读，不再各处判断模式名。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

MODE_DEFAULT = "default"
MODE_ENHANCED = "enhanced"
MODE_GRAPH_MIXED = "graph_mixed"
MODE_GRAPH_ONLY = "graph_only"
MODE_DEEP_THINKING = "deep_thinking"
MODE_FULLTEXT = "fulltext"

LEGACY_MODE_HIGH_PRECISION = "high_precision"


@dataclass(frozen=True)
class ModeSpec:
    """一个检索模式对外承诺的能力（只描述规则，不含实现）。

    needs_collection：必须选一个具体集合（图谱按集合建 workspace）。
    needs_doc：必须指定一篇文档（全文模式读整篇）。
    single_doc：能否用 doc_id 把检索限定在一篇（needs_doc 的模式恒为 True）。
    uses_graph：要先确认这个集合的知识图谱已就绪。
    translates_query：打开「英文检索」时是否把问题译成英文再召回（不做召回的模式不译）。
    supports_external：能否联网补充——外部引用要接在库内来源编号后面，没有编号的模式做不了。
    """

    id: str
    needs_collection: bool = False
    needs_doc: bool = False
    single_doc: bool = True
    uses_graph: bool = False
    translates_query: bool = True
    supports_external: bool = True

    def public(self) -> dict[str, object]:
        """给前端 / skill 的能力描述（`/api/capabilities` 的 `ask_modes`）。"""
        return asdict(self)


# 顺序即对外列出的顺序（错误提示、能力表）。
MODES: dict[str, ModeSpec] = {
    spec.id: spec
    for spec in (
        ModeSpec(MODE_DEFAULT),
        ModeSpec(MODE_ENHANCED),
        ModeSpec(MODE_GRAPH_MIXED, needs_collection=True, single_doc=False, uses_graph=True),
        ModeSpec(MODE_GRAPH_ONLY, needs_collection=True, single_doc=False, uses_graph=True,
                 supports_external=False),
        ModeSpec(MODE_DEEP_THINKING),
        ModeSpec(MODE_FULLTEXT, needs_doc=True, translates_query=False, supports_external=False),
    )
}

VALID_RETRIEVAL_MODES = frozenset(MODES)
# 不走联网补充的模式（由能力表推出）；开关开着时结果带 external.reason = "mode"。
EXTERNAL_SKIPPED_MODES = frozenset(m for m, spec in MODES.items() if not spec.supports_external)
# 全文检索的**确认阈值，不是上限**——超过它只要求调用方显式确认一次，确认后照常读完整篇。
# 全项目唯一真相源：HTTP 层与前端/skill 一律从响应里学这个数字，不得各自复制字面量。
FULLTEXT_CONFIRM_THRESHOLD_CHARS = 60_000


def normalize_retrieval_mode(value: str) -> tuple[str, bool]:
    """返回 ``(规范模式, 是否命中旧别名)``；不识别的值原样返回供调用方校验。"""
    mode = str(value or MODE_DEFAULT).strip() or MODE_DEFAULT
    if mode == LEGACY_MODE_HIGH_PRECISION:
        return MODE_GRAPH_MIXED, True
    return mode, False


def invalid_mode_message() -> str:
    """未知模式的错误提示，按能力表顺序列出全部合法值。"""
    names = [f"'{m}'" for m in MODES]
    return f"retrieval_mode must be {', '.join(names[:-1])}, or {names[-1]}"


__all__ = [
    "EXTERNAL_SKIPPED_MODES",
    "FULLTEXT_CONFIRM_THRESHOLD_CHARS",
    "LEGACY_MODE_HIGH_PRECISION",
    "MODES",
    "MODE_DEEP_THINKING",
    "MODE_DEFAULT",
    "MODE_ENHANCED",
    "MODE_FULLTEXT",
    "MODE_GRAPH_MIXED",
    "MODE_GRAPH_ONLY",
    "VALID_RETRIEVAL_MODES",
    "ModeSpec",
    "invalid_mode_message",
    "normalize_retrieval_mode",
]
