//! 自动更新（Cargo feature `updater`）。
//!
//! 发布流水线在有 updater 公钥时才启用这个 feature，并把公钥与端点写进 tauri.conf.json；
//! 本地构建与未配置公钥的构建里，命令照常存在但只返回「此版本未启用自动更新」，界面据此隐藏入口。
//!
//! 流程：启动 10 秒后（`prefs.general.check_updates` 开启时）检查一次 → 有新版本则发
//! `update://available` 事件并弹系统通知 → 用户在界面点「安装并重启」→ `install_update`
//! 下载、校验签名、安装后重启应用（重启前照常有序停止后端）。

use serde::Serialize;
use tauri::AppHandle;

#[cfg_attr(not(feature = "updater"), allow(dead_code))]
pub const AVAILABLE_EVENT: &str = "update://available";

#[derive(Debug, Clone, Serialize)]
pub struct UpdateInfo {
    pub enabled: bool,
    pub available: bool,
    pub current_version: String,
    pub version: Option<String>,
    pub notes: Option<String>,
}

#[cfg(feature = "updater")]
mod imp {
    use super::{UpdateInfo, AVAILABLE_EVENT};
    use tauri::{AppHandle, Emitter};
    use tauri_plugin_notification::NotificationExt;
    use tauri_plugin_updater::UpdaterExt;

    pub async fn check(app: &AppHandle) -> Result<UpdateInfo, String> {
        let current = app.package_info().version.to_string();
        let update = app
            .updater()
            .map_err(|e| e.to_string())?
            .check()
            .await
            .map_err(|e| e.to_string())?;
        Ok(match update {
            Some(update) => UpdateInfo {
                enabled: true,
                available: true,
                current_version: current,
                version: Some(update.version.clone()),
                notes: update.body.clone(),
            },
            None => UpdateInfo {
                enabled: true,
                available: false,
                current_version: current,
                version: None,
                notes: None,
            },
        })
    }

    pub async fn install(app: &AppHandle) -> Result<(), String> {
        let update = app
            .updater()
            .map_err(|e| e.to_string())?
            .check()
            .await
            .map_err(|e| e.to_string())?
            .ok_or("已是最新版本")?;
        update
            .download_and_install(|_chunk, _total| {}, || {})
            .await
            .map_err(|e| e.to_string())?;
        app.restart();
    }

    pub fn spawn_startup_check(app: AppHandle) {
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_secs(10));
            if let Ok(info) = tauri::async_runtime::block_on(check(&app)) {
                if info.available {
                    let version = info.version.clone().unwrap_or_default();
                    let _ = app.emit(AVAILABLE_EVENT, info);
                    let _ = app
                        .notification()
                        .builder()
                        .title("Knowledge Arch")
                        .body(format!("新版本 {version} 可用，可在应用内安装。"))
                        .show();
                }
            }
        });
    }

    pub fn plugin() -> tauri::plugin::TauriPlugin<tauri::Wry, tauri_plugin_updater::Config> {
        tauri_plugin_updater::Builder::new().build()
    }
}

#[cfg(not(feature = "updater"))]
mod imp {
    use super::UpdateInfo;
    use tauri::AppHandle;

    pub async fn check(app: &AppHandle) -> Result<UpdateInfo, String> {
        Ok(UpdateInfo {
            enabled: false,
            available: false,
            current_version: app.package_info().version.to_string(),
            version: None,
            notes: None,
        })
    }

    pub async fn install(_app: &AppHandle) -> Result<(), String> {
        Err("此版本未启用自动更新".into())
    }

    pub fn spawn_startup_check(_app: AppHandle) {}
}

#[cfg(feature = "updater")]
pub use imp::plugin;

pub fn spawn_startup_check(app: AppHandle) {
    imp::spawn_startup_check(app)
}

#[tauri::command]
pub async fn check_update(app: AppHandle) -> Result<UpdateInfo, String> {
    imp::check(&app).await
}

#[tauri::command]
pub async fn install_update(app: AppHandle) -> Result<(), String> {
    imp::install(&app).await
}
