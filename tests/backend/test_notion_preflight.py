"""真实部署故障回归：token 无效或目标不兼容时停止整个批次，而非逐篇失败。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from notion_tools import notion_client

from kacore.adapters.notion_rest import NotionMCPError, NotionRestClient
from kacore.api import KnowledgeRepositoryApi
from kacore.config import NotionSyncConfig
from kacore.domain.models import (
    NOTION_ENTITY_DOCUMENT,
    NOTION_PUSH_SYNCED,
    NotionEntityRecord,
    NotionOutboxItem,
    SourceDocument,
)
from kacore.notion_sync_job import NOTION_STAGE_PREPARING, NotionSyncJob
from kacore.pipelines.notion_sync_pipeline import NotionSyncPipeline
from kacore.repository.source_store.memory import InMemorySourceDocumentStore
from kacore.repository.sync_targets.notion import NotionSyncTarget

_UNAUTHORIZED = NotionMCPError("Notion token 无效", code="unauthorized", status=401)


@pytest.mark.parametrize(
    "response, configured, code",
    [
        ({"object": "page", "properties": {}}, "", "incompatible_response"),
        ({"object": "page", "properties": {}}, "source", "incompatible_response"),
        ({"data_sources": None}, "", "invalid_response"),
        ({"data_sources": {}}, "", "invalid_response"),
        ({"data_sources": [{"id": 42}]}, "", "invalid_response"),
        ({"data_sources": [{}]}, "", "invalid_response"),
        ({"data_sources": []}, "", "no_data_source"),
        ({"data_sources": [{"id": "a"}, {"id": "b"}]}, "", "ambiguous_data_source"),
        ({"data_sources": [{"id": "a"}]}, "b", "data_source_mismatch"),
    ],
)
async def test_resolver_classifies_response(response, configured, code) -> None:
    adapter = notion_client(AsyncMock(return_value=response), rate_limit_rps=0)
    adapter.configure_data_sources({"container": configured})
    with pytest.raises(NotionMCPError) as error:
        await adapter.resolve_data_source("container")
    assert error.value.code == code


async def test_capability_check_requires_token_and_does_not_cache_failure() -> None:
    tokens = iter(["", "secret_ok"])
    transport = AsyncMock(return_value=(200, {}, {"object": "user", "id": "bot"}))
    adapter = NotionRestClient(lambda: next(tokens), transport=transport, rate_limit_rps=0)
    with pytest.raises(NotionMCPError) as error:
        await adapter.check_capabilities()
    assert error.value.code == "not_configured"
    assert "Notion" in str(error.value) and "token" in str(error.value)
    transport.assert_not_awaited()
    await adapter.check_capabilities()
    transport.assert_awaited_once()


async def test_capability_check_surfaces_invalid_token() -> None:
    adapter = notion_client(AsyncMock(), rate_limit_rps=0, users_me=_UNAUTHORIZED)
    with pytest.raises(NotionMCPError) as error:
        await adapter.check_capabilities()
    assert error.value.code == "unauthorized" and error.value.status == 401


@pytest.mark.parametrize("bad_token", [True, False])
async def test_106_document_batch_preflight_fails_once(bad_token: bool) -> None:
    store = InMemorySourceDocumentStore()
    for i in range(106):
        await store.add_document(
            SourceDocument(str(i), "Doc", "/doc.pdf", "pdf", 1, "hash", "default")
        )
    await store.add_notion_outbox(NotionOutboxItem(id="qa", content="保留正文"))
    caller = AsyncMock(return_value={"object": "page", "properties": {}})
    adapter = notion_client(
        caller, rate_limit_rps=0, users_me=_UNAUTHORIZED if bad_token else None
    )
    target = NotionSyncTarget(
        NotionSyncConfig(enabled=True, database_id="old-id", qa_database_id="qa"),
        store,
        adapter=adapter,
    )
    job = NotionSyncJob()
    result = await NotionSyncPipeline(store, target).push_all(progress=job)
    assert result["status"] == "error"
    assert result["error_code"] == ("unauthorized" if bad_token else "incompatible_response")
    assert job.docs_total == 106 and job.docs_processed == 0 and job.docs_failed == 0
    assert job.stage == NOTION_STAGE_PREPARING
    assert caller.await_count == (0 if bad_token else 1)
    assert await store.list_notion_entities() == []
    assert await store.list_sync_records() == []
    assert (await store.get_notion_outbox("qa")).content == "保留正文"


async def test_initialization_preflight_stops_before_creation() -> None:
    caller = AsyncMock()
    adapter = notion_client(caller, users_me=_UNAUTHORIZED)
    target = NotionSyncTarget(
        NotionSyncConfig(enabled=True, parent_page_id="parent"),
        InMemorySourceDocumentStore(),
        adapter=adapter,
    )
    with pytest.raises(NotionMCPError) as error:
        await target.initialize_databases()
    assert error.value.code == "unauthorized"
    caller.assert_not_awaited()


_NOT_FOUND = NotionMCPError("Could not find database", code="object_not_found", status=404)


def _adapter_with_missing(*missing: str):
    """resolve 对 missing 里的 id 报 404，其余返回 source-<id>；建库依次返回 new-*。"""

    async def resolve(database_id: str) -> str:
        if database_id in missing:
            raise _NOT_FOUND
        return f"source-{database_id}"

    adapter = notion_client(AsyncMock())
    adapter.resolve_data_source = AsyncMock(side_effect=resolve)
    adapter.create_database = AsyncMock(side_effect=["new-articles", "new-qa"])
    adapter.retrieve_data_source = AsyncMock(return_value={"properties": {}})
    adapter.update_database = AsyncMock()
    return adapter


@pytest.mark.parametrize("field, role", [("database_id", "Articles"), ("qa_database_id", "QA")])
async def test_push_preflight_404_identifies_target(field: str, role: str) -> None:
    adapter = _adapter_with_missing("missing-id")
    config = NotionSyncConfig(enabled=True, **{field: "missing-id"})
    target = NotionSyncTarget(config, InMemorySourceDocumentStore(), adapter=adapter)
    with pytest.raises(NotionMCPError) as error:
        await target.preflight()
    assert error.value.code == "object_not_found" and error.value.status == 404
    assert error.value.__cause__ is _NOT_FOUND
    assert f"Notion {role}" in str(error.value)
    assert f"notion_sync.{field}=missing-id" in str(error.value)
    assert "shared" in str(error.value) and "database setup" in str(error.value)


async def test_initialize_recreates_404_articles_database() -> None:
    """回归：404 后点「初始化数据库」不再被预检拦下，失效库在父页面下重建。"""
    adapter = _adapter_with_missing("gone-articles")
    config = NotionSyncConfig(
        enabled=True, parent_page_id="parent", database_id="gone-articles", qa_database_id="qa"
    )
    target = NotionSyncTarget(config, InMemorySourceDocumentStore(), adapter=adapter)
    result = await target.initialize_databases()
    assert result["status"] == "success"
    assert result["database_id"] == "new-articles" and result["qa_database_id"] == "qa"
    assert result["recreated_articles"] is True and result["recreated_qa"] is False
    assert result["created_articles"] is True and result["created_qa"] is False
    assert any("gone-articles" in w for w in result["warnings"])
    assert any("Citations" in w for w in result["warnings"])
    adapter.create_database.assert_awaited_once()
    assert adapter.create_database.await_args.kwargs["parent_page_id"] == "parent"
    assert target.config.database_id == "new-articles"
    assert target.config.data_source_id == "source-new-articles"


async def test_initialize_recreates_both_404_databases() -> None:
    adapter = _adapter_with_missing("gone-a", "gone-q")
    config = NotionSyncConfig(
        enabled=True, parent_page_id="parent", database_id="gone-a", qa_database_id="gone-q"
    )
    target = NotionSyncTarget(config, InMemorySourceDocumentStore(), adapter=adapter)
    result = await target.initialize_databases()
    assert result["status"] == "success"
    assert (result["database_id"], result["qa_database_id"]) == ("new-articles", "new-qa")
    assert result["recreated_articles"] and result["recreated_qa"]
    qa_props = adapter.create_database.await_args_list[1].kwargs["properties"]
    assert qa_props["Citations"]["relation"]["data_source_id"] == "source-new-articles"


async def test_initialize_404_without_parent_reports_without_writes() -> None:
    adapter = _adapter_with_missing("gone-articles")
    config = NotionSyncConfig(enabled=True, database_id="gone-articles")
    target = NotionSyncTarget(config, InMemorySourceDocumentStore(), adapter=adapter)
    result = await target.initialize_databases()
    assert result["status"] == "error" and result["error_code"] == "object_not_found"
    assert "Articles (gone-articles)" in result["message"] and "parent page" in result["message"]
    adapter.create_database.assert_not_awaited()
    adapter.update_database.assert_not_awaited()
    assert target.config.database_id == "gone-articles"


async def test_initialize_still_stops_on_non_404_errors() -> None:
    adapter = notion_client(AsyncMock())
    adapter.resolve_data_source = AsyncMock(side_effect=_UNAUTHORIZED)
    adapter.create_database = AsyncMock()
    config = NotionSyncConfig(enabled=True, parent_page_id="parent", database_id="db")
    target = NotionSyncTarget(config, InMemorySourceDocumentStore(), adapter=adapter)
    with pytest.raises(NotionMCPError) as error:
        await target.initialize_databases()
    assert error.value.code == "unauthorized"
    adapter.create_database.assert_not_awaited()


async def test_rebuilt_articles_database_gets_every_document_again() -> None:
    """重建后旧账本作废：否则指纹相同的文章全被跳过，新库永远是空的。"""
    store = InMemorySourceDocumentStore()
    await store.add_document(SourceDocument("d1", "Doc", "/doc.pdf", "pdf", 1, "h", ""))
    await store.upsert_notion_entity(
        NotionEntityRecord(
            entity_type=NOTION_ENTITY_DOCUMENT, entity_key="d1", page_id="old-page",
            metadata_hash="same", status=NOTION_PUSH_SYNCED,
        )
    )
    adapter = _adapter_with_missing("gone-articles")
    config = NotionSyncConfig(
        enabled=True, parent_page_id="parent", database_id="gone-articles", qa_database_id="qa"
    )
    pipeline = NotionSyncPipeline(store, NotionSyncTarget(config, store, adapter=adapter))
    result = await pipeline.initialize_databases()
    assert result["status"] == "success" and result["recreated_articles"]
    assert await store.list_notion_entities() == []


async def test_reconciling_existing_databases_keeps_the_ledger() -> None:
    store = InMemorySourceDocumentStore()
    record = NotionEntityRecord(
        entity_type=NOTION_ENTITY_DOCUMENT, entity_key="d1", page_id="p", status=NOTION_PUSH_SYNCED
    )
    await store.upsert_notion_entity(record)
    adapter = _adapter_with_missing()
    config = NotionSyncConfig(enabled=True, database_id="db", qa_database_id="qa")
    pipeline = NotionSyncPipeline(store, NotionSyncTarget(config, store, adapter=adapter))
    result = await pipeline.initialize_databases()
    assert result["status"] == "success" and not result["created"]
    assert await store.list_notion_entities() == [record]


async def test_background_job_exposes_one_preflight_error_without_document_failures() -> None:
    store = InMemorySourceDocumentStore()
    for i in range(106):
        await store.add_document(SourceDocument(str(i), "Doc", "/doc.pdf", "pdf", 1, "h", ""))
    adapter = notion_client(
        AsyncMock(return_value={"object": "page", "properties": {}}), rate_limit_rps=0
    )
    target = NotionSyncTarget(
        NotionSyncConfig(enabled=True, database_id="legacy"), store, adapter=adapter
    )
    api = KnowledgeRepositoryApi(source_store=store)
    api.attach_notion_sync_pipeline(NotionSyncPipeline(store, target))
    await api.sync_notion_push()
    await api._notion_sync_task
    job = api.get_active_notion_sync_job()
    assert job["status"] == "error" and len(job["errors"]) == 1
    assert "data_sources" in job["recent_error"]
    assert job["docs_total"] == 106 and job["docs_processed"] == 0 and job["docs_failed"] == 0
    # token 校验 + 读取一次容器，然后整批停止。
    assert job["request_count"] == 2 and await store.list_notion_entities() == []
