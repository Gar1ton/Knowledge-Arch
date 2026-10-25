"""回答生成相关的 API 子门面（mixin，组合进 KnowledgeRepositoryApi）。

目前只负责读取用户在数据流「回答」节点填写的系统提示（`flow.answer.system_prompt`，
即 `AskAgentConfig.system_prompt`）。每次调用现取，节点里改完立刻对下一次提问生效。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kacore.config import Config


class AnswerPromptMixin:
    _config: Config | None

    def _answer_system_prompt(self) -> str:
        """用户系统提示；未配置或为空时返回空串（ask 不叠加任何内容）。"""
        if self._config is None:
            return ""
        return self._config.get_ask_agent_config().system_prompt.strip()


__all__ = ["AnswerPromptMixin"]
