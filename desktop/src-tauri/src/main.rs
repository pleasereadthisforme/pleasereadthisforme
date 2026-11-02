#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
#[cfg(not(windows))]
compile_error!("This initial desktop host targets Windows only.");
mod sidecar;
mod locale;
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
};
use tauri::{Emitter, Listener, Manager, WebviewUrl, WebviewWindowBuilder};

mod startup_timing {
    static START: std::sync::OnceLock<std::time::Instant> = std::sync::OnceLock::new();
    pub fn mark(stage: &str) {
        let start = START.get_or_init(std::time::Instant::now);
        if std::env::var("PRTFM_STARTUP_TIMING").as_deref() == Ok("1") {
            println!("PRTFM_STARTUP {} {}", stage, start.elapsed().as_millis());
        }
    }
}

type OwnedSidecar = Arc<Mutex<Option<sidecar::Sidecar>>>;
fn main() {
    startup_timing::mark("host-entry");
    let process: OwnedSidecar = Arc::new(Mutex::new(None));
    let owned = process.clone();
    let app = tauri::Builder::default()
        .setup(move |app| {
            startup_timing::mark("tauri-setup");
            let handle = app.handle().clone();
            let owned = owned.clone();
            std::thread::spawn(move || {
                let start = || -> Result<(), String> {
                    let root = match std::env::var_os("PRTFM_DESKTOP_RUNTIME") {
                        Some(path) => PathBuf::from(path),
                        None => handle.path().resource_dir().map_err(|_| "Cannot locate resources")?.join("runtime-bundle"),
                    }.canonicalize().map_err(|_| "Runtime bundle not found")?;
                    locale::initialize(&handle.path().app_config_dir().map_err(|_| "Settings path unavailable")?, root.parent().ok_or("Resources path unavailable")?);
                    let preview = handle.config().identifier == "app.prtfm.desktop.preview";
                    startup_timing::mark("storage-check-start");
                    if !sidecar::recover_before_start(&root, preview)?
                        || (preview && !sidecar::first_run_before_start(&root)?) {
                        handle.exit(0);
                        return Ok(());
                    }
                    startup_timing::mark("storage-check-end");
                    let (child, info) = sidecar::Sidecar::start(&root, preview)?;
                    *owned.lock().map_err(|_| "Host state unavailable")? = Some(child);
                    let origin = format!("http://127.0.0.1:{}", info.port);
                    let url: tauri::Url = format!("{origin}/activity-ui/").parse().map_err(|_| "Invalid local URL")?;
                    let expected = origin.clone();
                    let credential = serde_json::to_string(&info.token).map_err(|_| "Credential serialization failed")?;
                    let init = format!("if(location.origin === {}) {{sessionStorage.setItem('prtfm-server-token', {});}}",
                        serde_json::to_string(&origin).unwrap(), credential);
                    startup_timing::mark("webview-build-start");
                    let name_path = handle.path().app_config_dir().map_err(|_| "Settings path unavailable")?.join("agent-name.txt");
                    let agent_name = std::fs::read_to_string(&name_path).unwrap_or_default();
                    let settings_init = format!("window.__prtfmDesktop=true;window.__prtfmAgentName={};window.__prtfmLanguage=\"{}\";", serde_json::to_string(&agent_name).unwrap(), locale::language());
                    let navigation_handle = handle.clone();
                    let workspace = WebviewWindowBuilder::new(&handle, "workspace", WebviewUrl::External(url))
                        .title("Please read this for me").inner_size(1280.0, 850.0)
                        .initialization_script(&init)
                        .initialization_script(&settings_init)
                        .on_navigation(move |url| {
                            if url.origin().ascii_serialization() != expected { return false; }
                            if let Some(action) = url.path().strip_prefix("/__desktop/") {
                                if action == "language" {
                                    let value = url.query_pairs().find(|(key, _)| key == "value").map(|(_, value)| value.into_owned()).unwrap_or_default();
                                    if matches!(value.as_str(), "en" | "ko") {
                                        let saved = name_path.parent().map(|dir| std::fs::create_dir_all(dir).is_ok() && std::fs::write(dir.join("ui-language.txt"), &value).is_ok()).unwrap_or(false);
                                        if saved { locale::set_language(&value); }
                                        if let Some(window) = navigation_handle.get_webview_window("workspace") {
                                            let _ = window.eval(format!("window.dispatchEvent(new CustomEvent('language-saved',{{detail:{{ok:{saved},language:{}}}}}))", serde_json::to_string(&value).unwrap()));
                                        }
                                    }
                                } else if action == "agent-name" {
                                    let name = url.query_pairs().find(|(k, _)| k == "name").map(|(_, v)| v.trim().to_string()).unwrap_or_default();
                                    if !name.is_empty() && name.chars().count() <= 40 && !name.chars().any(char::is_control) {
                                        let saved = name_path.parent().map(|p| std::fs::create_dir_all(p).is_ok()).unwrap_or(false) && std::fs::write(&name_path, &name).is_ok();
                                        if let Some(window) = navigation_handle.get_webview_window("workspace") {
                                            let _ = window.eval(format!("window.dispatchEvent(new CustomEvent('agent-name-saved',{{detail:{{ok:{saved},name:{}}}}}))", serde_json::to_string(&name).unwrap()));
                                        }
                                    }
                                } else if matches!(action, "key-status" | "key-save" | "key-delete" | "storage-backup") {
                                    let _ = navigation_handle.emit("desktop-settings-action", action);
                                }
                                return false;
                            }
                            true
                        })
                        .build().map_err(|_| "Cannot open application window")?;
                    startup_timing::mark("webview-build-end");
                    let closing = Arc::new(AtomicBool::new(false));
                    let backup_busy = closing.clone();
                    let backup_owned = owned.clone();
                    let backup_root = root.clone();
                    let backup_info = info.clone();
                    let backup_app = handle.clone();
                    let action_window = workspace.clone();
                    handle.listen("desktop-settings-action", move |event| {
                        let window = &action_window;
                        let selected: String = serde_json::from_str(event.payload()).unwrap_or_default();
                        let key_action = matches!(selected.as_str(), "key-status" | "key-save" | "key-delete");
                        if (!key_action && selected != "storage-backup") || backup_busy.swap(true, Ordering::SeqCst) { return; }
                        let (busy, owned, root, info, app, window) = (backup_busy.clone(), backup_owned.clone(),
                            backup_root.clone(), backup_info.clone(), backup_app.clone(), window.clone());
                        std::thread::spawn(move || {
                            let action = || -> Result<bool, String> {
                                if key_action {
                                    if selected == "key-status" {
                                        let report = sidecar::startup_command(&root, "prtfm.credentials.ui", "status", None, preview)?;
                                        if report["status"] != "ready" { return Err(crate::locale::text("키 등록 상태를 확인하지 못했습니다.").into()); }
                                        let configured = report["configured"] == true;
                                        let _ = window.eval(format!("window.dispatchEvent(new CustomEvent('key-state',{{detail:{{configured:{configured}}}}}))"));
                                        return Ok(false);
                                    }
                                    let parent = window.hwnd().map_err(|_| crate::locale::text("앱 창을 확인하지 못했습니다."))?.0 as isize;
                                    if sidecar::request_close(&info, "check") != Ok(true) {
                                        let _ = sidecar::request_close(&info, "abort");
                                        return Err(crate::locale::text("작업 완료 후 키 설정을 변경하세요.").into());
                                    }
                                    let action = if selected == "key-save" { "save" } else { "delete" };
                                    let result = sidecar::startup_command(&root, "prtfm.credentials.ui", action, Some(&parent.to_string()), preview);
                                    let _ = window.set_focus();
                                    match result {
                                        Ok(report) if report["status"] == "cancelled" => {
                                            let _ = sidecar::request_close(&info, "abort");
                                            let _ = window.eval(crate::locale::text("window.dispatchEvent(new CustomEvent('key-state',{detail:{message:'등록을 취소했습니다.'}}))"));
                                            return Ok(false);
                                        }
                                        Ok(report) if report["status"] == "saved" || report["status"] == "deleted" => {
                                            let _ = window.eval(crate::locale::text("window.dispatchEvent(new CustomEvent('key-state',{detail:{message:'변경을 적용하기 위해 앱을 다시 시작합니다.'}}))"));
                                            if let Some(mut child) = owned.lock().map_err(|_| "Host state unavailable")?.take() {
                                                let _ = child.stop_for_backup();
                                            }
                                            app.restart();
                                        }
                                        _ => {
                                            let _ = sidecar::request_close(&info, "abort");
                                            return Err(crate::locale::text("키 변경을 완료하지 못했습니다. 등록 상태를 다시 확인하세요.").into());
                                        }
                                    }

                                }
                                let Some(path) = rfd::FileDialog::new().set_title(crate::locale::text("백업 저장 폴더 선택")).pick_folder() else { return Ok(false); };
                                let path = path.to_string_lossy();
                                let plan = sidecar::startup_command(&root, "prtfm.store.backup_ui", "plan", Some(&path), preview)?;
                                if plan["status"] != "planned" { return Err(plan["message"].as_str().unwrap_or(crate::locale::text("백업 계획을 확인하지 못했습니다.")).into()); }
                                let choice = rfd::MessageDialog::new().set_title(crate::locale::text("백업 만들기"))
                                    .set_description(if crate::locale::english() { format!("Backup: {} files, {} bytes\nFree space required: {} bytes\nOriginal documents and API keys are excluded. The backup is not encrypted. Pause the app, back up, and restart?",
                                        plan["included_files"], plan["included_bytes"], plan["required_free_bytes"]) } else { format!("백업 대상: {} 파일, {} bytes\n필요 여유 공간: {} bytes\n원본 문서·API 키는 제외되며 암호화되지 않습니다. 앱을 잠시 중지하고 백업 후 다시 시작할까요?",
                                        plan["included_files"], plan["included_bytes"], plan["required_free_bytes"]) })
                                    .set_buttons(rfd::MessageButtons::YesNo).show();
                                if choice != rfd::MessageDialogResult::Yes { return Ok(false); }
                                if sidecar::request_close(&info, "check") != Ok(true) {
                                    let _ = sidecar::request_close(&info, "abort");
                                    return Err(crate::locale::text("작업 중이거나 상태를 확인할 수 없습니다. 작업 완료 후 다시 시도하세요.").into());
                                }
                                let child = owned.lock().map_err(|_| "Host state unavailable")?.take();
                                let Some(mut child) = child else {
                                    let _ = sidecar::request_close(&info, "abort");
                                    return Err(crate::locale::text("서버 상태를 확인하지 못했습니다.").into());
                                };
                                let _ = window.hide();
                                let stopped = child.stop_for_backup();
                                drop(child);
                                let result = if stopped {
                                    sidecar::startup_command(&root, "prtfm.store.backup_ui", "create", Some(&path), preview)
                                } else { Err(crate::locale::text("정상 종료를 확인하지 못해 백업을 실행하지 않았습니다.").into()) };
                                let message = match result {
                                    Ok(report) if report["status"] == "created" => if crate::locale::english() { format!("Backup saved.\n{}", report["path"].as_str().unwrap_or("")) } else { format!("백업을 저장했습니다.\n{}", report["path"].as_str().unwrap_or("")) },
                                    Ok(report) => report["message"].as_str().unwrap_or(crate::locale::text("백업을 완료하지 못했습니다. 기존 데이터는 보존했습니다.")).to_string(),
                                    Err(error) => error,
                                };
                                rfd::MessageDialog::new().set_title(crate::locale::text("백업 결과")).set_description(message).show();
                                app.restart();
                            };
                            if let Err(error) = action() {
                                if key_action {
                                    let text = serde_json::to_string(&error).unwrap();
                                    let _ = window.eval(format!("window.dispatchEvent(new CustomEvent('key-state',{{detail:{{message:{text}}}}}))"));
                                } else { rfd::MessageDialog::new().set_title(crate::locale::text("백업 안내")).set_description(error).show(); }
                            }
                            busy.store(false, Ordering::SeqCst);
                        });
                    });
                    let close_window = workspace.clone();
                    let close_info = info.clone();
                    workspace.on_window_event(move |event| {
                        if matches!(event, tauri::WindowEvent::Focused(_) | tauri::WindowEvent::Resized(_)) {
                            // WebView2 can keep document.hidden/hasFocus unchanged when minimized.
                            let active = !close_window.is_minimized().unwrap_or(true)
                                && match event {
                                    tauri::WindowEvent::Focused(focused) => *focused,
                                    _ => close_window.is_focused().unwrap_or(false),
                                };
                            let _ = close_window.eval(&format!(
                                "window.__prtfmCharacterActive={active};window.dispatchEvent(new Event('character-window-state'));"
                            ));
                        }
                        if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                            api.prevent_close();
                            if closing.swap(true, Ordering::SeqCst) { return; }
                            let window = close_window.clone();
                            let info = close_info.clone();
                            let closing = closing.clone();
                            std::thread::spawn(move || {
                                let check = sidecar::request_close(&info, "check");
                                let approved = match check {
                                    Ok(true) => true,
                                    _ => {
                                        let message = if check.is_ok() {
                                            crate::locale::text("문서 처리 또는 답변 생성 중입니다. 종료하면 진행 중인 작업이 중단됩니다. 이미 전송된 모델 요청은 비용이 발생할 수 있습니다. 종료할까요?")
                                        } else {
                                            crate::locale::text("작업 상태를 확인할 수 없습니다. 종료하면 진행 중인 작업이 중단될 수 있습니다. 종료할까요?")
                                        };
                                        let choice = rfd::MessageDialog::new().set_title(crate::locale::text("앱 종료 확인"))
                                            .set_description(message).set_buttons(rfd::MessageButtons::YesNo)
                                            .set_level(rfd::MessageLevel::Warning).show();
                                        if choice == rfd::MessageDialogResult::Yes {
                                            let _ = sidecar::request_close(&info, "confirm");
                                            true
                                        } else {
                                            let _ = sidecar::request_close(&info, "abort");
                                            false
                                        }
                                    }
                                };
                                if approved && window.destroy().is_err() {
                                    let _ = sidecar::request_close(&info, "abort");
                                }
                                closing.store(false, Ordering::SeqCst);
                            });
                        }
                    });
                    if let Some(boot) = handle.get_webview_window("boot") { let _ = boot.close(); }
                    Ok(())
                };
                if let Err(error) = start() {
                    if let Ok(mut child) = owned.lock() { child.take(); }
                    rfd::MessageDialog::new().set_title(crate::locale::text("앱 시작 실패")).set_description(error)
                        .set_level(rfd::MessageLevel::Error).show();
                    handle.exit(1);
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!()).expect("Desktop initialization failed");
    app.run(move |_handle, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            if let Ok(mut child) = process.lock() {
                child.take();
            }
        }
    });
}
