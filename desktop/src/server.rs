//! Finding, starting, watching and stopping the PictureHouse (Python) server.
//!
//! The app either *attaches* to a server that is already running (started
//! from a terminal or `run-server.bat`) or *owns* one it started itself.
//! Only an owned server is stopped on Quit.
//!
//! Owned servers run inside a Windows Job Object with kill-on-close: the
//! venv's python.exe is only a launcher that starts the real interpreter as
//! a child, and the server starts FFmpeg processes of its own. Closing the
//! job ends the whole tree, and Windows closes it for us if this app dies.
//! On Linux the server watches for this app going away and stops itself
//! (`procs.exit_with_parent`), taking its FFmpeg processes with it.

use std::fs::{self, File};
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const DEFAULT_PORT: u16 = 9595;
pub const START_TIMEOUT: Duration = Duration::from_secs(90);

/// The repo (or install) folder holding `pyproject.toml` and `src/picturehouse`.
pub fn find_home() -> Option<PathBuf> {
    if let Ok(p) = std::env::var("PICTUREHOUSE_HOME") {
        let p = PathBuf::from(p);
        if p.join("src").join("picturehouse").is_dir() {
            return Some(p);
        }
    }
    let exe = std::env::current_exe().ok()?;
    exe.ancestors()
        .find(|d| d.join("pyproject.toml").is_file() && d.join("src").join("picturehouse").is_dir())
        .map(Path::to_path_buf)
}

/// How the server is run.
#[derive(Clone, Debug)]
pub enum Launch {
    /// Installed: the frozen server shipped next to the app (`server/` resource).
    Bundled(PathBuf),
    /// Development: the project folder, using its `.venv` (or `uv run`).
    Project(PathBuf),
}

impl Launch {
    /// Bundled server first (installed app), then the project folder.
    pub fn find(resource_dir: Option<&Path>) -> Option<Launch> {
        let exe = format!("picturehouse-server{}", std::env::consts::EXE_SUFFIX);
        let bundled = resource_dir.map(|d| d.join("server").join(exe));
        if let Some(exe) = bundled.filter(|p| p.is_file()) {
            return Some(Launch::Bundled(exe));
        }
        find_home().map(Launch::Project)
    }

    pub fn describe(&self) -> String {
        match self {
            Launch::Bundled(exe) => format!("bundled server {}", exe.display()),
            Launch::Project(home) => format!("server in the project folder {}", home.display()),
        }
    }

    /// A command that runs the `picturehouse` CLI; append its arguments.
    fn command(&self) -> Command {
        let mut cmd = match self {
            Launch::Bundled(exe) => {
                let mut c = Command::new(exe);
                if let Some(dir) = exe.parent() {
                    c.current_dir(dir);
                }
                c
            }
            Launch::Project(home) => {
                let venv = if cfg!(windows) {
                    home.join(".venv").join("Scripts").join("python.exe")
                } else {
                    home.join(".venv").join("bin").join("python")
                };
                let mut c = if venv.is_file() {
                    Command::new(venv)
                } else {
                    let mut c = Command::new("uv");
                    c.args(["run", "--no-sync", "python"]);
                    c
                };
                c.args(["-m", "picturehouse"]).current_dir(home);
                c
            }
        };
        #[cfg(windows)]
        cmd.creation_flags(CREATE_NO_WINDOW);
        cmd
    }
}

/// The server's data folder logs (same default as the Python side).
pub fn log_dir() -> PathBuf {
    let base = std::env::var("PICTUREHOUSE_DATA_DIR").map(PathBuf::from).unwrap_or_else(|_| {
        if cfg!(windows) {
            let local = std::env::var("LOCALAPPDATA").unwrap_or_else(|_| ".".into());
            PathBuf::from(local).join("PictureHouse")
        } else {
            let data = std::env::var("XDG_DATA_HOME").map(PathBuf::from).unwrap_or_else(|_| {
                PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| ".".into()))
                    .join(".local")
                    .join("share")
            });
            data.join("picturehouse")
        }
    });
    base.join("logs")
}

