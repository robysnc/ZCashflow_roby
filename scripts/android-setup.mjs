#!/usr/bin/env node

import { existsSync, readFileSync, writeFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const root = "src-tauri/gen/android";
const manifest = join(root, "app/src/main/AndroidManifest.xml");

function patchManifest() {
  let xml = readFileSync(manifest, "utf8");
  const perms = [
    "android.permission.INTERNET",
    "android.permission.ACCESS_LOCAL_NETWORK",
    "android.permission.ACCESS_NETWORK_STATE",
    "android.permission.ACCESS_WIFI_STATE",
    "android.permission.CHANGE_WIFI_MULTICAST_STATE",
  ];
  for (const p of perms) {
    if (!xml.includes(`"${p}"`)) {
      xml = xml.replace(/<application/, `<uses-permission android:name="${p}" />\n    <application`);
    }
  }
  writeFileSync(manifest, xml);
  console.log("patched", manifest);
}

function findMainActivity(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      const found = findMainActivity(p);
      if (found) return found;
    } else if (name === "MainActivity.kt") return p;
  }
  return null;
}

function patchActivity() {
  const file = findMainActivity(join(root, "app/src/main/java"));
  if (!file) throw new Error("MainActivity.kt not found – run `pnpm tauri android init` first");
  let out = readFileSync(file, "utf8");
  if (!/^package dev\.zcashflow\.app\s*$/m.test(out)) {
    throw new Error("MainActivity package must match the Rust JNI bridge: dev.zcashflow.app");
  }
  if (!out.includes("// zcashflow-local-network")) {
    // Upgrade activities patched by earlier versions of this script.
    out = out.replace(/  private var multicastLock: WifiManager\.MulticastLock\? = null\n/, "");
    out = out.replace(/    val wifi = applicationContext\.getSystemService\(Context\.WIFI_SERVICE\) as WifiManager\n    multicastLock = wifi\.createMulticastLock\("zcashflow-sync"\)\.apply \{\n      setReferenceCounted\(true\)\n      acquire\(\)\n    \}\n/, "");
    out = out.replace(/    multicastLock\?\.let \{ if \(it\.isHeld\) it\.release\(\) \}\n/, "");
    if (out.includes("multicastLock")) throw new Error("Unrecognised multicast patch; migrate MainActivity manually");
    out = out.replace(/import android\.(content\.Context|net\.wifi\.WifiManager)\n/g, "");
    if (!out.includes("import android.os.Bundle")) {
      out = out.replace(/^(package[^\n]*\n)/m, "$1\nimport android.os.Bundle\n");
    }
    out = out.replace(/class MainActivity\s*:\s*TauriActivity\(\)\s*(?:\{)?/, `class MainActivity : TauriActivity() {
  // zcashflow-local-network
  private val localNetwork = LocalNetworkPermission(this)
  external fun setLocalNetworkPermission(granted: Boolean)
`);
    // Bare Tauri templates may omit the class body.
    if (!out.trimEnd().endsWith("}")) out += "\n}\n";
    const hook = (name, declaration, superCall, call) => {
      if (new RegExp(`override fun ${name}\\s*\\(`).test(out)) {
        if (!out.includes(superCall)) throw new Error(`Cannot safely patch ${name}`);
        out = out.replace(superCall, `${superCall}\n    ${call}`);
      } else {
        out = out.replace(/\}\s*$/, `  ${declaration} {\n    ${superCall}\n    ${call}\n  }\n}\n`);
      }
    };
    hook("onCreate", "override fun onCreate(savedInstanceState: Bundle?)", "super.onCreate(savedInstanceState)", "localNetwork.onCreate()");
    hook("onResume", "override fun onResume()", "super.onResume()", "localNetwork.onResume()");
    hook("onDestroy", "override fun onDestroy()", "super.onDestroy()", "localNetwork.onDestroy()");
    hook("onRequestPermissionsResult", "override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray)", "super.onRequestPermissionsResult(requestCode, permissions, grantResults)", "localNetwork.onRequestPermissionsResult(requestCode)");
  }
  const helper = readFileSync(fileURLToPath(new URL("./android/LocalNetworkPermission.kt", import.meta.url)), "utf8");
  writeFileSync(join(file, "..", "LocalNetworkPermission.kt"), helper);
  writeFileSync(file, out);
  console.log("patched", file);
}

function patchSigning() {
  const gradle = join(root, "app/build.gradle.kts");
  if (!existsSync(join(root, "keystore.properties"))) return console.log("no keystore.properties, release stays unsigned");
  let src = readFileSync(gradle, "utf8");
  if (src.includes('signingConfigs.getByName("release")')) return console.log("already patched", gradle);
  const config = `    signingConfigs {
        create("release") {
            val props = Properties().apply {
                rootProject.file("keystore.properties").inputStream().use { load(it) }
            }
            keyAlias = props.getProperty("keyAlias")
            keyPassword = props.getProperty("password")
            storePassword = props.getProperty("password")
            storeFile = rootProject.file(props.getProperty("storeFile"))
        }
    }
    buildTypes {`;
  src = src.replace(/^    buildTypes \{/m, config);
  src = src.replace(/(getByName\("release"\) \{\n)/, `$1            signingConfig = signingConfigs.getByName("release")\n`);
  writeFileSync(gradle, src);
  console.log("patched", gradle);
}

patchManifest();
patchActivity();
patchSigning();
