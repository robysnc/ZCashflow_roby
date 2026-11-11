import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const script = fileURLToPath(new URL("./android-setup.mjs", import.meta.url));
const modern = `package dev.zcashflow.app
import android.os.Bundle
import androidx.activity.enableEdgeToEdge
class MainActivity : TauriActivity() {
  override fun onCreate(savedInstanceState: Bundle?) {
    enableEdgeToEdge()
    super.onCreate(savedInstanceState)
  }
}
`;

for (const [name, initial, upgrade] of [
  ["current Tauri template", modern, false],
  ["empty activity body", "package dev.zcashflow.app\nclass MainActivity : TauriActivity() {}\n", false],
  ["bare activity", "package dev.zcashflow.app\nclass MainActivity : TauriActivity()\n", false],
  ["activity patched with the previous multicast script", modern, true],
]) {
  test(`Android setup migrates ${name} and is idempotent`, () => {
    const dir = mkdtempSync(join(tmpdir(), "zcashflow-android-"));
    try {
      const main = join(dir, "src-tauri/gen/android/app/src/main");
      const java = join(main, "java/dev/zcashflow/app");
      mkdirSync(java, { recursive: true });
      writeFileSync(join(main, "AndroidManifest.xml"), '<manifest xmlns:android="http://schemas.android.com/apk/res/android"><application /></manifest>');
      const activity = join(java, "MainActivity.kt");
      writeFileSync(activity, initial);
      if (upgrade) {
        writeFileSync(activity, initial
          .replace("import android.os.Bundle", "import android.content.Context\nimport android.net.wifi.WifiManager\nimport android.os.Bundle")
          .replace("class MainActivity : TauriActivity() {", "class MainActivity : TauriActivity() {\n  private var multicastLock: WifiManager.MulticastLock? = null")
          .replace("    super.onCreate(savedInstanceState)\n", `    super.onCreate(savedInstanceState)
    val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
    multicastLock = wifi.createMulticastLock("zcashflow-sync").apply {
      setReferenceCounted(true)
      acquire()
    }
`)
          .replace(/\}\s*$/, `  override fun onDestroy() {
    multicastLock?.let { if (it.isHeld) it.release() }
    super.onDestroy()
  }
}\n`));
      }
      execFileSync(process.execPath, [script], { cwd: dir });
      const first = readFileSync(activity, "utf8");
      const manifest = readFileSync(join(main, "AndroidManifest.xml"), "utf8");
      const helper = readFileSync(join(java, "LocalNetworkPermission.kt"), "utf8");
      execFileSync(process.execPath, [script], { cwd: dir });
      assert.equal(readFileSync(activity, "utf8"), first);
      assert.equal(readFileSync(join(main, "AndroidManifest.xml"), "utf8"), manifest);
      assert.equal(readFileSync(join(java, "LocalNetworkPermission.kt"), "utf8"), helper);
      for (const perm of ["INTERNET", "ACCESS_LOCAL_NETWORK", "ACCESS_NETWORK_STATE", "ACCESS_WIFI_STATE", "CHANGE_WIFI_MULTICAST_STATE"]) {
        assert.equal(manifest.split(`"android.permission.${perm}"`).length - 1, 1);
      }
      for (const method of ["onCreate", "onResume", "onDestroy", "onRequestPermissionsResult"]) {
        assert.equal(first.split(`override fun ${method}(`).length - 1, 1);
        assert.equal(first.split(`localNetwork.${method}(`).length - 1, 1);
      }
      if (initial === modern) assert.match(first, /enableEdgeToEdge\(\)/);
      assert.doesNotMatch(first, /multicastLock/);
      assert.equal((first.match(/\{/g) || []).length, (first.match(/\}/g) || []).length);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
}
