//! PictureHouse desktop app.
//!
//! A native window around the PictureHouse web console, plus what a browser
//! tab can't do: a tray icon, starting and stopping the media server, keeping
//! it running when the window is closed (TVs still need it), single instance,
//! and "start when I sign in".
//!
//! The window first shows a local splash page (ui/index.html) and navigates
//! to the server once it answers. The console is loaded as a remote page with
//! no access to Tauri's IPC: it's the same web app a browser gets.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod server;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use server::{Server, Started};
use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_opener::OpenerExt;

const MAIN: &str = "main";

/// "Keep serving when the window is closed" (tray setting, off by default).
static KEEP_SERVING: AtomicBool = AtomicBool::new(false);

#[derive(Default, serde::Serialize, serde::Deserialize)]
struct Prefs {
    #[serde(default)]
    keep_serving_in_tray: bool,
}

fn prefs_path(app: &AppHandle) -> Option<std::path::PathBuf> {
    app.path().app_config_dir().ok().map(|d| d.join("desktop.json"))
}

fn load_prefs(app: &AppHandle) -> Prefs {
    prefs_path(app)
        .and_then(|p| std::fs::read(p).ok())
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or_default()
}

fn save_prefs(app: &AppHandle, prefs: &Prefs) {
    if let Some(path) = prefs_path(app) {
        if let Some(dir) = path.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        let _ = std::fs::write(path, serde_json::to_vec_pretty(prefs).unwrap_or_default());
    }
}
const TRAY: &str = "tray";

/// Add folder -> Browse, inside the desktop app: the system's own folder
/// picker (the console asks via window.__TAURI__; in a plain browser it uses
/// its in-page folder browser instead). The server runs on this computer, so
/// a path picked here is a path the server can read. None = cancelled.
#[tauri::command]
async fn pick_folder(app: AppHandle, start: Option<String>) -> Option<String> {
    use tauri_plugin_dialog::DialogExt;
    let mut dialog = app.dialog().file().set_title("Choose a folder for PictureHouse");
    if let Some(dir) = start.filter(|d| std::path::Path::new(d).is_dir()) {
        dialog = dialog.set_directory(dir);
    }
    if let Some(window) = app.get_webview_window(MAIN) {
        dialog = dialog.set_parent(&window);
    }
    dialog.blocking_pick_folder().map(|p| p.to_string())
}

fn main() {
    let minimized = std::env::args().any(|a| a == "--minimized");

    let app = tauri::Builder::default()
        // A second launch just brings the existing window forward.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_window(app)))
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec!["--minimized"])))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![pick_folder])
        .setup(move |app| {
            let handle = app.handle().clone();
            KEEP_SERVING.store(load_prefs(&handle).keep_serving_in_tray, Ordering::Relaxed);
            // The installed app ships the frozen server as a resource; in
            // development the project folder is used instead.
            let resources = app.path().resource_dir().ok();
            let server = Arc::new(Server::new(resources.as_deref()));
            app.manage(server.clone());
            let window = build_window(&handle, server.port(), !minimized)?;
            build_tray(&handle)?;
            spawn_start(handle.clone(), window);
            spawn_watchdog(handle);
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                if KEEP_SERVING.load(Ordering::Relaxed) {
                    // Opted in: hide to the tray, TVs and phones keep playing.
                    let _ = window.hide();
                } else {
                    // Default: closing quits. RunEvent::Exit stops the server
                    // cleanly and frees its ports, ready for a clean restart.
                    let _ = window.hide();
                    window.app_handle().exit(0);
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building PictureHouse");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            let server = handle.state::<Arc<Server>>().inner().clone();
            server.stop(); // only stops a server this app started
        }
    });
}

// ---------------------------------------------------------------- window

