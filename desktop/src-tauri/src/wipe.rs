//! 「删除全部数据」与 Windows 卸载器共用的删除规则（只依赖标准库，可单独 `rustc --test`）。
//!
//! - 平台默认目录（数据 / 日志 / 缓存 / 壳配置）整个归应用所有，直接删除。
//! - 用户自选的数据目录可能本来就有别的文件：选定时目录为空（或不存在）就写入 `OWNED_MARKER`，
//!   之后可整个删除；没有标记时只删 `KNOWN_ENTRIES`，目录删空了才连目录一起删。
//! - 壳把当前数据目录写进壳配置目录的 `POINTER_FILE`（UTF-16LE、无 BOM、无换行），
//!   卸载器（`windows/hooks.nsh`）据此找到自选目录；两边清单由测试核对一致。

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

/// 自选数据目录「整个归应用所有」的标记文件。
pub const OWNED_MARKER: &str = ".knowledge-arch-owned";
/// 壳配置目录里记录当前数据目录的文件（供卸载器读取）。
pub const POINTER_FILE: &str = "data-dir.txt";

/// 后端在数据目录根下创建的条目（`ka_server/paths.py`、`kacore` 各模块；含旧插件导入的 managed_docs）。
pub const KNOWN_ENTRIES: &[&str] = &[
    OWNED_MARKER,
    ".ka_server.lock",
    ".legacy_import",
    ".r2_restore_rollback",
    ".r2_restore_staging",
    "agent-connection.json",
    "embedding_cache.db",
    "embedding_cache.db-journal",
    "embedding_cache.db-shm",
    "embedding_cache.db-wal",
    "extras",
    "index_compatibility.json",
    "knowledge_repository.db",
    "knowledge_repository.db-journal",
    "knowledge_repository.db-shm",
    "knowledge_repository.db-wal",
    "library",
    "lightrag_workspaces",
    "logs",
    "managed_docs",
    "secrets",
    "settings.json",
    "uploads",
    "vector_store.db",
    "zotero_web_cache",
];

/// 选定自选数据目录时调用：目录不存在或为空就建好并写入标记，返回是否归应用所有。
pub fn claim_if_empty(dir: &Path) -> io::Result<bool> {
    if is_owned(dir) {
        return Ok(true);
    }
    let empty = match fs::read_dir(dir) {
        Ok(mut entries) => entries.next().is_none(),
        Err(e) if e.kind() == io::ErrorKind::NotFound => true,
        Err(e) => return Err(e),
    };
    if !empty {
        return Ok(false);
    }
    fs::create_dir_all(dir)?;
    fs::write(dir.join(OWNED_MARKER), "Knowledge Arch data folder\n")?;
    Ok(true)
}

pub fn is_owned(dir: &Path) -> bool {
    dir.join(OWNED_MARKER).is_file()
}

/// 删除一个目录：`owned` 时整个删除，否则只删 `KNOWN_ENTRIES`。
///
/// 相对路径一律拒绝；`protected`（如家目录）及其祖先不整删；单项失败不中断，返回全部失败说明。
pub fn remove_dir(dir: &Path, owned: bool, protected: &[PathBuf]) -> Result<(), Vec<String>> {
    if !dir.is_absolute() {
        return Err(vec![format!("{}: not an absolute path", dir.display())]);
    }
    if !dir.exists() {
        return Ok(());
    }
    if owned && !is_protected(dir, protected) {
        return fs::remove_dir_all(dir).map_err(|e| vec![format!("{}: {e}", dir.display())]);
    }
    let mut errors = Vec::new();
    for name in KNOWN_ENTRIES {
        let path = dir.join(name);
        let result = match fs::symlink_metadata(&path) {
            Ok(meta) if meta.is_dir() => fs::remove_dir_all(&path),
            Ok(_) => fs::remove_file(&path),
            Err(_) => continue,
        };
        if let Err(e) = result {
            errors.push(format!("{}: {e}", path.display()));
        }
    }
    let _ = fs::remove_dir(dir); // 还有别的文件时保留目录
    if errors.is_empty() {
        Ok(())
    } else {
        Err(errors)
    }
}

/// 根目录、`protected` 里的目录及其祖先不允许整个删除。
fn is_protected(dir: &Path, protected: &[PathBuf]) -> bool {
    dir.parent().is_none() || protected.iter().any(|p| p.starts_with(dir))
}

