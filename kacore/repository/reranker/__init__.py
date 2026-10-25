"""重排器子系统：接口 + 实现 + 工厂（repository 层）。

工厂 build_reranker 按 provider 选择实现：auto 优先本地 cross-encoder，依赖缺失或
模型加载失败自动回退 Noop；remote 调用连接上的 `/rerank`；noop 强制不重排。
repository 层不依赖 config——工厂只接原始参数，由组合根从 RerankConfig 解开传入
（构造器注入、不读全局单例）。
"""
from __future__ import annotations

import importlib.util
import logging

from kacore.repository.reranker.base import Reranker, ScoredChunk
from kacore.repository.reranker.noop import NoopReranker

logger = logging.getLogger("Reranker")

# ── provider 常量（杜绝魔法字面量）─────────────────────────────
PROVIDER_NOOP = "noop"
PROVIDER_CROSS_ENCODER = "cross_encoder"
PROVIDER_REMOTE = "remote"
DEFAULT_RERANK_MODEL = "Alibaba-NLP/gte-reranker-modernbert-base"


def _has_sentence_transformers() -> bool:
    """sentence-transformers 是否可用（仅探测，不导入，便于测试 monkeypatch）。"""
    return importlib.util.find_spec("sentence_transformers") is not None


def build_reranker(
    *,
    provider: str = PROVIDER_NOOP,
    model: str = DEFAULT_RERANK_MODEL,
    device: str = "auto",
    batch_size: int = 32,
    max_candidates: int = 30,
    base_url: str = "",
    api_key: str = "",
) -> Reranker:
    """构造重排器（默认 noop——不重排，零模型零部署）。

    仅当 provider=cross_encoder 时才懒加载本地 cross-encoder（首次使用下载模型）；缺
    sentence-transformers 则记 warning 回退 NoopReranker，保证不影响普通 ask。其余
    provider（含历史 auto/mmr）一律 noop——rerank 精度交由 deep thinking 的 verification 承担。
    provider=remote 时 base_url 与 model 来自「模型供应商」连接；缺任一项回退 noop。
    """
    if provider == PROVIDER_REMOTE:
        if not base_url or not model:
            logger.warning(
                "provider=remote but the connection address or model is missing; falling back to "
                "noop."
            )
            return NoopReranker()
        from kacore.repository.reranker.remote import RemoteReranker

        try:
            return RemoteReranker(
                base_url=base_url, model=model, api_key=api_key, max_candidates=max_candidates
            )
        except ValueError as exc:
            logger.warning((
                "provider=remote has an invalid connection address (%s); falling back to noop."
            ), exc)
            return NoopReranker()
    if provider != PROVIDER_CROSS_ENCODER:
        return NoopReranker()
    if not _has_sentence_transformers():
        logger.warning(
            "provider=cross_encoder but sentence-transformers is missing; falling back to noop."
        )
        return NoopReranker()
    from kacore.repository.reranker.bge_local import CrossEncoderReranker

    return CrossEncoderReranker(
        model=model, device=device, batch_size=batch_size, max_candidates=max_candidates
    )


__all__ = [
    "Reranker",
    "ScoredChunk",
    "NoopReranker",
    "build_reranker",
    "PROVIDER_NOOP",
    "PROVIDER_CROSS_ENCODER",
    "PROVIDER_REMOTE",
    "DEFAULT_RERANK_MODEL",
]
