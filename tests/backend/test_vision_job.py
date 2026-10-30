"""识图任务（2.1.0 P5）：只补缺失、写回描述与图块、致命错误整体停下、取消、测试识图模型。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from kacore.api import KnowledgeRepositoryApi
from kacore.api_vision import VisionUnavailableError
from kacore.config import Config, SourceStoreConfig
from kacore.domain.figures import CHUNK_KIND_FIGURE, FIGURE_DONE, FIGURE_FAILED, FIGURE_PENDING
from kacore.domain.llm_generation import STATUS_OK, GenerationResult
from kacore.managers.ingest_manager import IngestManager
from kacore.pipelines.figure_caption_pipeline import PROMPT_VERSION, FigureCaptionPipeline
from kacore.providers.base import (
    ERROR_NO_IMAGE_INPUT,
    ERROR_TIMEOUT,
    ProviderClient,
    ProviderError,
    ProviderProfile,
)
from kacore.providers.registry import ProviderRegistry
from kacore.providers.router import ModelRouter
from kacore.repository.source_store.memory import InMemorySourceDocumentStore
from kacore.settings.store import SettingsStore
from kacore.vision_job import VISION_CANCELLED, VISION_ERROR, VISION_PARTIAL, VISION_SUCCESS

fitz = pytest.importorskip("pymupdf")


class _VisionStub(ProviderClient):
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state

    async def generate_result(self, prompt, system_prompt="", *, allow_mock=True):
        raise AssertionError("text generation is not used")

    async def list_models(self) -> list[str]:
        return []

    async def ping(self, model: str) -> None:
        return None

    async def describe_image(self, prompt, image_png, system_prompt="", *, max_tokens=800):
        assert image_png.startswith(b"\x89PNG")
        self.state.setdefault("prompts", []).append(prompt)
        behaviour = self.state.get("behaviour", "ok")
        if isinstance(behaviour, list):
            behaviour = behaviour.pop(0) if behaviour else "ok"
        if behaviour == "fatal":
            raise ProviderError(ERROR_NO_IMAGE_INPUT, "text-only model rejected the image", 400)
        if behaviour == "timeout":
            raise ProviderError(ERROR_TIMEOUT, "Request timed out")
        if behaviour == "slow":
            await asyncio.sleep(0.2)
        answer = self.state.get("answer", "A red line chart that rises from 75 to 90.")
        return GenerationResult(text=answer, status=STATUS_OK, prompt_tokens=1000,
                                completion_tokens=50)


def _chart_page(doc: Any, number: int) -> None:
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(72, 80, 520, 200),
                        "Body text that explains the experiment in detail. " * 4, fontsize=10)
    box = fitz.Rect(120, 230, 420, 390)
    page.draw_rect(box)
    for i in range(1, 6):
        page.draw_line((box.x0, box.y0 + i * 26), (box.x1, box.y0 + i * 26), color=(0.8, 0.8, 0.8))
    page.insert_textbox(fitz.Rect(72, 412, 520, 440), f"Figure {number}: Accuracy curve {number}.",
                        fontsize=9)


async def _setup(tmp_path: Path, state: dict[str, Any], *, enabled: bool = True,
                 assign: bool = True, pages: int = 2) -> tuple[KnowledgeRepositoryApi, Any, str]:
    store = InMemorySourceDocumentStore()
    manager = IngestManager(source_store=store, config=SourceStoreConfig(), data_dir=tmp_path)
    doc = fitz.open()
    for n in range(1, pages + 1):
        _chart_page(doc, n)
    pdf = tmp_path / "paper.pdf"
    doc.save(pdf)
    doc.close()
    doc_id = await manager.ingest(title="paper", file_path=str(pdf),
                                  content_type="application/pdf",
                                  size_bytes=pdf.stat().st_size, collection="papers")
    settings = SettingsStore.in_memory()

    def factory(profile: ProviderProfile, key: str, model: str, timeout: int,
                retries: int) -> ProviderClient:
        state.setdefault("built", []).append((model, timeout, retries))
        return _VisionStub(state)

    registry = ProviderRegistry(settings, client_factory=factory)
    registry.create(label="VL", base_url="https://vl.example/v1", provider_id="vl", api_key="k")
    if assign:
        settings.set("flow.models.vision", {"provider_id": "vl", "model": "qwen-vl"})
    api = KnowledgeRepositoryApi(
        source_store=store,
        ingest_manager=manager,
        config=Config({"vision": {"enabled": enabled, "max_concurrency": 2, "timeout_sec": 30}}),
        model_router=ModelRouter(settings, registry),
        figure_caption_pipeline=FigureCaptionPipeline(render_dpi=72),
    )
    return api, store, doc_id


async def _wait(api: KnowledgeRepositoryApi) -> dict[str, Any]:
    assert api._vision_task is not None
    await asyncio.wait_for(api._vision_task, 10)
    job = api.get_active_vision_job()
    assert job is not None
    return job


async def test_refuses_when_off_or_without_a_model(tmp_path: Path) -> None:
    api, _, _ = await _setup(tmp_path, {}, enabled=False)
    with pytest.raises(VisionUnavailableError, match="turn it on"):
        await api.start_vision_build(None)
    api, _, _ = await _setup(tmp_path / "b", {}, assign=False)
    with pytest.raises(VisionUnavailableError, match="Model assignment"):
        await api.start_vision_build(None)


async def test_describes_missing_figures_and_refreshes_figure_chunks(tmp_path: Path) -> None:
    state: dict[str, Any] = {}
    api, store, doc_id = await _setup(tmp_path, state)
    status = await api.get_vision_status("papers")
    assert (status["missing"], status["counts"]) == (2, {FIGURE_PENDING: 2})
    assert status["estimated_prompt_tokens"] > 0

    await api.start_vision_build("papers")
    job = await _wait(api)

    assert (job["status"], job["described"], job["total_figures"]) == (VISION_SUCCESS, 2, 2)
    assert (job["prompt_tokens"], job["completion_tokens"]) == (2000, 100)
    assert state["built"][0] == ("qwen-vl", 30, 1)
    assert any("Caption: Figure 1: Accuracy curve 1." in p for p in state["prompts"])
    figures = await store.list_document_figures(doc_id)
    assert {f.status for f in figures} == {FIGURE_DONE}
    assert figures[0].model and figures[0].prompt_version == PROMPT_VERSION
    figure_chunks = [c for c in await store.list_chunks(doc_id)
                     if c.metadata.get("chunk_kind") == CHUNK_KIND_FIGURE]
    assert len(figure_chunks) == 2
    assert "Figure description: A red line chart" in figure_chunks[0].text
    doc = await store.get_document(doc_id)
    assert doc is not None and doc.needs_reindex

    # 再跑一次：没有缺失的图，立即成功、不调用模型。
    calls = len(state["prompts"])
    await api.start_vision_build("papers")
    again = await _wait(api)
    assert (again["status"], again["total_figures"]) == (VISION_SUCCESS, 0)
    assert len(state["prompts"]) == calls


async def test_fatal_error_stops_the_job_and_leaves_figures_pending(tmp_path: Path) -> None:
    state: dict[str, Any] = {"behaviour": "fatal"}
    api, store, doc_id = await _setup(tmp_path, state)

    await api.start_vision_build(None)
    job = await _wait(api)

    assert job["status"] == VISION_ERROR
    assert "rejected the image" in job["recent_error"]
    assert {f.status for f in await store.list_document_figures(doc_id)} == {FIGURE_PENDING}


async def test_transient_failure_is_retried_on_the_next_run(tmp_path: Path) -> None:
    state: dict[str, Any] = {"behaviour": ["timeout", "ok"]}
    api, store, doc_id = await _setup(tmp_path, state)
    api._config = Config({"vision": {"enabled": True, "max_concurrency": 1}})

    await api.start_vision_build(None)
    job = await _wait(api)
    assert (job["status"], job["described"], job["failed"]) == (VISION_PARTIAL, 1, 1)
    statuses = sorted(f.status for f in await store.list_document_figures(doc_id))
    assert statuses == [FIGURE_DONE, FIGURE_FAILED]

    await api.start_vision_build(None)
    retry = await _wait(api)
    assert (retry["status"], retry["total_figures"], retry["described"]) == (VISION_SUCCESS, 1, 1)


async def test_cancel_stops_before_new_requests(tmp_path: Path) -> None:
    state: dict[str, Any] = {"behaviour": "slow"}
    api, store, doc_id = await _setup(tmp_path, state, pages=4)
    api._config = Config({"vision": {"enabled": True, "max_concurrency": 1}})

    await api.start_vision_build(None)
    await asyncio.sleep(0.05)
    await api.cancel_vision_build()
    job = await _wait(api)

    assert job["status"] == VISION_CANCELLED
    assert job["described"] < 4
    done = [f for f in await store.list_document_figures(doc_id) if f.status == FIGURE_DONE]
    assert len(done) == job["described"]


@pytest.mark.parametrize(("answer", "status"), [("Red.", "ok"), ("Blue", "warning")])
async def test_vision_model_test(tmp_path: Path, answer: str, status: str) -> None:
    api, _, _ = await _setup(tmp_path, {"answer": answer})

    result = await api.test_vision_model()

    assert result["status"] == status and result["model"]
