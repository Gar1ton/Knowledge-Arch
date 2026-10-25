//! 桌面壳自己保存的偏好（owner=shell 的设置项）与目录解析。
//!
//! 这些偏好在后端启动之前就要用到（数据目录覆盖决定 `--data-dir`），所以不放在后端的
//! settings.json 里，而是放在系统配置目录的 `shell.json`。界面改动时前端会同时写后端
//! （便于展示 / 随快照携带）并调用 `set_shell_pref` 交给壳即时生效。
//!
//! 默认目录与后端 `ka_server/paths.py`（platformdirs）保持一致：
//! - macOS   数据 ~/Library/Application Support/Knowledge Arch，日志 ~/Library/Logs/Knowledge Arch
//! - Windows 数据 %LOCALAPPDATA%\Knowledge Arch，日志 %LOCALAPPDATA%\Knowledge Arch\Logs
//! - Linux   数据 ~/.local/share/Knowledge Arch，日志 ~/.local/state/Knowledge Arch/log
//!
//! 指定了数据目录覆盖时，后端把日志写在 `<数据目录>/logs`。

use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

pub const APP_NAME: &str = "Knowledge Arch";
const PREFS_FILE: &str = "shell.json";

pub const KEY_DATA_DIR: &str = "prefs.storage.data_dir";
pub const KEY_CLOSE_TO_TRAY: &str = "prefs.general.close_to_tray";
pub const KEY_LAUNCH_AT_LOGIN: &str = "prefs.general.launch_at_login";
pub const KEY_CHECK_UPDATES: &str = "prefs.general.check_updates";

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct ShellPrefs {
    /// 数据目录覆盖；空 = 平台默认目录（不传 `--data-dir`）。
    pub data_dir: String,
    pub close_to_tray: bool,
    pub launch_at_login: bool,
    pub check_updates: bool,
}

impl Default for ShellPrefs {
    fn default() -> Self {
        Self {
            data_dir: String::new(),
            close_to_tray: true,
            launch_at_login: false,
            check_updates: true,
        }
    }
}

impl ShellPrefs {
    fn path() -> Option<PathBuf> {
        dirs::config_dir().map(|d| d.join(APP_NAME).join(PREFS_FILE))
    }

    /// 读取；文件缺失或损坏时返回默认值（不阻止应用启动）。
    pub fn load() -> Self {
        Self::path()
            .and_then(|p| fs::read_to_string(p).ok())
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_default()
    }

    /// 原子写入（先写临时文件再改名）。
    pub fn save(&self) -> Result<(), String> {
        let path = Self::path().ok_or("no config directory")?;
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        let tmp = path.with_extension("json.tmp");
        let text = serde_json::to_string_pretty(self).map_err(|e| e.to_string())?;
        fs::write(&tmp, text).map_err(|e| e.to_string())?;
        fs::rename(&tmp, &path).map_err(|e| e.to_string())
    }

    /// 按设置键写入一项；未知键返回错误。
    pub fn apply(&mut self, key: &str, value: &serde_json::Value) -> Result<(), String> {
        match key {
            KEY_DATA_DIR => {
                self.data_dir = value.as_str().unwrap_or_default().trim().to_string();
            }
            KEY_CLOSE_TO_TRAY => self.close_to_tray = value.as_bool().ok_or("expected bool")?,
            KEY_LAUNCH_AT_LOGIN => self.launch_at_login = value.as_bool().ok_or("expected bool")?,
            KEY_CHECK_UPDATES => self.check_updates = value.as_bool().ok_or("expected bool")?,
            other => return Err(format!("unknown shell preference: {other}")),
        }
        Ok(())
    }

    pub fn as_map(&self) -> serde_json::Value {
        serde_json::json!({
            KEY_DATA_DIR: self.data_dir,
            KEY_CLOSE_TO_TRAY: self.close_to_tray,
            KEY_LAUNCH_AT_LOGIN: self.launch_at_login,
            KEY_CHECK_UPDATES: self.check_updates,
        })
    }

    pub fn data_dir_override(&self) -> Option<PathBuf> {
        if self.data_dir.is_empty() {
            None
        } else {
            Some(PathBuf::from(&self.data_dir))
        }
    }
}

/// 平台默认数据目录（与 platformdirs.user_data_dir(APP_NAME, appauthor=False) 一致）。
/// 只有 Windows 的日志目录由它推导；其余平台保留它作为布局说明与测试对照。
#[cfg_attr(not(target_os = "windows"), allow(dead_code))]
pub fn default_data_dir() -> PathBuf {
    #[cfg(target_os = "windows")]
    let base = dirs::data_local_dir();
    #[cfg(not(target_os = "windows"))]
    let base = dirs::data_dir();
    base.unwrap_or_else(|| PathBuf::from(".")).join(APP_NAME)
}

/// 平台默认日志目录（与 platformdirs.user_log_dir(APP_NAME, appauthor=False) 一致）。
pub fn default_log_dir() -> PathBuf {
    #[cfg(target_os = "macos")]
    {
        dirs::home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join("Library/Logs")
            .join(APP_NAME)
    }
    #[cfg(target_os = "windows")]
    {
        default_data_dir().join("Logs")
    }
    #[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
    {
        dirs::state_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join(APP_NAME)
            .join("log")
    }
}

/// 当前生效的日志目录：有数据目录覆盖时在其下的 logs/。
pub fn log_dir(prefs: &ShellPrefs) -> PathBuf {
    match prefs.data_dir_override() {
        Some(dir) => dir.join("logs"),
        None => default_log_dir(),
    }
}

pub fn ensure_dir(path: &Path) {
    let _ = fs::create_dir_all(path);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn apply_known_keys_and_reject_unknown() {
        let mut prefs = ShellPrefs::default();
        prefs
            .apply(KEY_DATA_DIR, &serde_json::json!("  /tmp/ka  "))
            .unwrap();
        prefs
            .apply(KEY_CLOSE_TO_TRAY, &serde_json::json!(false))
            .unwrap();
        assert_eq!(prefs.data_dir_override(), Some(PathBuf::from("/tmp/ka")));
        assert!(!prefs.close_to_tray);
        assert!(prefs
            .apply("prefs.general.language", &serde_json::json!("zh"))
            .is_err());
        assert!(prefs
            .apply(KEY_CHECK_UPDATES, &serde_json::json!("yes"))
            .is_err());
    }

    #[test]
    fn override_moves_logs_into_data_dir() {
        let mut prefs = ShellPrefs::default();
        assert_eq!(log_dir(&prefs), default_log_dir());
        prefs.data_dir = "/data/ka".into();
        assert_eq!(log_dir(&prefs), PathBuf::from("/data/ka/logs"));
    }

    #[test]
    fn missing_fields_fall_back_to_defaults() {
        let prefs: ShellPrefs = serde_json::from_str(r#"{"data_dir": "/x"}"#).unwrap();
        assert!(prefs.close_to_tray && prefs.check_updates && !prefs.launch_at_login);
    }
}
