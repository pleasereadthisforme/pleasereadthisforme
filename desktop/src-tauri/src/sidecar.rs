use serde::Deserialize;
use std::os::windows::io::AsRawHandle;
use std::os::windows::process::CommandExt;
use std::{
    io::{BufRead, BufReader, Read, Write},
    net::{SocketAddr, TcpStream},
    path::Path,
    process::{Child, Command, Stdio},
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};
use windows_sys::Win32::{
    Foundation::{CloseHandle, HANDLE},
    System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    },
};

// HANDLE ownership can move between threads; it is never shared for mutation.
struct Job(isize);
impl Job {
    fn attach(child: &Child) -> Result<Self, String> {
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() {
                return Err("Cannot create sidecar job".into());
            }
            let job = Self(handle as isize);
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &info as *const _ as _,
                std::mem::size_of_val(&info) as u32,
            ) == 0
                || AssignProcessToJobObject(handle, child.as_raw_handle() as HANDLE) == 0
            {
                return Err("Cannot assign sidecar job".into());
            }
            Ok(job)
        }
    }
}
impl Drop for Job {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.0 as HANDLE);
        }
    }
}

pub fn startup_command(root: &Path, module: &str, action: &str, target: Option<&str>, preview: bool) -> Result<serde_json::Value, String> {
    let mut command = Command::new(root.join("python/python.exe"));
    command.env("PRTFM_UI_LANGUAGE", crate::locale::language());
    command.args(["-X", "utf8", "-B", "-m", module, action]);
    if preview { command.args(["--channel", "preview"]); }
    if let Some(path) = target { command.args(["--target", path]); }
    let mut child = command.current_dir(root).stdin(Stdio::null())
        .stdout(Stdio::piped()).stderr(Stdio::null()).creation_flags(0x08000000)
        .spawn().map_err(|_| crate::locale::text("가져오기 상태 확인을 시작하지 못했습니다."))?;
    let _job = Job::attach(&child).map_err(|error| { let _ = child.kill(); let _ = child.wait(); error })?;
    let stdout = child.stdout.take().ok_or(crate::locale::text("상태 응답을 읽을 수 없습니다."))?;
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let mut bytes = Vec::new();
        let result = stdout.take(65537).read_to_end(&mut bytes).map(|_| bytes);
        let _ = tx.send(result);
    });
    let timeout = if module == "prtfm.credentials.ui" || (module == "prtfm.store.first_run_ui" && (action == "import" || action == "import-legacy"))
        || (module == "prtfm.store.backup_ui" && (action == "create" || action == "restore")) { 330 } else { 60 };
    let result = rx.recv_timeout(Duration::from_secs(timeout));
    let _ = child.kill();
    let _ = child.wait();
    let bytes = result.map_err(|_| crate::locale::text("가져오기 확인 시간이 초과되었습니다. 데이터를 보존했습니다."))?
        .map_err(|_| crate::locale::text("가져오기 상태를 읽지 못했습니다."))?;
    if bytes.len() > 65536 { return Err(crate::locale::text("가져오기 응답이 너무 큽니다.").into()); }
    serde_json::from_slice(&bytes).map_err(|_| crate::locale::text("가져오기 상태를 확인하지 못했습니다. 앱을 다시 설치하거나 진단을 요청해 주세요.").into())
}

