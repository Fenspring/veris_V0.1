//! Veris Desktop shell.
//!
//! The shell owns three things the core deliberately does not: the OS keychain,
//! process supervision, and the installer/updater. Everything else — connectors,
//! sync, the knowledge graph, agents — lives in the packaged core, which is the
//! same software that runs in a container or on a server.
//!
//! NOTE: scaffolded and not yet built on Windows or macOS. See desktop/README.md.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpListener;
use tauri::Manager;
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandEvent;

/// Bind to port 0 and let the OS choose. A fixed port would collide with
/// whatever else the hospital workstation is running, and would let any local
/// process guess where the core is listening.
fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .expect("no local port available")
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            let port = free_port();
            let data_dir = app
                .path()
                .app_data_dir()
                .expect("no application data directory");
            std::fs::create_dir_all(&data_dir).ok();

            // The core binds to loopback only. Nothing Veris runs is reachable
            // from the network unless the operator deliberately deploys the
            // server build instead.
            let (mut rx, _child) = app
                .shell()
                .sidecar("veris-core")
                .expect("veris-core sidecar missing from the bundle")
                .env("HOST", "127.0.0.1")
                .env("PORT", port.to_string())
                .env("VERIS_DATA_DIR", data_dir.to_string_lossy().to_string())
                .spawn()
                .expect("failed to start the Veris core");

            // Surface core failures rather than leaving a blank window. A
            // desktop app that silently shows nothing is indistinguishable from
            // a broken install.
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    if let CommandEvent::Stderr(line) = event {
                        eprintln!("[veris-core] {}", String::from_utf8_lossy(&line));
                    }
                }
            });

            let window = app.get_webview_window("main").expect("no main window");
            window
                .eval(&format!("window.__VERIS_API__ = 'http://127.0.0.1:{port}';"))
                .ok();
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Veris");
}
