// Treble Tracker desktop shell (spec §4: "Tauri-based native app").
//
// Deliberately thin. The shell owns the window and the TAPI sidecar
// lifecycle; it renders nothing itself. All screen rendering happens in
// the shared TypeScript renderer against buffers resolved by TAPI, which
// is what keeps the desktop and TUI in agreement (invariant I6) rather
// than hoping two implementations stay in step.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::{Ipv4Addr, SocketAddr, TcpStream};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;

/// Loopback only, matching `treble/tapi/server.py`. Local-only mode has no
/// authentication, so there must be no network surface to reach.
const TAPI_PORT: u16 = 8756;

struct Sidecar(Mutex<Option<Child>>);

fn tapi_is_running() -> bool {
    let address = SocketAddr::from((Ipv4Addr::LOCALHOST, TAPI_PORT));
    TcpStream::connect_timeout(&address, Duration::from_millis(250)).is_ok()
}

/// Start `treble serve` unless something is already serving.
///
/// The check matters: a user who is already running the server from a
/// terminal, or who opens the app twice, must not get a second process
/// fighting for the port and the store.
fn start_sidecar() -> Option<Child> {
    if tapi_is_running() {
        return None;
    }
    let repo = env!("TREBLE_REPO");
    let executable = format!("{repo}/.venv/bin/treble");
    match Command::new(&executable).arg("serve").current_dir(repo).spawn() {
        Ok(child) => Some(child),
        Err(error) => {
            // The window still opens and explains itself: the frontend
            // reports an unreachable service rather than showing nothing.
            eprintln!("could not start {executable}: {error}");
            None
        }
    }
}

fn main() {
    tauri::Builder::default()
        .manage(Sidecar(Mutex::new(start_sidecar())))
        .build(tauri::generate_context!())
        .expect("error while building Treble Tracker")
        .run(|handle, event| {
            if let tauri::RunEvent::ExitRequested { .. } = event {
                // Only ever kills a child this process started, so a
                // server the user runs themselves outlives the window.
                use tauri::Manager;
                if let Some(child) = handle.state::<Sidecar>().0.lock().unwrap().take() {
                    let mut child = child;
                    let _ = child.kill();
                }
            }
        });
}
