#!/usr/bin/env python3
"""Configure SDK 37, then inspect/package Android APKs using SDK build tools."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path("src-tauri/gen/android")
SDK_TASK = r'''
// zcashflow-ci-sdk-check
val verifyAndroid37 by tasks.registering {
    doLast {
        check(android.compileSdk == 37) { "Expected compileSdk 37, got ${android.compileSdk}" }
        check(android.defaultConfig.targetSdk == 37) { "Expected targetSdk 37, got ${android.defaultConfig.targetSdk}" }
        check(android.buildToolsVersion == "37.0.0") { "Expected build tools 37.0.0" }
        val report = layout.buildDirectory.file("reports/android-sdk.txt").get().asFile
        report.parentFile.mkdirs()
        report.writeText("compileSdk=${android.compileSdk}\ntargetSdk=${android.defaultConfig.targetSdk}\nbuildTools=${android.buildToolsVersion}\n")
        println(report.readText())
    }
}
tasks.named("preBuild").configure { dependsOn(verifyAndroid37) }
'''


def configure(root=ROOT):
    gradle = root / "app/build.gradle.kts"
    source = gradle.read_text()
    for setting in ("compileSdk", "targetSdk"):
        source, count = re.subn(rf"(?m)^(\s*{setting}\s*=\s*)\d+\s*$", rf"\g<1>37", source)
        if count != 1:
            raise ValueError(f"Expected exactly one numeric {setting} assignment, got {count}")
    if re.search(r"(?m)^\s*buildToolsVersion\s*=", source):
        source, count = re.subn(r'(?m)^(\s*buildToolsVersion\s*=\s*)"[^"]+"', r'\g<1>"37.0.0"', source)
        if count != 1:
            raise ValueError("Could not set buildToolsVersion")
    else:
        source, count = re.subn(r"(?m)^(android\s*\{)\s*$", '\\1\n    buildToolsVersion = "37.0.0"', source)
        if count != 1:
            raise ValueError("Android Gradle block not found")
    if "// zcashflow-ci-sdk-check" not in source:
        source += SDK_TASK
    gradle.write_text(source)
    print("Configured compileSdk=37, targetSdk=37, buildToolsVersion=37.0.0; Gradle preBuild will verify effective values.")


def inspect_badging(text, arm64_only):
    package = re.search(r"^package: name='([^']+)'", text, re.M)
    compile_sdk = re.search(r"\bcompileSdkVersion='(\d+)'", text)
    target_sdk = re.search(r"^targetSdkVersion:'(\d+)'", text, re.M)
    permissions = re.findall(r"^uses-permission(?:-sdk-23)?: name='([^']+)'", text, re.M)
    abi_line = re.search(r"^native-code: (.+)$", text, re.M)
    abis = re.findall(r"'([^']+)'", abi_line[1]) if abi_line else []
    if not package or package[1] != "dev.zcashflow.app":
        raise ValueError("Unexpected APK application ID")
    if not compile_sdk or compile_sdk[1] != "37":
        raise ValueError("APK was not compiled with SDK 37")
    if not target_sdk or target_sdk[1] != "37":
        raise ValueError("APK targetSdk is not 37")
    for permission in ("ACCESS_LOCAL_NETWORK", "CHANGE_WIFI_MULTICAST_STATE"):
        if f"android.permission.{permission}" not in permissions:
            raise ValueError(f"Final APK is missing {permission}")
    if arm64_only and abis != ["arm64-v8a"]:
        raise ValueError(f"Expected only ARM64 native code, got {abis}")
    return {"applicationId": package[1], "compileSdk": 37, "targetSdk": 37, "abis": abis, "permissions": permissions}


def command(*args, check=True):
    return subprocess.run([str(a) for a in args], check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def package_apks(test_build):
    tools = Path(os.environ["ANDROID_HOME"]) / "build-tools/37.0.0"
    out = Path("android-artifacts")
    out.mkdir(exist_ok=True)
    apks = sorted((ROOT / "app/build/outputs/apk").glob("**/release/*.apk"))
    if not apks or (test_build and len(apks) != 1):
        raise ValueError(f"Expected {'one ARM64 APK' if test_build else 'release APKs'}, found {len(apks)}")
    sdk_report = ROOT / "app/build/reports/android-sdk.txt"
    report = dict(line.split("=", 1) for line in sdk_report.read_text().splitlines())
    if report != {"compileSdk": "37", "targetSdk": "37", "buildTools": "37.0.0"}:
        raise ValueError(f"Unexpected effective Gradle configuration: {report}")
    shutil.copyfile(sdk_report, out / "android-sdk.txt")
    keystore = Path(os.environ["RUNNER_TEMP"]) / "zcashflow-test.jks"
    if test_build:
        command("keytool", "-genkeypair", "-noprompt", "-keystore", keystore,
                "-storepass", "android", "-keypass", "android", "-alias", "zcashflow-test",
                "-keyalg", "RSA", "-keysize", "2048", "-validity", "30",
                "-dname", "CN=ZCashflow temporary network test,OU=TEST ONLY,O=ZCashflow test")
    results = []
    try:
        for source in apks:
            if test_build:
                dest = out / "ZCashflow-1.8.4-sdk37-arm64-TEST-SIGNED.apk"
                aligned = Path(os.environ["RUNNER_TEMP"]) / "zcashflow-aligned.apk"
                command(tools / "zipalign", "-P", "16", "-f", "4", source, aligned)
                command(tools / "apksigner", "sign", "--ks", keystore,
                        "--ks-key-alias", "zcashflow-test", "--ks-pass", "pass:android",
                        "--key-pass", "pass:android", "--out", dest, aligned)
            else:
                dest = out / source.name
                shutil.copyfile(source, dest)
            badging = command(tools / "aapt2", "dump", "badging", dest).stdout
            (out / f"{dest.stem}-manifest.txt").write_text(badging)
            metadata = inspect_badging(badging, test_build)
            signature = command(tools / "apksigner", "verify", "--verbose", "--print-certs", dest, check=False)
            (out / f"{dest.stem}-signature.txt").write_text(signature.stdout)
            if test_build:
                if signature.returncode != 0:
                    raise ValueError("Test APK signature verification failed")
                command(tools / "zipalign", "-c", "-P", "16", "4", dest)
                signing = "TEMPORARY TEST KEY — different from the official application signing key"
            elif signature.returncode == 0:
                signing = "SIGNED with repository release keystore; match with official certificate NOT VERIFIED"
            else:
                signing = "UNSIGNED or invalid signature — APK is NOT installable as supplied"
            metadata.update(apk=dest.name, signing=signing, sha256=hashlib.sha256(dest.read_bytes()).hexdigest())
            results.append(metadata)
    finally:
        if test_build:
            keystore.unlink(missing_ok=True)
    (out / "verification.json").write_text(json.dumps(results, indent=2) + "\n")
    (out / "SHA256SUMS").write_text("".join(f"{r['sha256']}  {r['apk']}\n" for r in results))
    notice = "\n".join(f"- {r['apk']}: {r['signing']}" for r in results)
    readme = f"""# Android SDK 37 APK verification

Commit: {os.environ.get('GITHUB_SHA', 'local')}
Run: {os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}

Effective Gradle configuration and final APK compileSdk/targetSdk: 37.
ACCESS_LOCAL_NETWORK and CHANGE_WIFI_MULTICAST_STATE verified in each final APK.

{notice}

Test builds use a newly generated key for this run; the private key is deleted
and is never uploaded. This APK cannot update the officially signed application
or a test APK from another run. Export a backup before any uninstall: uninstalling
can delete local data. Signature certificate fingerprints are in *-signature.txt;
APK hashes are in SHA256SUMS. No GitHub release is created by this branch run.
"""
    (out / "README.md").write_text(readme)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(readme)
    print(readme)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["configure", "package"])
    args = parser.parse_args()
    if args.mode == "configure":
        configure()
    else:
        package_apks(os.environ.get("ANDROID_TEST_BUILD") == "true")