fn build_window(app: &AppHandle, port: u16, visible: bool) -> tauri::Result<WebviewWindow> {
    let opener = app.clone();
    WebviewWindowBuilder::new(app, MAIN, WebviewUrl::App("index.html".into()))
        .title("PictureHouse")
        .inner_size(1280.0, 820.0)
        .min_inner_size(420.0, 560.0)
        .center()
        .visible(visible)
        .on_navigation(move |url| {
            // Stay on the splash page or the local server; open anything else
            // (TMDb links, help pages) in the default browser instead.
            if is_app_url(url, port) {
                return true;
            }
            let _ = opener.opener().open_url(url.as_str(), None::<&str>);
            false
        })
        .build()
}

/// The bundled splash page. Tauri serves app pages from `http://tauri.localhost`
/// on Windows (WebView2) but from `tauri://localhost` on Linux (WebKitGTK).
fn splash_url() -> Url {
    let base = if cfg!(windows) { "http://tauri.localhost" } else { "tauri://localhost" };
    Url::parse(&format!("{base}/index.html")).expect("valid splash url")
}

fn is_app_url(url: &Url, port: u16) -> bool {
    let host = url.host_str().unwrap_or_default();
    match url.scheme() {
        "tauri" => true,
        "http" | "https" if host == "tauri.localhost" => true,
        "http" => (host == "127.0.0.1" || host == "localhost") && url.port() == Some(port),
        "about" | "data" | "blob" => true,
        _ => false,
    }
}

fn main_window(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window(MAIN)
}

