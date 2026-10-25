//! Knowledge Arch 桌面壳（Tauri 2）。
//!
//! 职责只在「壳」这一层：单实例、窗口 / 菜单 / 托盘、原生对话框与系统通知、壳偏好，以及后端
//! sidecar 的启停（见 sidecar.rs）。业务全部在 Python 后端；界面是 web/frontend 的静态导出。
//!
//! 前端可调用的命令（与 web/frontend/lib/backend.ts 一一对应）：
//! backend_info / backend_state / restart_backend / pick_folder / open_log_dir / notify /
//! set_shell_pref / get_shell_prefs。

mod prefs;
mod sidecar;

use prefs::ShellPrefs;
use sidecar::{BackendInfo, BackendState, LaunchSpec, Sidecar};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;
#[cfg(target_os = "macos")]
use tauri::menu::PredefinedMenuItem;
use tauri::menu::{Menu, MenuBuilder, MenuItemBuilder, SubmenuBuilder};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, RunEvent, WindowEvent, Wry};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt as AutostartExt};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_opener::OpenerExt;

const MAIN_WINDOW: &str = "main";
const MENU_PREFERENCES: &str = "preferences";
const MENU_SHOW: &str = "show";
const MENU_QUIT: &str = "quit";
/// 前端订阅的事件：菜单 / 托盘里的「偏好设置…」。
const MENU_PREFERENCES_EVENT: &str = "menu://preferences";
const BACKEND_INFO_TIMEOUT: Duration = Duration::from_secs(75);
/// 开发期（`tauri dev`）界面由 next dev 提供，需要把它的 Origin 加进后端 CORS 白名单。
const DEV_ORIGIN: &str = "http://localhost:26619";
/// 调试用：指定另一个解释器启动后端（例如仓库的开发 venv）；发行版也生效，便于排障。
const ENV_SIDECAR_PYTHON: &str = "KA_SIDECAR_PYTHON";

struct ShellState {
    prefs: Mutex<ShellPrefs>,
    sidecar: Sidecar,
}

fn resources_dir(app: &AppHandle) -> PathBuf {
    app.path()
        .resource_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
}

fn launch_spec(app: &AppHandle, prefs: &ShellPrefs) -> LaunchSpec {
    let resources = resources_dir(app);
    let python = std::env::var_os(ENV_SIDECAR_PYTHON)
        .map(PathBuf::from)
        .unwrap_or_else(|| sidecar::bundled_python(&resources));
    let uv = sidecar::bundled_uv(&resources);
    LaunchSpec {
        python,
        uv: uv.exists().then_some(uv),
        data_dir: prefs.data_dir_override(),
        log_dir: prefs::log_dir(prefs),
        dev_origin: cfg!(debug_assertions).then(|| DEV_ORIGIN.to_string()),
    }
}

