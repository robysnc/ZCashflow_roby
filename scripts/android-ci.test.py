import importlib.util
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