fn show_window(app: &AppHandle) {
    if let Some(w) = main_window(app) {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn splash_call(window: &WebviewWindow, js_fn: &str, args: &[&str]) {
    let args: Vec<String> = args.iter().map(|a| serde_json::to_string(a).unwrap()).collect();
    let _ = window.eval(format!("window.{js_fn} && window.{js_fn}({})", args.join(",")));
}

fn set_tooltip(app: &AppHandle, text: &str) {
    if let Some(tray) = app.tray_by_id(TRAY) {
        let _ = tray.set_tooltip(Some(text));
    }
}

// ---------------------------------------------------------------- server lifecycle

fn spawn_start(app: AppHandle, window: WebviewWindow) {
    std::thread::spawn(move || {
        let server = app.state::<Arc<Server>>().inner().clone();
        set_tooltip(&app, "PictureHouse: starting…");
        let progress = |text: &str| splash_call(&window, "phStatus", &[text]);
        match server.start(&progress) {
            Ok(started) => {
                let note = match started {
                    Started::Attached(name) => format!("PictureHouse: using the running server ({name})"),
                    Started::Spawned => "PictureHouse: running".to_string(),
                };
                set_tooltip(&app, &note);
                if let Ok(url) = Url::parse(&server.url()) {
                    let _ = window.navigate(url);
                }
            }
            Err(msg) => {
                set_tooltip(&app, "PictureHouse: server not running");
                splash_call(&window, "phError", &["The media server didn't start", &msg]);
                show_window(&app);
            }
        }
    });
}

/// Restart a server that crashed (at most a few times, with backoff).
fn spawn_watchdog(app: AppHandle) {
    std::thread::spawn(move || {
        let server = app.state::<Arc<Server>>().inner().clone();
        let mut restarts = 0u32;
        loop {
            std::thread::sleep(Duration::from_secs(3));
            if !server.crashed() {
                continue;
            }
            let tail = server.console_log().map(|p| server::tail(&p, 20)).unwrap_or_default();
            if restarts >= 3 {
                set_tooltip(&app, "PictureHouse: server stopped (crashed repeatedly)");
                if let Some(w) = main_window(&app) {
                    let _ = w.navigate(splash_url());
                    std::thread::sleep(Duration::from_millis(600));
                    splash_call(&w, "phError", &["The media server keeps stopping", &tail]);
                }
                return;
            }
            restarts += 1;
            set_tooltip(&app, "PictureHouse: server stopped, restarting…");
            std::thread::sleep(Duration::from_secs(2u64.pow(restarts)));
            if let Some(w) = main_window(&app) {
                restart(&app, &w);
            }
        }
    });
}

fn restart(app: &AppHandle, window: &WebviewWindow) {
    let server = app.state::<Arc<Server>>().inner().clone();
    server.stop();
    // Back to the splash page while the server comes up again.
    let _ = window.navigate(splash_url());
    std::thread::sleep(Duration::from_millis(500));
    spawn_start(app.clone(), window.clone());
}

// ---------------------------------------------------------------- tray

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let autostart_on = app.autolaunch().is_enabled().unwrap_or(false);
    let open = MenuItem::with_id(app, "open", "Open PictureHouse", true, None::<&str>)?;
    let browser = MenuItem::with_id(app, "browser", "Open in web browser", true, None::<&str>)?;
    let scan = MenuItem::with_id(app, "scan", "Scan libraries now", true, None::<&str>)?;
    let restart_item = MenuItem::with_id(app, "restart", "Restart server", true, None::<&str>)?;
    let logs = MenuItem::with_id(app, "logs", "Open logs folder", true, None::<&str>)?;
    let autostart = CheckMenuItem::with_id(app, "autostart", "Start when I sign in", true,
                                           autostart_on, None::<&str>)?;
    let keep = CheckMenuItem::with_id(app, "keep", "Keep serving when the window is closed", true,
                                      KEEP_SERVING.load(Ordering::Relaxed), None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit PictureHouse", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[
        &open, &browser, &PredefinedMenuItem::separator(app)?,
        &scan, &restart_item, &logs, &PredefinedMenuItem::separator(app)?,
        &keep, &autostart, &PredefinedMenuItem::separator(app)?, &quit,
    ])?;

    let autostart_item = autostart.clone();
    let keep_item = keep.clone();
    let mut tray = TrayIconBuilder::with_id(TRAY)
        .tooltip("PictureHouse")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(move |app, event| {
            if event.id.as_ref() == "keep" {
                let on = !KEEP_SERVING.load(Ordering::Relaxed);
                KEEP_SERVING.store(on, Ordering::Relaxed);
                let _ = keep_item.set_checked(on);
                save_prefs(app, &Prefs { keep_serving_in_tray: on });
                return;
            }
            on_menu(app, event.id.as_ref(), &autostart_item)
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                show_window(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app)?;
    Ok(())
}

fn on_menu(app: &AppHandle, id: &str, autostart: &CheckMenuItem<tauri::Wry>) {
    let server = app.state::<Arc<Server>>().inner().clone();
    match id {
        "open" => show_window(app),
        "browser" => {
            let _ = app.opener().open_url(server.url(), None::<&str>);
        }
        "scan" => {
            let port = server.port();
            let app = app.clone();
            std::thread::spawn(move || {
                let note = match server::post(port, "/api/scan") {
                    Ok(()) => "PictureHouse: scanning libraries…",
                    Err(_) => "PictureHouse: couldn't start a scan (is the server running?)",
                };
                set_tooltip(&app, note);
            });
        }
        "restart" => {
            if !server.owns_server() && server::probe(server.port()).is_some() {
                // Started from a terminal or run-server.bat: not ours to restart.
                set_tooltip(app, "PictureHouse: the server was started outside the app; restart it there");
                return;
            }
            if let Some(w) = main_window(app) {
                let app = app.clone();
                std::thread::spawn(move || restart(&app, &w));
            }
        }
        "logs" => {
            let dir = server::log_dir();
            let _ = std::fs::create_dir_all(&dir);
            let _ = app.opener().open_path(dir.to_string_lossy(), None::<&str>);
        }
        "autostart" => {
            let mgr = app.autolaunch();
            let enabled = mgr.is_enabled().unwrap_or(false);
            let result = if enabled { mgr.disable() } else { mgr.enable() };
            let now = mgr.is_enabled().unwrap_or(enabled);
            let _ = autostart.set_checked(now);
            if result.is_err() {
                set_tooltip(app, "PictureHouse: couldn't change the sign-in setting");
            }
        }
        "quit" => app.exit(0),
        _ => {}
    }
}