pub fn recover_before_start(root: &Path, preview: bool) -> Result<bool, String> {
    let report = startup_command(root, "prtfm.store.import_recovery_ui", "status", None, preview)?;
    if report["status"] == "not_pending" { return Ok(true); }
    let target = report["path"].as_str().ok_or(crate::locale::text("복구 대상 경로를 확인하지 못했습니다."))?;
    let actions = report["actions"].as_array().ok_or(crate::locale::text("복구 선택을 확인하지 못했습니다."))?;
    let mut action = None;
    if actions.iter().any(|a| a == "resume") {
        let choice = rfd::MessageDialog::new().set_title(crate::locale::text("중단된 가져오기"))
            .set_description(if crate::locale::english() { format!("Destination: {target}\n\nA completed check record exists. Verify the data again and finish importing?\nChoose No to roll back or exit.") } else { format!("대상: {target}\n\n검사 완료 기록이 있습니다. 데이터를 다시 검증한 뒤 가져오기를 완료할까요?\n아니요를 선택하면 되돌리기 또는 종료를 선택할 수 있습니다.") })
            .set_buttons(rfd::MessageButtons::YesNo).show();
        if choice == rfd::MessageDialogResult::Yes { action = Some("resume"); }
    }
    if action.is_none() && actions.iter().any(|a| a == "rollback") {
        let explanation = if report["phase"] == "original_in_place" {
            crate::locale::text("기존 저장소는 원래 위치에 그대로 있습니다. 중단된 가져오기를 취소하고 앱을 종료할까요? 저장소는 이동하거나 삭제하지 않습니다. 아니요를 선택하면 변경 없이 종료합니다.")
        } else if report["phase"] == "restore_initialization" {
            crate::locale::text("보존해 둔 빈 저장소를 원래 위치로 복원하고 앱을 종료할까요? 데이터는 삭제하지 않습니다. 아니요를 선택하면 변경 없이 종료합니다.")
        } else {
            crate::locale::text("미완료 데이터를 임시 위치로 되돌리고 앱을 종료할까요? 원본과 가져온 데이터는 삭제하지 않습니다. 아니요를 선택하면 변경 없이 종료합니다.")
        };
        let choice = rfd::MessageDialog::new().set_title(crate::locale::text("가져오기 되돌리기"))
            .set_description(if crate::locale::english() { format!("Destination: {target}\n\n{explanation}") } else { format!("대상: {target}\n\n{explanation}") })
            .set_buttons(rfd::MessageButtons::YesNo).show();
        if choice == rfd::MessageDialogResult::Yes { action = Some("rollback"); }
    }
    let Some(action) = action else {
        if actions.is_empty() {
            rfd::MessageDialog::new().set_title(crate::locale::text("가져오기 확인 필요"))
                .set_description(crate::locale::text("안전하게 재개하거나 되돌릴 수 없는 상태입니다. 데이터를 보존하고 앱을 종료합니다. 저장소 표식을 직접 삭제하지 마세요."))
                .set_level(rfd::MessageLevel::Warning).show();
        }
        return Ok(false);
    };
    let result = startup_command(root, "prtfm.store.import_recovery_ui", action, Some(target), preview)?;
    if result["status"] == "committed" { return Ok(true); }
    if result["status"] == "original_initialization_restored" {
        rfd::MessageDialog::new().set_title(crate::locale::text("기존 저장소 복원 완료"))
            .set_description(crate::locale::text("가져오기 전의 빈 저장소를 원래 위치에 복원했습니다. 데이터를 삭제하지 않았습니다. 앱을 종료합니다.")).show();
        return Ok(false);
    }
    if result["status"] == "rolled_back_pending" {
        let path = result["path"].as_str().unwrap_or(crate::locale::text("위치를 확인하지 못했습니다."));
        rfd::MessageDialog::new().set_title(crate::locale::text("되돌리기 완료"))
            .set_description(if crate::locale::english() { format!("Incomplete data is preserved at:\n{path}\n\nOriginal data is unchanged. The app will exit.") } else { format!("미완료 데이터를 다음 위치에 보존했습니다:\n{path}\n\n원본 데이터는 변경하지 않았습니다. 앱을 종료합니다.") }).show();
        return Ok(false);
    }
    Err(crate::locale::text("가져오기 복구를 완료하지 못했습니다. 데이터를 보존했습니다. 다시 시작해 상태를 확인해 주세요.").into())
}

