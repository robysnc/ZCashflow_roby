# Android 17 local network permission

For `dev.zcashflow.app` targeting SDK 37 or later, Android 17 requires the
runtime permission `android.permission.ACCESS_LOCAL_NETWORK` for the direct
TCP and multicast traffic used by sync. The previous setup declared Internet,
network/Wi-Fi state and multicast permissions only. Rust opened its TCP listener
and mDNS daemon unconditionally. This matches the reported Android 16/17
regression, but confirmation on the affected device is still required.

Reference: https://developer.android.com/privacy-and-security/local-network-permission

## Implementation

- `pnpm android:setup` adds the manifest permission and installs the Kotlin
  permission controller alongside the generated `MainActivity`. It supports
  fresh Tauri templates and the earlier multicast-only patch, and can be rerun.
- Android 17 **and** target SDK >= 37: request permission at startup. Earlier
  Android versions/targets keep their existing network behavior without this
  runtime request. SDK targets are not changed by the setup script.
- Acquire the existing Wi-Fi multicast lock before notifying Rust of a grant;
  release it at activity destruction. Permission is rechecked on resume, including
  after returning from application settings. A denial explains that offline use
  remains available and offers a link to settings; it does not repeatedly prompt
  on resume or disable the user's sync preference.
- The small JNI callback retains the permission state even before Tauri/database
  initialization or vault unlock. Once both the core and permission are ready,
  it starts the existing TCP/mDNS services off the Android UI thread. Startup is
  serialized and idempotent, including concurrent callbacks and vault startup.
- Before a grant, sync does not start listeners/mDNS, scan the LAN, probe local
  addresses for status, reconnect, or perform pairing/direct connections.
  Database, scheduler, encryption and offline features remain available.
- The JNI symbol intentionally matches `dev.zcashflow.app.MainActivity`;
  the setup script rejects a different package instead of silently breaking it.
- Sync protocol, schema, encryption, TCP port 47811 with existing fallback,
  mDNS service name and existing LAN discovery logic are unchanged.

Generated Android files are not checked into this repository. Re-run setup
when preparing an Android project, including an already initialized one.

## Verification performed

- `node --test scripts/android-setup.test.mjs`: 4 tests pass (fresh, empty,
  bare and legacy-patched activities; permissions, hook preservation and
  idempotence). This command is now included in CI.
- TypeScript `tsc --noEmit`: passes.
- Vitest: 77 tests pass in 8 files.
- Production web build: passes; existing large-chunk advisory remains.
- Kotlin compiler 2.2.21 against the official Android SDK 37.0 `android.jar`:
  generated activity and controller compile, with a minimal Activity-based
  stand-in for TauriActivity. This checks Android API signatures, **not** full
  Tauri/Gradle integration or JNI loading on a device.
- Normal `cargo test --locked` was attempted but cannot build the Tauri Linux
  host here because GLib/GTK development libraries are missing.
- A separate temporary Cargo harness compiled the actual clock, database,
  domain, OCR, sync, vault and error modules with the original non-Tauri
  dependencies. Result: **102 passed, 2 ignored**. All 6 sync tests passed,
  including live two-way sync with receipts, encrypted/plain-device sync,
  pairing/reconnection, and the new denied -> granted/concurrent startup test.
  The harness excludes Tauri commands/host and the Android JNI bridge.
- `git diff --check`: passes.

## Remaining Android validation

Build in a configured Android/Tauri environment, without publishing a release:

```sh
pnpm install --frozen-lockfile
pnpm tauri android init --ci # only if not already initialized
pnpm android:setup
pnpm tauri android build --apk --target aarch64
```

Confirm **compile SDK and target SDK 37** in the generated Android project before
building the Android 17 test APK. The existing build workflow explicitly
installs platform 35; its SDK provisioning has not been upgraded by this fix.
Use a compatible SDK/AGP/NDK setup for your SDK 37 build. Confirm the merged APK
manifest includes `ACCESS_LOCAL_NETWORK`, `CHANGE_WIFI_MULTICAST_STATE`, and
package `dev.zcashflow.app`.

On the Galaxy Z Fold 8 / Android 17, test:

1. Fresh install or reset the permission: no LAN activity before consent;
   granting access starts discovery and sync without restarting the app.
2. Deny, cancel the explanation, and use offline features. No crash, scan or
   automatic repeated permission request should occur.
3. Open Android application settings and grant Nearby devices access, then return:
   discovery and sync start without restarting. Also test permanent denial.
4. Start with a password-protected vault; grant before and after vault unlock.
5. Reopen the app with permission already granted, rotate/fold the device, and
   background/foreground it: no duplicate listeners, multicast locks or sessions.
6. Pair with the Windows PC through mDNS and manually through its address on
   TCP 47811; verify transactions and receipts sync both ways, then reconnect.
7. Revoke access in settings and return/relaunch: the app remains usable offline
   and offers permission recovery. Android may restart the process on revocation.
8. Repeat normal pairing, mDNS discovery, manual TCP and sync on Android 16;
   no Android 17 permission dialog should appear.
9. Test the **release** APK as well: confirm the JNI method resolves without an
   `UnsatisfiedLinkError` after shrinking/linking, and retain relevant logcat
   output if discovery or connections still fail.

Windows firewall/Wi-Fi client isolation and device-specific behavior remain
separate possible causes if permission is granted but LAN traffic still fails.
