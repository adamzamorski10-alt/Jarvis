use std::{
    fs::OpenOptions,
    io::Write,
    net::TcpListener,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    thread,
    time::Duration,
};

use serde::Serialize;
use tauri::{Manager, State};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

#[derive(Default)]
struct CoreState {
    url: Option<String>,
    pid: Option<u32>,
    running: bool,
    last_error: Option<String>,
    retry_attempts: u32,
    log_dir: Option<String>,
}

type SharedCoreState = Arc<Mutex<CoreState>>;

#[derive(Clone, Serialize)]
struct CoreDiagnostics {
    url: Option<String>,
    pid: Option<u32>,
    running: bool,
    last_error: Option<String>,
    retry_attempts: u32,
    log_dir: Option<String>,
}

struct CoreProcess(Mutex<Option<CommandChild>>);

fn append_desktop_log(log_dir: Option<&Path>, filename: &str, message: &str) {
    if let Some(log_dir) = log_dir {
        let _ = std::fs::create_dir_all(log_dir);
        if let Ok(mut file) = OpenOptions::new()
            .create(true)
            .append(true)
            .open(log_dir.join(filename))
        {
            let _ = writeln!(file, "{message}");
        }
    }
}

fn tauri_log(state: Option<&SharedCoreState>, message: &str) {
    let _ = writeln!(std::io::stderr(), "{message}");
    if let Some(state) = state {
        let log_dir = state
            .lock()
            .ok()
            .and_then(|guard| guard.log_dir.clone())
            .map(PathBuf::from);
        append_desktop_log(log_dir.as_deref(), "desktop.log", message);
        append_desktop_log(log_dir.as_deref(), "startup.log", message);
    }
}

fn stop_core_process(process: &CoreProcess) {
    let mut pid_to_kill = None;
    if let Ok(mut guard) = process.0.lock() {
        if let Some(child) = guard.take() {
            pid_to_kill = Some(child.pid());
            let _ = child.kill();
        }
    }

    #[cfg(target_os = "windows")]
    if let Some(pid) = pid_to_kill {
        let _ = std::process::Command::new("taskkill")
            .args(["/PID", &pid.to_string(), "/T", "/F"])
            .output();
    }
}

#[tauri::command]
fn get_core_url(state: State<'_, SharedCoreState>) -> Result<String, String> {
    let state = state.lock().map_err(|_| "Core state lock poisoned".to_string())?;
    state
        .url
        .clone()
        .ok_or_else(|| state.last_error.clone().unwrap_or_else(|| "Core URL is not available".to_string()))
}

#[tauri::command]
fn get_core_diagnostics(state: State<'_, SharedCoreState>) -> Result<CoreDiagnostics, String> {
    let state = state.lock().map_err(|_| "Core state lock poisoned".to_string())?;
    Ok(CoreDiagnostics {
        url: state.url.clone(),
        pid: state.pid,
        running: state.running,
        last_error: state.last_error.clone(),
        retry_attempts: state.retry_attempts,
        log_dir: state.log_dir.clone(),
    })
}

fn reserve_local_port() -> Result<u16, String> {
    let listener = TcpListener::bind("127.0.0.1:0")
        .map_err(|error| format!("Could not reserve local port: {error}"))?;
    let port = listener
        .local_addr()
        .map_err(|error| format!("Could not read reserved local port: {error}"))?
        .port();
    drop(listener);
    Ok(port)
}

fn wait_for_health(url: &str, state: &SharedCoreState) -> Result<(), String> {
    let health_url = format!("{url}/api/health");

    for attempt in 1..=30 {
        tauri_log(Some(state), &format!("[Tauri] Core health check attempt {attempt} url={health_url}"));
        if let Ok(mut guard) = state.lock() {
            guard.retry_attempts = attempt;
        }

        match reqwest::blocking::get(&health_url) {
            Ok(response) if response.status().is_success() => {
                tauri_log(Some(state), &format!("[Tauri] Core health check successful status={}", response.status()));
                if let Ok(mut guard) = state.lock() {
                    guard.running = true;
                    guard.last_error = None;
                }
                return Ok(());
            }
            Ok(response) => {
                tauri_log(Some(state), &format!("[Tauri] Core health check failed status={}", response.status()));
                if let Ok(mut guard) = state.lock() {
                    guard.last_error = Some(format!("Health check returned HTTP {}", response.status()));
                }
            }
            Err(error) => {
                tauri_log(Some(state), &format!("[Tauri] Core health check error={error}"));
                if let Ok(mut guard) = state.lock() {
                    guard.last_error = Some(error.to_string());
                }
            }
        }

        thread::sleep(Duration::from_millis(500));
    }

    Err(format!("Core did not become healthy at {health_url}"))
}

