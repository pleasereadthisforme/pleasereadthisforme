//! Local status-only window. No key value or credential command is exposed to web IPC.
use std::{sync::mpsc, time::Duration};
use tauri::{AppHandle, WebviewUrl, WebviewWindowBuilder};

pub fn choose(app: &AppHandle, configured: bool) -> Result<Option<&'static str>, String> {
    let (tx, rx) = mpsc::channel();
    let navigation_tx = tx.clone();
    let window = WebviewWindowBuilder::new(app, "key-manager", WebviewUrl::App("api-key.html".into()))
        .title(crate::locale::text("OpenAI API 키")).inner_size(480.0, 370.0).resizable(false).center()
        .initialization_script(format!("window.__keyConfigured={configured};"))
        .on_navigation(move |url| {
            if url.scheme() == "https" && url.host_str() == Some("prtfm-key-action.invalid") {
                let action = match url.path() {
                    "/save" => Some("save"),
                    "/delete" if configured => Some("delete"),
                    "/cancel" => Some("cancel"),
                    _ => None,
                };
                if let Some(action) = action { let _ = navigation_tx.send(action); }
                return false;
            }
            // Only the bundled management page may navigate in this window.
            (url.scheme() == "tauri" && url.host_str() == Some("localhost")
                || url.scheme() == "http" && url.host_str() == Some("tauri.localhost"))
                && url.path() == "/api-key.html"
        })
        .build().map_err(|_| crate::locale::text("키 관리 창을 열지 못했습니다."))?;
    window.on_window_event(move |event| {
        if matches!(event, tauri::WindowEvent::CloseRequested { .. } | tauri::WindowEvent::Destroyed) {
            let _ = tx.send("cancel");
        }
    });
    let action = rx.recv_timeout(Duration::from_secs(600)).unwrap_or("cancel");
    let _ = window.destroy();
    Ok(match action { "save" => Some("save"), "delete" => Some("delete"), _ => None })
}