/// 把当前数据目录写进 `config_dir/POINTER_FILE`；`None`（平台默认目录）时删除该文件。
pub fn write_pointer(config_dir: &Path, data_dir: Option<&Path>) -> io::Result<()> {
    let path = config_dir.join(POINTER_FILE);
    let Some(dir) = data_dir else {
        return match fs::remove_file(&path) {
            Err(e) if e.kind() != io::ErrorKind::NotFound => Err(e),
            _ => Ok(()),
        };
    };
    fs::create_dir_all(config_dir)?;
    let text = dir.display().to_string();
    let bytes: Vec<u8> = text.encode_utf16().flat_map(u16::to_le_bytes).collect();
    fs::write(path, bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    static SEQ: AtomicUsize = AtomicUsize::new(0);

    fn scratch() -> PathBuf {
        let n = SEQ.fetch_add(1, Ordering::SeqCst);
        let dir = std::env::temp_dir().join(format!("ka-wipe-{}-{n}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn empty_or_missing_folder_is_claimed() {
        let root = scratch();
        let missing = root.join("new");
        assert!(claim_if_empty(&missing).unwrap());
        assert!(is_owned(&missing));
        assert!(claim_if_empty(&root.join("new")).unwrap());
    }

    #[test]
    fn non_empty_folder_is_not_claimed() {
        let root = scratch();
        fs::write(root.join("thesis.docx"), "x").unwrap();
        assert!(!claim_if_empty(&root).unwrap());
        assert!(!is_owned(&root));
    }

    #[test]
    fn shared_folder_keeps_foreign_files() {
        let root = scratch();
        fs::write(root.join("thesis.docx"), "mine").unwrap();
        fs::write(root.join("settings.json"), "{}").unwrap();
        fs::create_dir_all(root.join("library/doc1")).unwrap();
        fs::write(root.join("knowledge_repository.db-wal"), "").unwrap();

        remove_dir(&root, false, &[]).unwrap();

        assert!(root.join("thesis.docx").is_file());
        assert!(!root.join("settings.json").exists());
        assert!(!root.join("library").exists());
        assert!(!root.join("knowledge_repository.db-wal").exists());
    }

    #[test]
    fn shared_folder_left_empty_is_removed() {
        let root = scratch();
        let dir = root.join("data");
        fs::create_dir_all(dir.join("secrets")).unwrap();
        remove_dir(&dir, false, &[]).unwrap();
        assert!(!dir.exists());
    }

    #[test]
    fn owned_folder_is_removed_entirely() {
        let root = scratch();
        let dir = root.join("data");
        claim_if_empty(&dir).unwrap();
        fs::write(dir.join("unknown-from-old-plugin.bin"), "x").unwrap();
        remove_dir(&dir, is_owned(&dir), &[]).unwrap();
        assert!(!dir.exists());
    }

    #[test]
    fn protected_paths_are_never_removed_entirely() {
        let root = scratch();
        let home = root.join("home");
        fs::create_dir_all(&home).unwrap();
        fs::write(home.join("notes.txt"), "keep").unwrap();
        fs::write(home.join("settings.json"), "{}").unwrap();

        let protected = std::slice::from_ref(&home);
        remove_dir(&home, true, protected).unwrap();
        remove_dir(&root, true, protected).unwrap();

        assert!(home.join("notes.txt").is_file());
        assert!(!home.join("settings.json").exists());
    }

    #[test]
    fn relative_paths_are_refused() {
        assert!(remove_dir(Path::new("Knowledge Arch"), true, &[]).is_err());
    }

    #[test]
    fn missing_folder_is_fine() {
        let root = scratch();
        assert!(remove_dir(&root.join("nope"), true, &[]).is_ok());
    }

    #[test]
    fn pointer_is_utf16le_without_bom_or_newline() {
        let root = scratch();
        let data = PathBuf::from("D:/资料/KA");
        write_pointer(&root, Some(&data)).unwrap();
        let bytes = fs::read(root.join(POINTER_FILE)).unwrap();
        let units: Vec<u16> = bytes
            .chunks(2)
            .map(|c| u16::from_le_bytes([c[0], c[1]]))
            .collect();
        assert_eq!(String::from_utf16(&units).unwrap(), "D:/资料/KA");
        write_pointer(&root, None).unwrap();
        assert!(!root.join(POINTER_FILE).exists());
        write_pointer(&root, None).unwrap();
    }

    #[test]
    fn uninstaller_hook_matches_this_list() {
        let hook = include_str!("../windows/hooks.nsh");
        for name in KNOWN_ENTRIES {
            assert!(
                hook.contains(&format!("\\{name}\"")),
                "hooks.nsh misses {name}"
            );
        }
        assert!(hook.contains(POINTER_FILE));
        assert!(hook.contains(OWNED_MARKER));
    }
}
