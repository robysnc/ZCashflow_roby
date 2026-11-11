//! Android's permission callback can arrive before or after database/vault startup.
//! Keep the grant independently of the core and converge through idempotent start().
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::OnceLock;

use tauri::{Emitter, Manager};

static ALLOWED: AtomicBool = AtomicBool::new(false);
static APP: OnceLock<tauri::AppHandle> = OnceLock::new();

pub fn allowed() -> bool {
    ALLOWED.load(Ordering::SeqCst)
}

pub fn init(app: &tauri::AppHandle) {
    let _ = APP.set(app.clone());
}

// JNI jboolean is an unsigned byte; the two opaque pointers are unused JNIEnv/jobject.
// Keep the symbol in sync with the package checked by android-setup.mjs.
#[no_mangle]
pub extern "system" fn Java_dev_zcashflow_app_MainActivity_setLocalNetworkPermission(
    _env: *mut std::ffi::c_void,
    _activity: *mut std::ffi::c_void,
    granted: u8,
) {
    ALLOWED.store(granted != 0, Ordering::SeqCst);
    if let Some(app) = APP.get() {
        let app = app.clone();
        // Do not block Android's UI thread with TCP/mDNS initialization.
        std::thread::spawn(move || {
            if let Some(core) = app.try_state::<std::sync::Arc<crate::sync::SyncCore>>() {
                if allowed() {
                    if let Err(e) = core.start(false) {
                        eprintln!("sync: could not start after local network permission: {e}");
                    }
                } else {
                    core.disconnect_all();
                }
            }
            let _ = app.emit("sync-changed", ());
        });
    }
}