#[derive(Clone, Deserialize)]
pub struct Handshake {
    pub port: u16,
    pub token: String,
}
pub fn first_run_before_start(root: &Path) -> Result<bool, String> {
    let module = "prtfm.store.first_run_ui";
    let mut plan = startup_command(root, module, "plan", None, true)?;
    let state = plan["target_state"]["state"].as_str().unwrap_or("blocked");
    if state == "existing_data" || state == "initialized_empty" { return Ok(true); }
    if state != "missing" && state != "empty_directory" {
        return Err(crate::locale::text("이 위치의 기존 데이터를 자동으로 사용하거나 변경할 수 없습니다. 기존 앱으로 열거나 별도의 Preview 저장 위치를 사용해 주세요.").into());
    }
    if state == "missing" {
        let choice = rfd::MessageDialog::new().set_title(crate::locale::text("백업에서 복원"))
            .set_description(crate::locale::text("저장된 백업에서 문서 처리 결과·임베딩·대화 기록을 복원할까요? 원본 문서와 API 키는 포함되지 않습니다. 아니요를 선택하면 기존 시작 절차로 이동합니다."))
            .set_buttons(rfd::MessageButtons::YesNoCancel).show();
        if choice == rfd::MessageDialogResult::Cancel { return Ok(false); }
        if choice == rfd::MessageDialogResult::Yes {
            let Some(path) = rfd::FileDialog::new().set_title(crate::locale::text("복원할 백업 폴더 선택")).pick_folder() else { return Ok(false); };
            let path = path.to_string_lossy();
            let report = startup_command(root, "prtfm.store.backup_ui", "inspect", Some(&path), true)?;
            if report["status"] != "verified" {
                rfd::MessageDialog::new().set_title(crate::locale::text("백업 확인"))
                    .set_description(report["message"].as_str().unwrap_or(crate::locale::text("백업을 확인하지 못했습니다. 기존 데이터를 보존했습니다.")))
                    .show();
                return Ok(false);
            }
            let confirm = rfd::MessageDialog::new().set_title(crate::locale::text("백업 복원 확인"))
                .set_description(if crate::locale::english() { format!("Created: {}\nRestore the selected backup into a new Preview store. The backup is preserved and paid processing will not start automatically. Continue?",
                    report["created_at"].as_str().unwrap_or("")) } else { format!("생성 시각: {}\n선택한 백업을 새 Preview 저장소로 복원합니다. 백업은 보존되며 유료 처리를 자동 실행하지 않습니다. 진행할까요?",
                    report["created_at"].as_str().unwrap_or("")) })
                .set_buttons(rfd::MessageButtons::YesNo).show();
            if confirm != rfd::MessageDialogResult::Yes { return Ok(false); }
            let result = startup_command(root, "prtfm.store.backup_ui", "restore", Some(&path), true)?;
            if result["status"] != "restored" {
                rfd::MessageDialog::new().set_title(crate::locale::text("복원 결과"))
                    .set_description(result["message"].as_str().unwrap_or(crate::locale::text("복원을 완료하지 못했습니다. 앱을 다시 열어 복구 상태를 확인하세요.")))
                    .show();
                return Ok(false);
            }
            return Ok(true);
        }
    }
    let mut legacy_ready = false;
    if plan["source_reason"] == "legacy" {
        let choice = rfd::MessageDialog::new().set_title(crate::locale::text("구버전 데이터 확인"))
            .set_description(crate::locale::text("이전 위치에 소유 표식 없는 데이터가 있습니다. 기존 앱을 종료해 주세요. 원본을 변경하지 않고 형식·본문·벡터를 확인할까요? 이 확인만으로 가져오지는 않습니다. 아니요를 선택하면 새로 시작할 수 있습니다."))
            .set_buttons(rfd::MessageButtons::YesNo).show();
        if choice == rfd::MessageDialogResult::Yes {
            plan = startup_command(root, module, "review-legacy", None, true)?;
            legacy_ready = plan["legacy_review"]["status"] == "recognized_candidate";
            if !legacy_ready || plan["import_assessment"]["status"] != "ready" {
                rfd::MessageDialog::new().set_title(crate::locale::text("구버전 데이터 검토 결과"))
                    .set_description(plan["import_assessment"]["message"].as_str().unwrap_or(crate::locale::text("데이터를 확인하지 못했습니다. 원본은 변경하지 않았습니다. 기존 앱에서 계속 사용할 수 있습니다.")))
                    .set_buttons(rfd::MessageButtons::Ok).show();
            }
        }
    }
    let target = plan["target"].as_str().ok_or(crate::locale::text("저장 위치를 확인하지 못했습니다."))?;
    let source = plan["source"].as_str().unwrap_or("");
    let available = plan["choices"].as_array().map(|a| a.iter().any(|v| v == "review_import")).unwrap_or(false);
    let assessment = &plan["import_assessment"];
    let size_mib = assessment["bytes"].as_u64().unwrap_or(0) as f64 / 1048576.0;
    let files = assessment["files"].as_u64().unwrap_or(0);
    let required_mib = assessment["required_free_bytes"].as_u64().unwrap_or(0) as f64 / 1048576.0;
    if available {
        let choice = rfd::MessageDialog::new().set_title(crate::locale::text("기존 앱 데이터 가져오기"))
            .set_description(if crate::locale::english() { format!("Source: {source}\nNew location: {target}\n\nClose the original app first. Text, embeddings and conversations are copied; originals are preserved. Size: {size_mib:.1} MiB · {files} files\nFree space required: about {required_mib:.1} MiB\nLimits: 1 GiB · 2,000 files · 5 minutes. Duration depends on disk conditions. No reparsing or paid calls. Verify and import? Choose No to start fresh.") } else { format!("원본: {source}\n새 위치: {target}\n\n기존 앱을 종료한 후 진행해 주세요. 본문·임베딩·대화를 복사하며 원본은 보존합니다. 대상: {size_mib:.1} MiB · {files}개 파일\n필요 여유 공간: 약 {required_mib:.1} MiB\n한도: 1 GiB · 2,000개 파일 · 5분. 실제 소요 시간은 디스크 상태에 따라 달라집니다. 재파싱·유료 호출은 하지 않습니다. 가져오기를 검증하고 실행할까요? 아니요를 선택하면 새로 시작할 수 있습니다.") })
            .set_buttons(rfd::MessageButtons::YesNo).show();
        if choice == rfd::MessageDialogResult::Yes {
            let action = if legacy_ready { "import-legacy" } else { "import" };
            let result = startup_command(root, module, action, None, true)?;
            if result["status"] == "committed" { return Ok(true); }
            return Err(result["message"].as_str().unwrap_or(crate::locale::text("가져오기를 완료하지 못했습니다. 원본은 보존했습니다. 다시 시작해 상태를 확인해 주세요.")).into());
        }
    }
    let notice = if let Some(message) = assessment["message"].as_str() {
        message
    } else if plan["source_review_required"] == true && !available {
        crate::locale::text("기존 앱 데이터는 별도 호환성 확인이 필요하여 가져오지 않습니다. 기존 앱에서 계속 사용할 수 있습니다.\n\n")
    } else { "" };
    let choice = rfd::MessageDialog::new().set_title(crate::locale::text("새로 시작"))
        .set_description(if crate::locale::english() { format!("{notice}Storage location: {target}\n\nStart with an empty Preview store without changing existing data? Choose No to exit without changes.") } else { format!("{notice}저장 위치: {target}\n\n기존 자료를 변경하지 않고 빈 Preview 저장소로 시작할까요? 아니요를 선택하면 변경 없이 종료합니다.") })
        .set_buttons(rfd::MessageButtons::YesNo).show();
    if choice != rfd::MessageDialogResult::Yes { return Ok(false); }
    if startup_command(root, module, "start-new", None, true)?["status"] != "ready" {
        return Err(crate::locale::text("새 저장소를 준비하지 못했습니다. 기존 데이터를 변경하지 않았습니다.").into());
    }
    Ok(true)
}

