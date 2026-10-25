//! Knowledge Arch 桌面壳（Tauri 2）。
//!
//! 职责只在「壳」这一层：单实例、窗口 / 菜单 / 托盘、原生对话框与系统通知、壳偏好，以及后端
//! sidecar 的启停（见 sidecar.rs）。业务全部在 Python 后端；界面是 web/frontend 的静态导出。
//!
//! 前端可调用的命令（与 web/frontend/lib/backend.ts 一一对应）：
//! backend_info / backend_state / restart_backend / pick_folder / open_log_dir / notify /
//! set_shell_pref / get_shell_prefs / quit_app / wipe_data_and_quit / check_update / install_update。
//!
//! 菜单栏只在 macOS 挂（系统顶栏的应用 / 编辑 / 窗口菜单）；Windows / Linux 不挂，
//! Ctrl+, 与 Ctrl+Q 由前端处理，托盘菜单三平台都有。

mod prefs;
mod sidecar;
mod updater;
mod wipe;

use prefs::ShellPrefs;
use sidecar::{BackendInfo, BackendState, LaunchSpec, Sidecar};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::Duration;
#[cfg(target_os = "macos")]
use tauri::menu::{Menu, PredefinedMenuItem, SubmenuBuilder};
use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
#[cfg(target_os = "macos")]
use tauri::Wry;
use tauri::{AppHandle, Emitter, Manager, RunEvent, WindowEvent};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt as AutostartExt};
use tauri_plugin_dialog::{DialogExt, MessageDialogKind};
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
    /// 后端本次实际使用的数据目录覆盖（改了数据目录但还没重启时，与 prefs 里的新值不同）。
    launched_data_dir: Mutex<Option<PathBuf>>,
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

/// 把当前数据目录写给卸载器（失败只影响卸载时能否找到自选目录，不阻止启动）。
fn publish_data_dir(prefs: &ShellPrefs) {
    if let Some(dir) = prefs::config_dir() {
        let _ = wipe::write_pointer(&dir, prefs.data_dir_override().as_deref());
    }
}

/// 删除全部数据：自选目录（正在用的与下次启动要用的）按归属标记处理，平台默认的数据 / 日志 /
/// 缓存 / 壳配置目录整个删除。返回失败说明（空 = 全部删除）。调用前后端必须已停止。
fn wipe_all(custom_dirs: &[PathBuf]) -> Vec<String> {
    let protected: Vec<PathBuf> = dirs::home_dir().into_iter().collect();
    let mut targets = Vec::new();
    for dir in custom_dirs {
        targets.push((dir.clone(), wipe::is_owned(dir)));
    }
    targets.push((prefs::default_data_dir(), true));
    targets.push((prefs::default_log_root(), true));
    targets.push((prefs::default_cache_dir(), true));
    targets.extend(prefs::config_dir().map(|dir| (dir, true)));
    targets
        .into_iter()
        .filter_map(|(dir, owned)| wipe::remove_dir(&dir, owned, &protected).err())
        .flatten()
        .collect()
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
    *state.launched_data_dir.lock().unwrap() = spec.data_dir.clone();
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
    if key == prefs::KEY_DATA_DIR {
        // 选定时为空的目录整个归应用所有，删除数据时可整删；否则只删应用自己的条目。
        if let Some(dir) = prefs.data_dir_override() {
            wipe::claim_if_empty(&dir).map_err(|e| e.to_string())?;
        }
    }
    prefs.save()?;
    publish_data_dir(&prefs);
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

#[tauri::command]
fn quit_app(app: AppHandle) {
    app.exit(0);
}

/// 「删除全部数据」：停后端 → 关闭开机自启 → 删目录 → 退出。主密钥已由前端先经后端删除。
#[tauri::command]
async fn wipe_data_and_quit(
    app: AppHandle,
    state: tauri::State<'_, ShellState>,
) -> Result<(), String> {
    let mut custom_dirs: Vec<PathBuf> = state
        .prefs
        .lock()
        .unwrap()
        .data_dir_override()
        .into_iter()
        .collect();
    if let Some(dir) = state.launched_data_dir.lock().unwrap().clone() {
        if !custom_dirs.contains(&dir) {
            custom_dirs.push(dir);
        }
    }
    let sidecar = state.sidecar.clone();
    tauri::async_runtime::spawn_blocking(move || {
        sidecar.stop();
        let _ = app.autolaunch().disable();
        let errors = wipe_all(&custom_dirs);
        if !errors.is_empty() {
            let _ = app
                .dialog()
                .message(format!(
                    "Some files could not be deleted:\n\n{}",
                    errors.join("\n")
                ))
                .title("Knowledge Arch")
                .kind(MessageDialogKind::Error)
                .blocking_show();
        }
        app.exit(0);
    })
    .await
    .map_err(|e| e.to_string())
}

// ── 菜单与托盘 ─────────────────────────────────────────────────

#[cfg(target_os = "macos")]
fn build_menu(app: &AppHandle) -> tauri::Result<Menu<Wry>> {
    let preferences = MenuItemBuilder::with_id(MENU_PREFERENCES, "Preferences…")
        .accelerator("CmdOrCtrl+,")
        .build(app)?;
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
    // macOS 上复制 / 粘贴等快捷键依赖「编辑」菜单
    let edit = SubmenuBuilder::new(app, "Edit")
        .undo()
        .redo()
        .separator()
        .cut()
        .copy()
        .paste()
        .select_all()
        .build()?;
    let window = SubmenuBuilder::new(app, "Window")
        .minimize()
        .item(&PredefinedMenuItem::close_window(app, None)?)
        .build()?;
    MenuBuilder::new(app)
        .items(&[&app_menu, &edit, &window])
        .build()
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let show = MenuItemBuilder::with_id(MENU_SHOW, "Show window").build(app)?;
    let preferences = MenuItemBuilder::with_id(MENU_PREFERENCES, "Preferences…").build(app)?;
    let quit = MenuItemBuilder::with_id(MENU_QUIT, "Quit Knowledge Arch").build(app)?;
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
    let builder = tauri::Builder::default()
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
        ));
    #[cfg(feature = "updater")]
    let builder = builder.plugin(updater::plugin());
    let app = builder
        .setup(|app| {
            let handle = app.handle().clone();
            let prefs = ShellPrefs::load();
            publish_data_dir(&prefs);
            let sidecar = Sidecar::new(handle.clone(), launch_spec(&handle, &prefs));
            sidecar.start();
            if prefs.check_updates {
                updater::spawn_startup_check(handle.clone());
            }
            app.manage(ShellState {
                launched_data_dir: Mutex::new(prefs.data_dir_override()),
                prefs: Mutex::new(prefs),
                sidecar,
            });

            #[cfg(target_os = "macos")]
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
            quit_app,
            wipe_data_and_quit,
            updater::check_update,
            updater::install_update,
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