/// Read `[server] http_port` via `picturehouse config show` (the config is
/// the source of truth), falling back to the default port.
pub fn configured_port(launch: &Launch) -> u16 {
    let out = launch
        .command()
        .args(["config", "show"])
        .stdin(Stdio::null())
        .stderr(Stdio::null())
        .output();
    out.ok()
        .filter(|o| o.status.success())
        .and_then(|o| serde_json::from_slice::<serde_json::Value>(&o.stdout).ok())
        .and_then(|v| v["server"]["http_port"].as_u64())
        .and_then(|p| u16::try_from(p).ok())
        .unwrap_or(DEFAULT_PORT)
}

fn http() -> reqwest::blocking::Client {
    reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(3))
        .no_proxy()
        .build()
        .expect("http client")
}

/// What a running PictureHouse server says about itself.
pub struct Running {
    pub name: String,
    /// "desktop" when this app started it (possibly in an earlier run).
    pub launched_by: String,
    pub pid: Option<u32>,
}

/// `Some(..)` if a PictureHouse server answers on this port.
pub fn probe_info(port: u16) -> Option<Running> {
    let v: serde_json::Value = http()
        .get(format!("http://127.0.0.1:{port}/api/status"))
        .send()
        .ok()?
        .json()
        .ok()?;
    let server = &v["server"];
    Some(Running {
        name: server["name"].as_str()?.to_owned(),
        launched_by: server["launched_by"].as_str().unwrap_or("terminal").to_owned(),
        pid: server["pid"].as_u64().and_then(|p| u32::try_from(p).ok()),
    })
}

pub fn probe(port: u16) -> Option<String> {
    probe_info(port).map(|r| r.name)
}

/// Wait until nothing answers or listens on the port.
fn wait_port_free(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if !port_in_use(port) {
            return true;
        }
        std::thread::sleep(Duration::from_millis(150));
    }
    !port_in_use(port)
}