pub struct Sidecar {
    child: Child,
    job: Option<Job>,
}
impl Drop for Sidecar {
    fn drop(&mut self) {
        self.child.stdin.take();
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if matches!(self.child.try_wait(), Ok(Some(_))) {
                break;
            }
            thread::sleep(Duration::from_millis(50));
        }
        self.job.take(); // Also kills descendants, including after a normal Python exit.
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
impl Sidecar {
    pub fn stop_for_backup(&mut self) -> bool {
        self.child.stdin.take();
        let deadline = Instant::now() + Duration::from_secs(10);
        while Instant::now() < deadline {
            if matches!(self.child.try_wait(), Ok(Some(status)) if status.success()) {
                self.job.take();
                return true;
            }
            thread::sleep(Duration::from_millis(50));
        }
        false
    }
    pub fn start(root: &Path, preview: bool) -> Result<(Self, Handshake), String> {
        crate::startup_timing::mark("server-spawn-start");
        let mut command = Command::new(root.join("python/python.exe"));
        command.env("PRTFM_UI_LANGUAGE", crate::locale::language());
        command.args(["-B", "-m", "prtfm.server", "--hosted"]);
        if preview { command.args(["--storage-channel", "preview"]); }
        let child = command
            .env("PRTFM_COMMAND_MANIFEST", root.join("commands.json"))
            .env("PRTFM_RUNTIME_DIR", root.join("runtime"))
            .env("TIKTOKEN_CACHE_DIR", root.join("tokenizer-data"))
            .current_dir(root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .creation_flags(0x08000000) // CREATE_NO_WINDOW
            .spawn()
            .map_err(|_| "Cannot start bundled Python")?;
        crate::startup_timing::mark("server-spawn-end");
        let mut process = Self { child, job: None };
        process.job = Some(Job::attach(&process.child)?);
        let output = process
            .child
            .stdout
            .take()
            .ok_or("Missing handshake pipe")?;
        let (tx, rx) = mpsc::channel();
        let reader = thread::spawn(move || {
            let mut input = BufReader::new(output);
            let mut line = String::new();
            let result = input
                .by_ref()
                .take(4096)
                .read_line(&mut line)
                .map_err(|_| "Cannot read sidecar handshake")
                .and_then(|_| {
                    parse_handshake(&line)
                });
            let _ = tx.send(result);
            // Drain without retaining logs. Never write the credential to a log.
            let _ = std::io::copy(&mut input, &mut std::io::sink());
        });
        process
            .child
            .stdin
            .as_mut()
            .ok_or("Missing start pipe")?
            .write_all(b"go\n")
            .map_err(|_| "Cannot release sidecar start gate")?;
        let received = rx.recv_timeout(Duration::from_secs(45));
        let info = match received {
            Ok(Ok(value)) => value,
            Ok(Err(message)) => {
                drop(process);
                let _ = reader.join();
                return Err(message.into());
            }
            Err(_) => {
                drop(process);
                let _ = reader.join();
                return Err("Sidecar startup failed or timed out".into());
            }
        };
        crate::startup_timing::mark("server-handshake");
        if info.port == 0
            || info.token.len() < 32
            || !info
                .token
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || b"_-".contains(&c))
        {
            return Err("Invalid sidecar credential".into());
        }
        let deadline = Instant::now() + Duration::from_secs(30);
        while Instant::now() < deadline {
            if process
                .child
                .try_wait()
                .map_err(|_| "Cannot poll sidecar")?
                .is_some()
            {
                return Err("Sidecar exited before ready".into());
            }
            if healthy(&info) {
                crate::startup_timing::mark("server-healthy");
                return Ok((process, info));
            }
            thread::sleep(Duration::from_millis(100));
        }
        Err("Sidecar health timeout".into())
    }
}
fn healthy(info: &Handshake) -> bool {
    let addr: SocketAddr = ([127, 0, 0, 1], info.port).into();
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_millis(300)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(1)));
    let request = format!("GET /health HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {}\r\nConnection: close\r\n\r\n", info.port, info.token);
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = String::new();
    stream.take(4096).read_to_string(&mut response).is_ok() && response.starts_with("HTTP/1.1 200 ")
}