fn start_python_core(app: &tauri::App, state: SharedCoreState) -> Result<CommandChild, String> {
    tauri_log(Some(&state), "[Tauri] Starting Python Core");
    let port = reserve_local_port()?;
    let url = format!("http://127.0.0.1:{port}");

    let data_dir = app
        .path()
        .app_local_data_dir()
        .map_err(|error| format!("Could not resolve app data directory: {error}"))?;
    std::fs::create_dir_all(&data_dir)
        .map_err(|error| format!("Could not create app data directory: {error}"))?;
    let db_path = data_dir.join("ai_operator.db");
    let log_dir = data_dir.join("logs");
    std::fs::create_dir_all(&log_dir)
        .map_err(|error| format!("Could not create log directory: {error}"))?;
    if let Ok(mut guard) = state.lock() {
        guard.log_dir = Some(log_dir.to_string_lossy().to_string());
    }

    let parent_pid = std::process::id().to_string();
    let command_line = format!(
        "ai-operator-core --host 127.0.0.1 --port {port} --db-path {} --parent-pid {parent_pid} --log-dir {}",
        db_path.to_string_lossy(),
        log_dir.to_string_lossy(),
    );
    tauri_log(Some(&state), &format!("[Tauri] Sidecar command: {command_line}"));
    let (mut rx, child) = app
        .shell()
        .sidecar("ai-operator-core")
        .map_err(|error| format!("Could not create Core sidecar command: {error}"))?
        .args(["--host", "127.0.0.1", "--port", &port.to_string(), "--db-path"])
        .arg(db_path.to_string_lossy().to_string())
        .args(["--parent-pid", &parent_pid])
        .args(["--log-dir"])
        .arg(log_dir.to_string_lossy().to_string())
        .spawn()
        .map_err(|error| format!("Could not spawn Core sidecar: {error}"))?;

    tauri_log(Some(&state), &format!("[Tauri] Core spawned with PID {}", child.pid()));
    tauri_log(Some(&state), &format!("[Tauri] Core URL discovered: {url}"));
    if let Ok(mut guard) = state.lock() {
        guard.url = Some(url.clone());
        guard.pid = Some(child.pid());
        guard.running = false;
        guard.last_error = None;
        guard.retry_attempts = 0;
    }

    let log_state = state.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    tauri_log(Some(&log_state), &format!("[Core stdout] {}", String::from_utf8_lossy(&bytes).trim()));
                }
                CommandEvent::Stderr(bytes) => {
                    tauri_log(Some(&log_state), &format!("[Core stderr] {}", String::from_utf8_lossy(&bytes).trim()));
                }
                CommandEvent::Terminated(payload) => {
                    tauri_log(Some(&log_state), &format!("[Tauri] Core process terminated: {payload:?}"));
                }
                other => {
                    tauri_log(Some(&log_state), &format!("[Tauri] Core sidecar event: {other:?}"));
                }
            }
        }
        if let Ok(mut guard) = log_state.lock() {
            guard.running = false;
        }
    });

    wait_for_health(&url, &state)?;
    Ok(child)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let core_state: SharedCoreState = Arc::new(Mutex::new(CoreState::default()));
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .manage(core_state.clone())
        .manage(CoreProcess(Mutex::new(None)))
        .setup(move |app| {
            match start_python_core(app, core_state.clone()) {
                Ok(child) => {
                    let process = app.state::<CoreProcess>();
                    if let Ok(mut guard) = process.0.lock() {
                        *guard = Some(child);
                    };
                }
                Err(error) => {
                    tauri_log(Some(&core_state), &format!("[Tauri] Core startup failed: {error}"));
                    if let Ok(mut guard) = core_state.lock() {
                        guard.last_error = Some(error);
                        guard.running = false;
                    }
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![get_core_url, get_core_diagnostics])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let process = window.state::<CoreProcess>();
                stop_core_process(&process);
                window.app_handle().exit(0);
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
