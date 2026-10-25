//! 后端进程（sidecar）的生命周期：生成 token → 启动 `python -I -m ka_server serve` →
//! 读 stdout 的 `KA_READY port=N` → 把 `{base_url, token}` 交给前端 → 退出时 `POST /api/shutdown`，
//! 超时再强制结束。
//!
//! - token 每次启动随机生成，只经环境变量 `KA_TOKEN` 交给后端，不进命令行、不落盘。
//! - 后端只监听 127.0.0.1、端口由系统分配；`--parent-pid` 让壳意外退出时后端也自行退出。
//! - 60 秒内没有就绪、或运行中退出，都进入「崩溃」状态并发 `backend://exited` 事件（带最后几行日志）；
//!   就绪时发 `backend://ready`。重启会递增 generation，旧进程的读线程 / 监视线程据此失效。

use serde::Serialize;
use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter};

pub const READY_EVENT: &str = "backend://ready";
pub const EXITED_EVENT: &str = "backend://exited";
const READY_PREFIX: &str = "KA_READY port=";
const READY_TIMEOUT: Duration = Duration::from_secs(60);
const SHUTDOWN_GRACE: Duration = Duration::from_secs(10);
const LOG_TAIL_LINES: usize = 80;
/// 后端的「数据目录已被另一个实例占用」退出码（ka_server.EXIT_DATA_DIR_LOCKED）。
const EXIT_DATA_DIR_LOCKED: i32 = 3;