// No response body is needed. The authenticated endpoint atomically reserves
// shutdown so no document writer can start between an idle check and closing.
pub fn request_close(info: &Handshake, action: &str) -> Result<bool, String> {
    let addr: SocketAddr = ([127, 0, 0, 1], info.port).into();
    let mut stream = TcpStream::connect_timeout(&addr, Duration::from_secs(2))
        .map_err(|_| "Cannot check active work")?;
    stream.set_read_timeout(Some(Duration::from_secs(3))).map_err(|_| "Cannot check active work")?;
    stream.set_write_timeout(Some(Duration::from_secs(3))).map_err(|_| "Cannot check active work")?;
    let endpoint = match action { "confirm" => "close-confirm", "abort" => "close-abort", _ => "close-check" };
    write!(stream, "POST /desktop/{endpoint} HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n", info.port, info.token)
        .map_err(|_| "Cannot check active work")?;
    let mut line = String::new();
    BufReader::new(stream).take(512).read_line(&mut line).map_err(|_| "Cannot check active work")?;
    if line.starts_with("HTTP/1.1 204 ") { Ok(true) }
    else if line.starts_with("HTTP/1.1 409 ") { Ok(false) }
    else { Err("Cannot check active work".into()) }
}


fn parse_handshake(line: &str) -> Result<Handshake, &'static str> {
    let value: serde_json::Value = serde_json::from_str(line).map_err(|_| "Invalid sidecar handshake")?;
    if value.get("error").and_then(|v| v.as_str()) == Some("storage_blocked") {
        return Err(match value.get("reason").and_then(|v| v.as_str()) {
            Some("import_required") => crate::locale::text("기존 앱 데이터가 있는 위치입니다. Preview에서 바로 사용하지 않습니다. 기존 앱으로 열어 주세요. 가져오기 기능은 아직 준비 중입니다."),
            Some("adoption_required") => crate::locale::text("기존 저장소의 호환성 확인이 필요합니다. 자동 변경하지 않았습니다. 현재 데이터를 사용하던 앱으로 열어 주세요."),
            Some("channel_mismatch") => crate::locale::text("다른 채널의 저장소입니다. 해당 데이터를 만든 앱으로 열거나 별도 저장 위치를 사용해 주세요."),
            Some("storage_busy") => crate::locale::text("다른 앱이나 명령이 이 저장소를 사용 중입니다. 해당 작업을 종료한 뒤 다시 실행해 주세요."),
            _ => crate::locale::text("저장소 정보를 안전하게 확인하지 못했습니다. 경로와 접근 권한을 확인해 주세요. 소유 표식을 삭제하거나 수정하지 마세요."),
        });
    }
    if std::env::var("PRTFM_STARTUP_TIMING").as_deref() == Ok("1") {
        for stage in ["stdlib", "uvicorn", "llm-client", "server-app", "server-jobs",
                      "start-gate", "credentials", "lifecycle-import", "storage-session", "handshake"] {
            if let Some(ms) = value.get("startup_ms").and_then(|v| v.get(stage)).and_then(|v| v.as_f64()) {
                if ms.is_finite() && ms >= 0.0 {
                    println!("PRTFM_PYTHON_STARTUP {} {}", stage, ms);
                }
            }
        }
    }
    serde_json::from_value(value).map_err(|_| "Invalid sidecar handshake")
}

#[cfg(test)]
mod storage_startup_tests {
    use super::parse_handshake;
    #[test]
    fn accepts_success_and_sanitizes_blocked_reason() {
        assert_eq!(parse_handshake(r#"{"port":1234,"token":"test"}"#).unwrap().port, 1234);
        let error = parse_handshake(r#"{"error":"storage_blocked","reason":"storage_busy"}"#).err().unwrap();
        assert!(error.contains("is using this store"));
        let unknown = parse_handshake(r#"{"error":"storage_blocked","reason":"private-secret"}"#).err().unwrap();
        assert!(!unknown.contains("private-secret"));
        assert!(parse_handshake("invalid").is_err());
    }
}
