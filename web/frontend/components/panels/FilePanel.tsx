"use client";
/**
 * 左栏「文件」：一棵集合树（`CollectionTree`）+ 集合的对话框。
 *
 * 每个操作只出现一次：Zotero 同步在 Zotero 组头；上传、新建集合在本地组头；R2 备份在栏头；
 * 构建图谱、补全图表描述、新建子集合、改名、删除在集合行的 ⋯ 菜单。图谱构建的进度、暂停与取消
 * 在左下角进度栏（ProgressDock），这里只用构建状态给行画点。
 */
import React, { useCallback, useEffect, useState } from "react";
import { Panel } from "@/components/ds/Panel";
import { IconButton } from "@/components/ds/IconButton";
import { notifyDocumentsChanged, DOCUMENTS_CHANGED_EVENT } from "@/lib/documentEvents";
import { useFeatureReadiness, type Feature } from "@/lib/featureReadiness";
import { useConsole } from "@/lib/ConsoleContext";
import {
  listCollections,
  getActiveBuildJob,
  getBuildJobHistory,
  getLibrarySummary,
  buildGraph,
  deleteCollection,
  deleteCollectionByKey,
  syncZoteroPull,
  getActiveZoteroSyncJob,
  backupNow,
  Collection,
  GraphBuildJob,
  BuildJobRecord,
} from "@/lib/api";
import { useToast } from "@/components/ui/Toast";
import { VisionBuildDialog } from "@/components/panels/VisionBuildDialog";
import {
  DeleteCollectionDialog,
  NewCollectionDialog,
  RenameCollectionDialog,
} from "@/components/panels/CollectionDialogs";
import { UploadDocumentDialog } from "@/components/panels/UploadDocumentDialog";
import { CollectionTree, type GraphState, type TreeMenuItem } from "@/components/panels/CollectionTree";
import { useI18n, type I18nKey } from "@/lib/i18n";

function isActiveBuildStatus(status?: string): boolean {
  return status === "queued" || status === "running" || status === "pause_requested" || status === "paused";
}