#[derive(Debug, Clone, Serialize)]
pub struct BackendInfo {
    pub base_url: String,
    pub token: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct BackendState {
    pub state: &'static str,
    pub base_url: Option<String>,
    pub error: Option<String>,
    pub exit_code: Option<i32>,
    pub log_tail: Vec<String>,
    pub log_dir: String,
}

#[derive(Debug, Clone)]
enum Phase {
    Stopped,
    Starting,
    Ready {
        port: u16,
    },
    Crashed {
        error: String,
        exit_code: Option<i32>,
    },
}

/// 启动参数（由 main 根据资源目录与壳偏好构造）。
#[derive(Debug, Clone)]
pub struct LaunchSpec {
    pub python: PathBuf,
    pub uv: Option<PathBuf>,
    pub data_dir: Option<PathBuf>,
    pub log_dir: PathBuf,
    /// 开发期额外放行的 CORS Origin（`tauri dev` 时界面来自 next dev）。
    pub dev_origin: Option<String>,
}

struct Inner {
    phase: Phase,
    token: String,
    generation: u64,
    child: Option<Child>,
    log_tail: VecDeque<String>,
    spec: LaunchSpec,
}

#[derive(Clone)]
pub struct Sidecar {
    inner: Arc<(Mutex<Inner>, Condvar)>,
    app: AppHandle,
}

fn random_token() -> String {
    let mut bytes = [0u8; 32];
    getrandom::getrandom(&mut bytes).expect("system RNG unavailable");
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// 资源目录里打包的解释器（bundle_python.py 的布局）。
pub fn bundled_python(resources: &Path) -> PathBuf {
    if cfg!(windows) {
        resources.join("python").join("python.exe")
    } else {
        resources.join("python").join("bin").join("python3")
    }
}

pub fn bundled_uv(resources: &Path) -> PathBuf {
    let python = bundled_python(resources);
    let dir = python.parent().map(Path::to_path_buf).unwrap_or_default();
    dir.join(if cfg!(windows) { "uv.exe" } else { "uv" })
}

fn friendly_exit(code: Option<i32>) -> String {
    match code {
        Some(EXIT_DATA_DIR_LOCKED) => {
            "The data folder is in use by another Knowledge Arch process: quit it completely (including the tray), then restart the service."
                .into()
        }
        Some(c) => format!("The service exited unexpectedly (exit code {c})"),
        None => "The service exited unexpectedly".into(),
    }
}

/// 发一个最小的 HTTP POST（只用于 /api/shutdown，避免为此引入 HTTP 客户端依赖）。
fn post_shutdown(port: u16, token: &str) -> bool {
    let addr = format!("127.0.0.1:{port}");
    let Ok(mut stream) = TcpStream::connect_timeout(
        &addr.parse().expect("valid loopback address"),
        Duration::from_secs(2),
    ) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
    let request = format!(
        "POST /api/shutdown HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer {token}\r\n\
         Content-Length: 0\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut response = String::new();
    let _ = stream.read_to_string(&mut response);
    response.starts_with("HTTP/1.1 2")
}

impl Sidecar {
    pub fn new(app: AppHandle, spec: LaunchSpec) -> Self {
        Self {
            inner: Arc::new((
                Mutex::new(Inner {
                    phase: Phase::Stopped,
                    token: String::new(),
                    generation: 0,
                    child: None,
                    log_tail: VecDeque::with_capacity(LOG_TAIL_LINES),
                    spec,
                }),
                Condvar::new(),
            )),
            app,
        }
    }

    fn push_log(inner: &mut Inner, line: String) {
        if inner.log_tail.len() == LOG_TAIL_LINES {
            inner.log_tail.pop_front();
        }
        inner.log_tail.push_back(line);
    }

    /// 启动一个新的后端进程（调用前应确保旧进程已停止）。
    pub fn start(&self) {
        let (lock, cvar) = &*self.inner;
        let mut inner = lock.lock().unwrap();
        inner.generation += 1;
        let generation = inner.generation;
        inner.token = random_token();
        inner.log_tail.clear();
        inner.phase = Phase::Starting;
        let spec = inner.spec.clone();
        crate::prefs::ensure_dir(&spec.log_dir);

        let mut cmd = Command::new(&spec.python);
        cmd.args(["-I", "-m", "ka_server", "serve", "--port", "0"])
            .arg("--parent-pid")
            .arg(std::process::id().to_string())
            .env("KA_TOKEN", &inner.token)
            .env("PYTHONIOENCODING", "utf-8")
            .env("PYTHONUTF8", "1")
            .env_remove("PYTHONHOME")
            .env_remove("PYTHONPATH")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if let Some(uv) = &spec.uv {
            cmd.arg("--uv").arg(uv);
        }
        if let Some(dir) = &spec.data_dir {
            cmd.arg("--data-dir").arg(dir);
        }
        if let Some(origin) = &spec.dev_origin {
            cmd.arg("--dev-origin").arg(origin);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = match cmd.spawn() {
            Ok(child) => child,
            Err(err) => {
                inner.phase = Phase::Crashed {
                    error: format!(
                        "Cannot start the backend ({}): {err}",
                        spec.python.display()
                    ),
                    exit_code: None,
                };
                cvar.notify_all();
                drop(inner);
                self.emit_exited();
                return;
            }
        };
        let stdout = child.stdout.take();
        let stderr = child.stderr.take();
        inner.child = Some(child);
        drop(inner);

        if let Some(stdout) = stdout {
            let me = self.clone();
            thread::spawn(move || me.read_stdout(generation, stdout));
        }
        if let Some(stderr) = stderr {
            let me = self.clone();
            thread::spawn(move || me.read_stderr(generation, stderr));
        }
        let me = self.clone();
        thread::spawn(move || me.monitor(generation));
    }

    fn read_stdout(&self, generation: u64, stdout: impl Read) {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            let (lock, cvar) = &*self.inner;
            let mut inner = lock.lock().unwrap();
            if inner.generation != generation {
                continue; // 旧进程：继续读完管道（避免它因管道写满而阻塞），但不再记账
            }
            if let Some(port) = line
                .strip_prefix(READY_PREFIX)
                .and_then(|p| p.trim().parse().ok())
            {
                if matches!(inner.phase, Phase::Starting) {
                    inner.phase = Phase::Ready { port };
                    cvar.notify_all();
                    let info = BackendInfo {
                        base_url: format!("http://127.0.0.1:{port}"),
                        token: inner.token.clone(),
                    };
                    drop(inner);
                    let _ = self.app.emit(READY_EVENT, info);
                }
            } else {
                Self::push_log(&mut inner, line);
            }
        }
    }

    fn read_stderr(&self, generation: u64, stderr: impl Read) {
        let mut reader = BufReader::new(stderr);
        let mut buf = Vec::new();
        while reader
            .read_until(b'\n', &mut buf)
            .map(|n| n > 0)
            .unwrap_or(false)
        {
            let line = String::from_utf8_lossy(&buf).trim_end().to_string();
            buf.clear();
            let mut inner = self.inner.0.lock().unwrap();
            if inner.generation != generation {
                continue;
            }
            if !line.is_empty() {
                Self::push_log(&mut inner, line);
            }
        }
    }

    /// 监视进程：就绪超时与意外退出都转为「崩溃」。
    fn monitor(&self, generation: u64) {
        let started = Instant::now();
        loop {
            thread::sleep(Duration::from_millis(300));
            let (lock, cvar) = &*self.inner;
            let mut inner = lock.lock().unwrap();
            if inner.generation != generation {
                return;
            }
            let exited = match inner.child.as_mut().map(|c| c.try_wait()) {
                Some(Ok(Some(status))) => Some(status.code()),
                Some(Ok(None)) => None,
                Some(Err(_)) | None => Some(None),
            };
            if let Some(code) = exited {
                inner.child = None;
                inner.phase = Phase::Crashed {
                    error: friendly_exit(code),
                    exit_code: code,
                };
                cvar.notify_all();
                drop(inner);
                self.emit_exited();
                return;
            }
            if matches!(inner.phase, Phase::Starting) && started.elapsed() > READY_TIMEOUT {
                if let Some(mut child) = inner.child.take() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
                inner.phase = Phase::Crashed {
                    error: format!("Service not ready within {} s", READY_TIMEOUT.as_secs()),
                    exit_code: None,
                };
                cvar.notify_all();
                drop(inner);
                self.emit_exited();
                return;
            }
        }
    }

    fn emit_exited(&self) {
        let _ = self.app.emit(EXITED_EVENT, self.state());
    }

    /// 等到就绪（或失败）后返回连接信息；失败返回面向用户的原因。
    pub fn wait_ready(&self, timeout: Duration) -> Result<BackendInfo, String> {
        let (lock, cvar) = &*self.inner;
        let deadline = Instant::now() + timeout;
        let mut inner = lock.lock().unwrap();
        loop {
            match &inner.phase {
                Phase::Ready { port } => {
                    return Ok(BackendInfo {
                        base_url: format!("http://127.0.0.1:{port}"),
                        token: inner.token.clone(),
                    })
                }
                Phase::Crashed { error, .. } => return Err(error.clone()),
                Phase::Stopped => return Err("The service is not running".into()),
                Phase::Starting => {}
            }
            let now = Instant::now();
            if now >= deadline {
                return Err("Timed out waiting for the service to be ready".into());
            }
            inner = cvar.wait_timeout(inner, deadline - now).unwrap().0;
        }
    }

    pub fn state(&self) -> BackendState {
        let inner = self.inner.0.lock().unwrap();
        let (state, base_url, error, exit_code) = match &inner.phase {
            Phase::Stopped => ("stopped", None, None, None),
            Phase::Starting => ("starting", None, None, None),
            Phase::Ready { port } => (
                "ready",
                Some(format!("http://127.0.0.1:{port}")),
                None,
                None,
            ),
            Phase::Crashed { error, exit_code } => {
                ("crashed", None, Some(error.clone()), *exit_code)
            }
        };
        BackendState {
            state,
            base_url,
            error,
            exit_code,
            log_tail: inner.log_tail.iter().cloned().collect(),
            log_dir: inner.spec.log_dir.display().to_string(),
        }
    }

    pub fn log_dir(&self) -> PathBuf {
        self.inner.0.lock().unwrap().spec.log_dir.clone()
    }

    /// 更新下次启动的参数（数据目录变更等），不影响正在运行的进程。
    pub fn set_spec(&self, spec: LaunchSpec) {
        self.inner.0.lock().unwrap().spec = spec;
    }

    /// 有序停止：先 `POST /api/shutdown` 让后端释放数据库与 Milvus 锁，超时再强制结束。
    pub fn stop(&self) {
        let (lock, cvar) = &*self.inner;
        let (port, token, child) = {
            let mut inner = lock.lock().unwrap();
            inner.generation += 1; // 让旧进程的读线程 / 监视线程失效，退出不算崩溃
            let port = match inner.phase {
                Phase::Ready { port } => Some(port),
                _ => None,
            };
            inner.phase = Phase::Stopped;
            cvar.notify_all();
            (port, inner.token.clone(), inner.child.take())
        };
        let Some(mut child) = child else { return };
        if let Some(port) = port {
            post_shutdown(port, &token);
        }
        let deadline = Instant::now() + SHUTDOWN_GRACE;
        while Instant::now() < deadline {
            if let Ok(Some(_)) = child.try_wait() {
                return;
            }
            thread::sleep(Duration::from_millis(100));
        }
        let _ = child.kill();
        let _ = child.wait();
    }

    pub fn restart(&self) -> Result<BackendInfo, String> {
        self.stop();
        self.start();
        self.wait_ready(READY_TIMEOUT + Duration::from_secs(5))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn token_is_64_hex_chars_and_random() {
        let a = random_token();
        assert_eq!(a.len(), 64);
        assert!(a.chars().all(|c| c.is_ascii_hexdigit()));
        assert_ne!(a, random_token());
    }

    #[test]
    fn bundled_layout_matches_bundle_script() {
        let res = Path::new("/r");
        if cfg!(windows) {
            assert_eq!(bundled_python(res), Path::new("/r/python/python.exe"));
            assert_eq!(bundled_uv(res), Path::new("/r/python/uv.exe"));
        } else {
            assert_eq!(bundled_python(res), Path::new("/r/python/bin/python3"));
            assert_eq!(bundled_uv(res), Path::new("/r/python/bin/uv"));
        }
    }

    #[test]
    fn locked_data_dir_has_a_specific_message() {
        assert!(friendly_exit(Some(EXIT_DATA_DIR_LOCKED)).contains("another"));
        assert!(friendly_exit(Some(1)).contains("1"));
    }
}