/// Force-stop a leftover server by pid, but only if that pid really is a
/// PictureHouse server process (pids get reused).
#[cfg(windows)]
fn terminate_server_pid(pid: u32) -> bool {
    use std::ffi::OsString;
    use std::os::windows::ffi::OsStringExt;
    type Handle = *mut core::ffi::c_void;
    #[link(name = "kernel32")]
    extern "system" {
        fn OpenProcess(access: u32, inherit: i32, pid: u32) -> Handle;
        fn QueryFullProcessImageNameW(h: Handle, flags: u32, buf: *mut u16, size: *mut u32) -> i32;
        fn TerminateProcess(h: Handle, code: u32) -> i32;
        fn CloseHandle(h: Handle) -> i32;
    }
    const PROCESS_TERMINATE: u32 = 0x0001;
    const PROCESS_QUERY_LIMITED_INFORMATION: u32 = 0x1000;
    unsafe {
        let h = OpenProcess(PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if h.is_null() {
            return false;
        }
        let mut buf = [0u16; 1024];
        let mut len = buf.len() as u32;
        let ok = QueryFullProcessImageNameW(h, 0, buf.as_mut_ptr(), &mut len) != 0;
        let image = OsString::from_wide(&buf[..len as usize]).to_string_lossy().to_lowercase();
        let ours = ok && (image.ends_with("picturehouse-server.exe") || image.ends_with("python.exe"));
        let killed = ours && TerminateProcess(h, 1) != 0;
        CloseHandle(h);
        killed
    }
}

#[cfg(target_os = "linux")]
fn terminate_server_pid(pid: u32) -> bool {
    let cmdline = fs::read(format!("/proc/{pid}/cmdline")).unwrap_or_default();
    let cmdline = String::from_utf8_lossy(&cmdline).to_lowercase();
    let ours = cmdline.contains("picturehouse-server") || cmdline.contains("picturehouse");
    // SAFETY: plain kill(2) on a pid we just checked.
    ours && unsafe { libc::kill(pid as libc::pid_t, libc::SIGTERM) } == 0
}

#[cfg(not(any(windows, target_os = "linux")))]
fn terminate_server_pid(_pid: u32) -> bool {
    false
}

pub fn post(port: u16, path: &str) -> Result<(), String> {
    let r = http()
        .post(format!("http://127.0.0.1:{port}{path}"))
        .header("content-type", "application/json")
        .body("{}")
        .send()
        .map_err(|e| e.to_string())?;
    if r.status().is_success() {
        Ok(())
    } else {
        Err(format!("HTTP {}", r.status()))
    }
}

/// Is something (anything) listening on the port?
fn port_in_use(port: u16) -> bool {
    std::net::TcpStream::connect_timeout(
        &([127, 0, 0, 1], port).into(),
        Duration::from_millis(300),
    )
    .is_ok()
}

#[cfg(windows)]
#[allow(dead_code)] // held only so that dropping it closes the job
struct Job(win32job::Job);
// The job handle is only used from behind the Mutex below.
#[cfg(windows)]
unsafe impl Send for Job {}

struct Owned {
    child: Child,
    #[cfg(windows)]
    _job: Option<Job>, // dropping it kills the whole process tree
    console_log: PathBuf,
}

#[derive(Default)]
struct Inner {
    owned: Option<Owned>,
    port: u16,
    stopping: bool,
}

pub enum Started {
    Attached(String),
    Spawned,
}

pub struct Server {
    launch: Option<Launch>,
    inner: Mutex<Inner>,
}

impl Server {
    pub fn new(resource_dir: Option<&Path>) -> Self {
        let launch = Launch::find(resource_dir);
        let port = launch.as_ref().map(configured_port).unwrap_or(DEFAULT_PORT);
        Server { launch, inner: Mutex::new(Inner { port, ..Default::default() }) }
    }

    pub fn port(&self) -> u16 {
        self.inner.lock().unwrap().port
    }

    pub fn url(&self) -> String {
        format!("http://127.0.0.1:{}/", self.port())
    }

    pub fn owns_server(&self) -> bool {
        self.inner.lock().unwrap().owned.is_some()
    }

    /// Attach to a running server, or start one and wait until it answers.
    /// `progress` gets short human-readable status lines.
    pub fn start(&self, progress: &dyn Fn(&str)) -> Result<Started, String> {
        let port = self.port();
        if let Some(running) = probe_info(port) {
            if running.launched_by != "desktop" {
                // Started from a terminal or run-server.bat: the user's, leave it.
                return Ok(Started::Attached(running.name));
            }
            // Left over from an earlier run of this app (e.g. it was killed):
            // replace it so a restart always gets a fresh, current server.
            progress("Replacing a server left over from the last run…");
            let _ = post(port, "/api/admin/shutdown");
            if !wait_port_free(port, Duration::from_secs(10)) {
                if let Some(pid) = running.pid {
                    terminate_server_pid(pid);
                }
                wait_port_free(port, Duration::from_secs(5));
            }
        }
        if port_in_use(port) {
            return Err(format!(
                "Port {port} is used by another program, so the server can't start.\n\
                 Change [server] http_port in config.toml, or close that program."
            ));
        }
        let launch = self.launch.as_ref().ok_or_else(|| {
            "Couldn't find the PictureHouse server: neither the bundled server nor a \
             project folder (with pyproject.toml).\nReinstall PictureHouse, or set \
             PICTUREHOUSE_HOME to the project folder."
                .to_string()
        })?;

        progress("Starting the media server…");
        let logs = log_dir();
        let _ = fs::create_dir_all(&logs);
        let console_log = logs.join("desktop-server-console.log");
        let out = File::create(&console_log).map_err(|e| format!("can't write {console_log:?}: {e}"))?;
        let err = out.try_clone().map_err(|e| e.to_string())?;
        let child = launch
            .command()
            .arg("serve")
            .env("PICTUREHOUSE_LAUNCHED_BY", "desktop")
            .env("PICTUREHOUSE_PARENT_PID", std::process::id().to_string())
            .stdin(Stdio::null())
            .stdout(out)
            .stderr(err)
            .spawn()
            .map_err(|e| format!("Couldn't start the {}: {e}", launch.describe()))?;

        #[cfg(windows)]
        let job = {
            let mut info = win32job::ExtendedLimitInfo::new();
            info.limit_kill_on_job_close();
            match win32job::Job::create_with_limit_info(&mut info) {
                Ok(job) => {
                    use std::os::windows::io::AsRawHandle;
                    let _ = job.assign_process(child.as_raw_handle() as isize);
                    Some(Job(job))
                }
                Err(_) => None,
            }
        };

        {
            let mut inner = self.inner.lock().unwrap();
            inner.stopping = false;
            inner.owned = Some(Owned {
                child,
                #[cfg(windows)]
                _job: job,
                console_log: console_log.clone(),
            });
        }

        let started = Instant::now();
        loop {
            std::thread::sleep(Duration::from_millis(400));
            if probe(port).is_some() {
                return Ok(Started::Spawned);
            }
            if let Some(code) = self.exited() {
                let tail = tail(&console_log, 30);
                self.inner.lock().unwrap().owned = None;
                return Err(format!("The server stopped while starting (exit code {code}).\n\n{tail}"));
            }
            if started.elapsed() > START_TIMEOUT {
                let tail = tail(&console_log, 30);
                self.kill();
                return Err(format!(
                    "The server didn't answer within {} seconds.\n\n{tail}",
                    START_TIMEOUT.as_secs()
                ));
            }
            if started.elapsed() > Duration::from_secs(4) {
                progress("Starting the media server… (first start can take a little longer)");
            }
        }
    }

    /// `Some(exit code)` if an owned server process has exited.
    pub fn exited(&self) -> Option<i32> {
        let mut inner = self.inner.lock().unwrap();
        let owned = inner.owned.as_mut()?;
        match owned.child.try_wait() {
            Ok(Some(status)) => Some(status.code().unwrap_or(-1)),
            _ => None,
        }
    }

    /// True when an owned server died without us asking it to stop.
    pub fn crashed(&self) -> bool {
        let stopping = self.inner.lock().unwrap().stopping;
        !stopping && self.exited().is_some()
    }

    pub fn console_log(&self) -> Option<PathBuf> {
        self.inner.lock().unwrap().owned.as_ref().map(|o| o.console_log.clone())
    }

    /// Stop an owned server cleanly (TVs get an SSDP goodbye, FFmpeg stops,
    /// the database closes); force it after a few seconds.
    pub fn stop(&self) {
        let port = {
            let mut inner = self.inner.lock().unwrap();
            if inner.owned.is_none() {
                return;
            }
            inner.stopping = true;
            inner.port
        };
        // Clean stop first: SSDP goodbye to TVs, FFmpeg stopped, database closed.
        let _ = post(port, "/api/admin/shutdown");
        let deadline = Instant::now() + Duration::from_secs(10);
        while Instant::now() < deadline {
            if self.exited().is_some() && !port_in_use(port) {
                break;
            }
            std::thread::sleep(Duration::from_millis(150));
        }
        // Then make sure: kill whatever is left (dropping the job ends the whole
        // tree, including the real interpreter behind the venv launcher).
        self.kill();
        wait_port_free(port, Duration::from_secs(5));
    }

    fn kill(&self) {
        let mut inner = self.inner.lock().unwrap();
        inner.stopping = true;
        if let Some(mut owned) = inner.owned.take() {
            let _ = owned.child.kill();
            let _ = owned.child.wait();
            // `owned` (and its job) drops here: anything left in the tree is killed.
        }
    }
}

/// Last `n` lines of a text file, for error messages.
pub fn tail(path: &Path, n: usize) -> String {
    let Ok(mut f) = File::open(path) else { return String::new() };
    let len = f.metadata().map(|m| m.len()).unwrap_or(0);
    let _ = f.seek(SeekFrom::Start(len.saturating_sub(16 * 1024)));
    let mut buf = String::new();
    let _ = f.read_to_string(&mut buf);
    let lines: Vec<&str> = buf.lines().collect();
    lines[lines.len().saturating_sub(n)..].join("\n")
}