export function FilePanel() {
  const { selectedCollection, setSelectedCollection, setSelectedDocId } = useConsole();
  const { ready, ensure } = useFeatureReadiness();
  const { toast } = useToast();
  const { t } = useI18n();
  const FEATURE_LABEL: Record<Feature, I18nKey> = {
    zotero: "feature_zotero",
    r2: "feature_r2",
    graph: "feature_graph",
  };
  // 未就绪的功能显示为灰色；点击时现查一次，仍未就绪就提示去数据流页配置。
  const dimmed = (feature: Feature): React.CSSProperties | undefined =>
    ready[feature] === false ? { opacity: 0.4 } : undefined;
  async function whenReady(feature: Feature, action: () => void | Promise<void>) {
    if (ready[feature] === false && !(await ensure(feature))) {
      toast(t("feature_not_configured", { feature: t(FEATURE_LABEL[feature]) }), "info");
      return;
    }
    await action();
  }

  const [collections, setCollections] = useState<Collection[]>([]);
  const [libraryTotal, setLibraryTotal] = useState<number | null>(null);
  const [buildJob, setBuildJob] = useState<GraphBuildJob | null>(null);
  const [buildHistory, setBuildHistory] = useState<BuildJobRecord[]>([]);
  const [deletingCollection, setDeletingCollection] = useState<Collection | null>(null);
  const [deleteInProgress, setDeleteInProgress] = useState(false);
  // 「补全图表描述」确认框：null = 关闭，"" = 全部文档，否则为集合名。
  const [visionCollection, setVisionCollection] = useState<string | null>(null);
  const [showNewCollection, setShowNewCollection] = useState(false);
  const [newCollectionParent, setNewCollectionParent] = useState<Collection | null>(null);
  const [renamingCollection, setRenamingCollection] = useState<Collection | null>(null);
  const [showUpload, setShowUpload] = useState(false);
  const [zoteroSyncing, setZoteroSyncing] = useState(false);

  /** 集合与篇数：启动、同步结束、集合增删改、文档变化（上传等）之后重新取。 */
  const refreshTree = useCallback(() => {
    listCollections().then(setCollections).catch(() => {});
    getLibrarySummary().then((s) => setLibraryTotal(s.document_count)).catch(() => {});
  }, []);

  useEffect(() => {
    refreshTree();
    getBuildJobHistory().then(setBuildHistory).catch(() => {});
    window.addEventListener(DOCUMENTS_CHANGED_EVENT, refreshTree);
    return () => window.removeEventListener(DOCUMENTS_CHANGED_EVENT, refreshTree);
  }, [refreshTree]);

  // 旧版本把「LightRAG 集合」区的选择记成 `lr:名字`：换成这个集合在树上的键。
  useEffect(() => {
    if (!selectedCollection?.startsWith("lr:") || collections.length === 0) return;
    const name = selectedCollection.slice(3);
    const c = collections.find((x) => x.name === name);
    setSelectedCollection(c ? `${c.origin === "zotero" ? "z:" : "l:"}${c.name}` : null);
  }, [selectedCollection, collections, setSelectedCollection]);

  // 轮询 Zotero 同步进度；完成后刷新集合树与文档列表。
  useEffect(() => {
    if (!zoteroSyncing) return;
    let cancelled = false;
    async function poll() {
      if (cancelled) return;
      try {
        const job = await getActiveZoteroSyncJob();
        if (!cancelled) {
          if (!job || job.status !== "running") {
            setZoteroSyncing(false);
            notifyDocumentsChanged();
          } else {
            setTimeout(poll, 2000);
          }
        }
      } catch {
        if (!cancelled) setTimeout(poll, 3000);
      }
    }
    poll();
    return () => { cancelled = true; };
  }, [zoteroSyncing]);

  // 图谱构建状态：只用来给集合行画点、决定菜单文案；进度与暂停在 ProgressDock。
  const prevBuildJobRef = React.useRef<GraphBuildJob | null>(null);
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    async function poll() {
      if (cancelled) return;
      let job: GraphBuildJob | null = null;
      try {
        job = await getActiveBuildJob();
        if (!cancelled) {
          if (prevBuildJobRef.current && !job) {
            getBuildJobHistory().then(setBuildHistory).catch(() => {});
          }
          prevBuildJobRef.current = job;
          setBuildJob(job);
        }
      } catch { /* ignore */ }
      if (!cancelled) timer = window.setTimeout(poll, job ? 2000 : 5000);
    }
    poll();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, []);

  const activeBuild = buildJob && isActiveBuildStatus(buildJob.status) ? buildJob : null;

  function latestBuildOf(name: string): BuildJobRecord | undefined {
    return buildHistory
      .filter((r) => r.collection === name)
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0];
  }

  function graphStateOf(name: string): GraphState {
    if (activeBuild?.collection === name) return "building";
    const latest = latestBuildOf(name);
    if (latest?.status === "success") return "success";
    if (latest?.status === "interrupted") return "interrupted";
    return null;
  }

  function selectCollection(key: string | null) {
    setSelectedCollection(key);
    setSelectedDocId(null);
  }

  async function handleZoteroSync() {
    try {
      await syncZoteroPull(true);
      toast(t("zotero_sync_started"), "ok");
      setZoteroSyncing(true);
    } catch (err: unknown) {
      toast(err instanceof Error ? err.message : String(err), "error");
    }
  }

  async function startBuild(collectionName: string) {
    if (activeBuild) {
      toast(t("file_build_active_exists"), "error");
      return;
    }
    try {
      const job = await buildGraph(collectionName);
      setBuildJob(job);
      prevBuildJobRef.current = job;
      toast(t("file_build_started"), "ok");
    } catch {
      toast(t("file_build_error"), "error");
    }
  }

  function menuFor(c: Collection): TreeMenuItem[] {
    const interrupted = latestBuildOf(c.name)?.status === "interrupted" && activeBuild?.collection !== c.name;
    const items: TreeMenuItem[] = [
      {
        key: "graph",
        icon: "graph",
        label: t(interrupted ? "file_action_lightrag_resume" : "file_action_lightrag_build"),
        dimmed: ready.graph === false,
        onSelect: () => void whenReady("graph", () => startBuild(c.name)),
      },
      {
        key: "figures",
        icon: "image",
        label: t("file_action_describe_figures"),
        onSelect: () => setVisionCollection(c.name),
      },
    ];
    if (c.origin !== "zotero") {
      items.push({
        key: "sub",
        icon: "plus",
        label: t("file_action_add_subcollection"),
        separated: true,
        onSelect: () => { setNewCollectionParent(c); setShowNewCollection(true); },
      });
      if (!c.is_default) {
        items.push(
          {
            key: "rename",
            icon: "edit",
            label: t("file_action_rename_collection"),
            onSelect: () => setRenamingCollection(c),
          },
          {
            key: "delete",
            icon: "trash",
            label: t("file_action_delete_collection"),
            danger: true,
            onSelect: () => setDeletingCollection(c),
          },
        );
      }
    }
    return items;
  }

  const localCollections = collections.filter((c) => c.origin !== "zotero");

  return (
    <>
    <Panel
      title={t("panel_file")}
      flush
      style={{ flex: 1 }}
      right={
        <IconButton name="cloud" label={t("file_action_r2_backup")} style={dimmed("r2")} onClick={() => void whenReady("r2", async () => {
          try {
            await backupNow(false);
            toast(t("r2_full_backup_started"), "ok");
          } catch (err: unknown) {
            toast(err instanceof Error ? err.message : String(err), "error");
          }
        })} />
      }
    >
      <CollectionTree
        collections={collections}
        libraryTotal={libraryTotal}
        selectedCollection={selectedCollection}
        onSelect={selectCollection}
        graphStateOf={graphStateOf}
        menuFor={menuFor}
        emptyZoteroHint={t("file_no_zotero")}
        zoteroActions={
          <IconButton
            name="sync"
            label={t("file_action_zotero_sync")}
            size={14}
            active={zoteroSyncing}
            style={dimmed("zotero")}
            onClick={() => void whenReady("zotero", handleZoteroSync)}
          />
        }
        localActions={
          <>
            <span data-tour="upload" style={{ display: "inline-flex" }}>
              <IconButton name="upload" label={t("file_action_upload_document")} size={14} onClick={() => setShowUpload(true)} />
            </span>
            <IconButton name="plus" label={t("file_action_new_collection")} size={14} onClick={() => { setNewCollectionParent(null); setShowNewCollection(true); }} />
          </>
        }
      />
    </Panel>

    {deletingCollection && (
      <DeleteCollectionDialog
        collectionName={deletingCollection.name}
        defaultCollectionName={collections.find((c) => c.is_default)?.name ?? "default"}
        deleting={deleteInProgress}
        onCancel={() => setDeletingCollection(null)}
        onConfirm={async () => {
          const target = deletingCollection;
          setDeleteInProgress(true);
          try {
            if (target.coll_key) {
              await deleteCollectionByKey(target.coll_key);
            } else {
              await deleteCollection(target.name);
            }
            toast(t("file_deleted_toast", { name: target.name }), "ok");
            if (selectedCollection === `l:${target.name}`) {
              setSelectedCollection(null);
              setSelectedDocId(null);
            }
            setDeletingCollection(null);
            notifyDocumentsChanged();
          } catch (err: unknown) {
            toast(t("file_delete_failed_toast", { message: err instanceof Error ? err.message : String(err) }), "error");
          } finally {
            setDeleteInProgress(false);
          }
        }}
      />
    )}

    {showNewCollection && (
      <NewCollectionDialog
        parentKey={newCollectionParent?.coll_key}
        parentName={newCollectionParent?.name}
        onClose={() => { setShowNewCollection(false); setNewCollectionParent(null); }}
        onCreated={refreshTree}
      />
    )}

    {renamingCollection && (
      <RenameCollectionDialog
        collection={renamingCollection}
        onClose={() => setRenamingCollection(null)}
        onRenamed={(newName) => {
          // 选中的是被改名的集合时跟到新名字（选中键按名字记），文档列表随之刷新。
          if (selectedCollection === `l:${renamingCollection.name}`) {
            setSelectedCollection(`l:${newName}`);
          }
          notifyDocumentsChanged();
        }}
      />
    )}

    {showUpload && (
      <UploadDocumentDialog
        defaultCollection={selectedCollection ? selectedCollection.replace(/^(z:|l:|lr:)/, "") : null}
        localCollections={localCollections.map((c) => c.name)}
        onClose={() => setShowUpload(false)}
      />
    )}

    {visionCollection !== null && (
      <VisionBuildDialog
        collection={visionCollection || null}
        onClose={() => setVisionCollection(null)}
      />
    )}
    </>
  );
}
