import importlib.util
import os
import subprocess
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("android_ci", Path(__file__).with_name("android-ci.py"))
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)

VALID = """package: name='dev.zcashflow.app' versionCode='10804' versionName='1.8.4' compileSdkVersion='37' compileSdkVersionCodename='17'
sdkVersion:'24'
targetSdkVersion:'37'
uses-permission: name='android.permission.INTERNET'
uses-permission: name='android.permission.ACCESS_LOCAL_NETWORK'
uses-permission: name='android.permission.CHANGE_WIFI_MULTICAST_STATE'
native-code: 'arm64-v8a'
"""


class AndroidCiTest(unittest.TestCase):
    def test_configure_existing_project_and_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gradle = root / "app/build.gradle.kts"
            gradle.parent.mkdir()
            gradle.write_text('''android {
    compileSdk = 35
    namespace = "dev.zcashflow.app"
    defaultConfig {
        targetSdk = 35
        minSdk = 24
    }
}
''')
            ci.configure(root)
            source = gradle.read_text()
            self.assertIn("compileSdk = 37", source)
            self.assertIn("targetSdk = 37", source)
            self.assertIn('buildToolsVersion = "37.0.0"', source)
            self.assertIn("minSdk = 24", source)
            self.assertIn('tasks.named("preBuild")', source)
            ci.configure(root)
            self.assertEqual(gradle.read_text(), source)

    def test_configure_replaces_existing_build_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gradle = root / "app/build.gradle.kts"
            gradle.parent.mkdir()
            gradle.write_text('android {\n compileSdk = 37\n buildToolsVersion = "35.0.0"\n defaultConfig {\n targetSdk = 37\n }\n}\n')
            ci.configure(root)
            self.assertEqual(gradle.read_text().count('buildToolsVersion = "37.0.0"'), 1)

    def test_unexpected_gradle_format_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gradle = root / "app/build.gradle.kts"
            gradle.parent.mkdir()
            source = 'android { compileSdk = providers.gradleProperty("sdk").get().toInt() }'
            gradle.write_text(source)
            with self.assertRaises(ValueError):
                ci.configure(root)
            self.assertEqual(gradle.read_text(), source)

    def test_migrate_legacy_task_without_losing_following_code(self):
        legacy = '''
// zcashflow-ci-sdk-check
val verifyAndroid37 by tasks.registering {
    doLast { check(android.compileSdk == 37) }
}
tasks.named("preBuild").configure { dependsOn(verifyAndroid37) }
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gradle = root / "app/build.gradle.kts"
            gradle.parent.mkdir()
            gradle.write_text('android {\n compileSdk = 37\n defaultConfig {\n targetSdk = 37\n }\n}\n'
                              + legacy + '\n// Preserve subsequent configuration\n')
            ci.configure(root)
            source = gradle.read_text()
            self.assertIn("abstract class VerifyAndroid37", source)
            self.assertNotIn("doLast", source)
            self.assertEqual(source.count("// zcashflow-ci-sdk-check"), 1)
            self.assertTrue(source.endswith("// Preserve subsequent configuration\n"))
            ci.configure(root)
            self.assertEqual(gradle.read_text(), source)

    @unittest.skipUnless(os.environ.get("GRADLE_BIN"), "Set GRADLE_BIN for the Gradle-only configuration cache regression check")
    def test_gradle_configuration_cache_round_trip_and_sdk_rejection(self):
        # Exercise the actual generated task with Gradle, without the Android SDK.
        # The stand-in only supplies the finalized DSL values to its properties.
        prefix = '''
class AndroidConfig {
    var compileSdk: Int? = 37
    val defaultConfig = Defaults()
    var buildToolsVersion = "37.0.0"
    class Defaults { var targetSdk: Int? = 37 }
}
class Components(private val dsl: AndroidConfig) {
    fun finalizeDsl(action: (AndroidConfig) -> Unit) { action(dsl) }
}
val android = AndroidConfig()
android.compileSdk = providers.gradleProperty("compileSdk").getOrElse("37").toInt()
android.defaultConfig.targetSdk = providers.gradleProperty("targetSdk").getOrElse("37").toInt()
android.buildToolsVersion = providers.gradleProperty("buildTools").getOrElse("37.0.0")
val androidComponents = Components(android)
tasks.register("preBuild")
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "settings.gradle.kts").write_text('rootProject.name = "sdk-check-regression"\n')
            (root / "build.gradle.kts").write_text(prefix + ci.SDK_TASK)
            def run(*extra):
                return subprocess.run([os.environ["GRADLE_BIN"], "-p", tmp, "preBuild",
                                       "--configuration-cache", "--configuration-cache-problems=fail",
                                       "--console=plain", *extra], text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            first = run()
            self.assertEqual(first.returncode, 0, first.stdout)
            self.assertIn("Configuration cache entry stored", first.stdout)
            report = root / "build/reports/android-sdk.txt"
            expected = "compileSdk=37\ntargetSdk=37\nbuildTools=37.0.0\n"
            self.assertEqual(report.read_text(), expected)
            report.unlink()  # Force execution from the restored cache, not UP-TO-DATE.
            second = run()
            self.assertEqual(second.returncode, 0, second.stdout)
            self.assertIn("Reusing configuration cache", second.stdout)
            self.assertEqual(report.read_text(), expected)
            for option, error in [("-PcompileSdk=36", "Expected compileSdk 37"),
                                  ("-PtargetSdk=36", "Expected targetSdk 37"),
                                  ("-PbuildTools=35.0.0", "Expected build tools 37.0.0")]:
                with self.subTest(option=option):
                    invalid = run(option)
                    self.assertNotEqual(invalid.returncode, 0, invalid.stdout)
                    self.assertIn(error, invalid.stdout)

    def test_final_manifest_arm64(self):
        self.assertEqual(ci.inspect_badging(VALID, True)["abis"], ["arm64-v8a"])

    def test_reject_wrong_package_sdks_permissions_or_abis(self):
        changes = [
            ("dev.zcashflow.app", "example.other"),
            ("compileSdkVersion='37'", "compileSdkVersion='35'"),
            ("targetSdkVersion:'37'", "targetSdkVersion:'35'"),
            ("ACCESS_LOCAL_NETWORK", "OTHER_PERMISSION"),
            ("CHANGE_WIFI_MULTICAST_STATE", "OTHER_PERMISSION"),
            ("native-code: 'arm64-v8a'", "native-code: 'arm64-v8a' 'armeabi-v7a'"),
            ("native-code: 'arm64-v8a'", ""),
        ]
        for before, after in changes:
            with self.subTest(before=before), self.assertRaises(ValueError):
                ci.inspect_badging(VALID.replace(before, after), True)

    def test_normal_build_can_keep_armv7(self):
        dual = VALID.replace("native-code: 'arm64-v8a'", "native-code: 'arm64-v8a' 'armeabi-v7a'")
        self.assertEqual(len(ci.inspect_badging(dual, False)["abis"]), 2)


if __name__ == "__main__":
    unittest.main()