fn show_main(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(MAIN_WINDOW) {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn open_preferences(app: &AppHandle) {
    show_main(app);
    let _ = app.emit(MENU_PREFERENCES_EVENT, ());
}

// ── 命令 ────────────────────────────────────────────────────────

#[tauri::command]
async fn backend_info(state: tauri::State<'_, ShellState>) -> Result<BackendInfo, String> {
    let sidecar = state.sidecar.clone();
    tauri::async_runtime::spawn_blocking(move || sidecar.wait_ready(BACKEND_INFO_TIMEOUT))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
fn backend_state(state: tauri::State<'_, ShellState>) -> BackendState {
    state.sidecar.state()
}

#[tauri::command]
async fn restart_backend(
    app: AppHandle,
    state: tauri::State<'_, ShellState>,
) -> Result<BackendInfo, String> {
    let spec = launch_spec(&app, &state.prefs.lock().unwrap());
    let sidecar = state.sidecar.clone();
    sidecar.set_spec(spec);
    tauri::async_runtime::spawn_blocking(move || sidecar.restart())
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn pick_folder(app: AppHandle, title: Option<String>) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let mut dialog = app.dialog().file();
        if let Some(window) = app.get_webview_window(MAIN_WINDOW) {
            dialog = dialog.set_parent(&window);
        }
        if let Some(title) = title {
            dialog = dialog.set_title(title);
        }
        dialog
            .blocking_pick_folder()
            .and_then(|path| path.into_path().ok())
            .map(|path| path.display().to_string())
    })
    .await
    .map_err(|e| e.to_string())
}

#[tauri::command]
fn open_log_dir(app: AppHandle, state: tauri::State<'_, ShellState>) -> Result<(), String> {
    let dir = state.sidecar.log_dir();
    prefs::ensure_dir(&dir);
    app.opener()
        .open_path(dir.display().to_string(), None::<&str>)
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn notify(app: AppHandle, title: String, body: String) -> Result<(), String> {
    app.notification()
        .builder()
        .title(title)
        .body(body)
        .show()
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn set_shell_pref(
    app: AppHandle,
    state: tauri::State<'_, ShellState>,
    key: String,
    value: serde_json::Value,
) -> Result<(), String> {
    let mut prefs = state.prefs.lock().unwrap();
    prefs.apply(&key, &value)?;
    prefs.save()?;
    if key == prefs::KEY_LAUNCH_AT_LOGIN {
        let autostart = app.autolaunch();
        let result = if prefs.launch_at_login {
            autostart.enable()
        } else {
            autostart.disable()
        };
        result.map_err(|e| e.to_string())?;
    }
    // 数据目录变更在下次启动后端时生效（界面提示重启应用）。
    state.sidecar.set_spec(launch_spec(&app, &prefs));
    Ok(())
}

#[tauri::command]
fn get_shell_prefs(state: tauri::State<'_, ShellState>) -> serde_json::Value {
    state.prefs.lock().unwrap().as_map()
}

// ── 菜单与托盘 ─────────────────────────────────────────────────

fn build_menu(app: &AppHandle) -> tauri::Result<Menu<Wry>> {
    let preferences = MenuItemBuilder::with_id(MENU_PREFERENCES, "偏好设置…")
        .accelerator("CmdOrCtrl+,")
        .build(app)?;
    let edit = SubmenuBuilder::new(app, "编辑")
        .undo()
        .redo()
        .separator()
        .cut()
        .copy()
        .paste()
        .select_all()
        .build()?;

    #[cfg(target_os = "macos")]
    {
        let app_menu = SubmenuBuilder::new(app, "Knowledge Arch")
            .about(None)
            .separator()
            .item(&preferences)
            .separator()
            .services()
            .separator()
            .hide()
            .hide_others()
            .show_all()
            .separator()
            .quit()
            .build()?;
        let window = SubmenuBuilder::new(app, "窗口")
            .minimize()
            .item(&PredefinedMenuItem::close_window(app, None)?)
            .build()?;
        MenuBuilder::new(app)
            .items(&[&app_menu, &edit, &window])
            .build()
    }
    #[cfg(not(target_os = "macos"))]
    {
        let quit = MenuItemBuilder::with_id(MENU_QUIT, "退出")
            .accelerator("CmdOrCtrl+Q")
            .build(app)?;
        let file = SubmenuBuilder::new(app, "文件")
            .item(&preferences)
            .separator()
            .item(&quit)
            .build()?;
        MenuBuilder::new(app).items(&[&file, &edit]).build()
    }
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let show = MenuItemBuilder::with_id(MENU_SHOW, "显示窗口").build(app)?;
    let preferences = MenuItemBuilder::with_id(MENU_PREFERENCES, "偏好设置…").build(app)?;
    let quit = MenuItemBuilder::with_id(MENU_QUIT, "退出 Knowledge Arch").build(app)?;
    let menu = MenuBuilder::new(app)
        .items(&[&show, &preferences])
        .separator()
        .item(&quit)
        .build()?;
    let mut tray = TrayIconBuilder::with_id("main")
        .tooltip("Knowledge Arch")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_main(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app)?;
    Ok(())
}

fn handle_menu_event(app: &AppHandle, id: &str) {
    match id {
        MENU_PREFERENCES => open_preferences(app),
        MENU_SHOW => show_main(app),
        MENU_QUIT => app.exit(0),
        _ => {}
    }
}

// ── 入口 ────────────────────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        // 单实例必须最先注册：第二个实例只把已有窗口带到前台，然后退出。
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            show_main(app)
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_autostart::init(
            MacosLauncher::LaunchAgent,
            None,
        ))
        .setup(|app| {
            let handle = app.handle().clone();
            let prefs = ShellPrefs::load();
            let sidecar = Sidecar::new(handle.clone(), launch_spec(&handle, &prefs));
            sidecar.start();
            app.manage(ShellState {
                prefs: Mutex::new(prefs),
                sidecar,
            });

            app.set_menu(build_menu(&handle)?)?;
            app.on_menu_event(|app, event| handle_menu_event(app, event.id().as_ref()));
            build_tray(&handle)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                let close_to_tray = window
                    .app_handle()
                    .state::<ShellState>()
                    .prefs
                    .lock()
                    .map(|p| p.close_to_tray)
                    .unwrap_or(false);
                if close_to_tray {
                    let _ = window.hide();
                    api.prevent_close();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            backend_info,
            backend_state,
            restart_backend,
            pick_folder,
            open_log_dir,
            notify,
            set_shell_pref,
            get_shell_prefs,
        ])
        .build(tauri::generate_context!())
        .expect("failed to build the Knowledge Arch shell");

    app.run(|app, event| match event {
        // 退出前有序停止后端：释放数据库与 Milvus Lite 锁（父进程消失时后端也会自行退出）。
        RunEvent::Exit => {
            if let Some(state) = app.try_state::<ShellState>() {
                state.sidecar.stop();
            }
        }
        #[cfg(target_os = "macos")]
        RunEvent::Reopen { .. } => show_main(app),
        _ => {}
    });
}
