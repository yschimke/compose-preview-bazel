#!/usr/bin/env python3
"""Build tests/android_aspect with the Android aspect and check its schema-2 output.

Needs Bazel (bazelisk), an Android SDK (ANDROID_HOME) and network access, so it runs
only when COMPOSE_PREVIEW_ANDROID_ASPECT_TEST=1; the plain `unittest discover` run
stays hermetic.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
import zipfile

WORKSPACE = Path(__file__).resolve().parent / "android_aspect"
ASPECT = "@rules_compose_preview//:android.bzl%compose_preview_android_aspect"


def bazel():
    return shutil.which("bazelisk") or shutil.which("bazel")


@unittest.skipUnless(
    os.environ.get("COMPOSE_PREVIEW_ANDROID_ASPECT_TEST") == "1" and bazel()
    and os.environ.get("ANDROID_HOME"),
    "set COMPOSE_PREVIEW_ANDROID_ASPECT_TEST=1 with Bazel and ANDROID_HOME",
)
class AndroidAspectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        events = WORKSPACE / "bazel-bep.json"
        subprocess.run(
            [bazel(), "build", "//ui", f"--aspects={ASPECT}",
             "--output_groups=compose_preview_metadata,compose_preview_runtime",
             f"--build_event_json_file={events}"],
            cwd=WORKSPACE, check=True)
        cls.root = Path(subprocess.run([bazel(), "info", "execution_root"], cwd=WORKSPACE,
                                       check=True, capture_output=True, text=True).stdout.strip())
        cls.metadata = json.loads((WORKSPACE / "bazel-bin/ui/ui.compose-preview.json").read_text())

    def test_schema_2_with_android_block(self):
        self.assertEqual(2, self.metadata["schemaVersion"])
        android = self.metadata["android"]
        self.assertEqual("com.example.ui", android["packageName"])
        for key in ("resourceApk", "manifest", "rJar"):
            self.assertTrue((self.root / android[key]).is_file(), key)
        # Optional in the schema: the SDK layout may not name a platform level.
        self.assertIsInstance(android.get("compileSdk", 0), int)

    def test_exported_classes_are_the_targets_own(self):
        jars = self.metadata["projectJars"]
        self.assertTrue(any("ui_impl" in jar for jar in jars), jars)
        self.assertFalse(any("/theme/" in jar for jar in jars), jars)

    def test_final_id_r_classes_precede_every_library_r_class(self):
        runtime = self.metadata["runtimeJars"]
        self.assertEqual(self.metadata["android"]["rJar"], runtime[0])
        with zipfile.ZipFile(self.root / runtime[0]) as jar:
            names = set(jar.namelist())
        self.assertIn("com/example/ui/R$string.class", names)
        self.assertIn("com/example/theme/R$color.class", names)

    def test_resource_apk_links_dependency_resources(self):
        apk = self.root / self.metadata["android"]["resourceApk"]
        with zipfile.ZipFile(apk) as archive:
            self.assertIn("resources.arsc", archive.namelist())
            table = archive.read("resources.arsc")
        # aapt2 writes resource names to a UTF-8 key pool.
        self.assertIn(b"greeting", table)
        self.assertIn(b"brand", table)
        # The target's own value overlays a dependency's conflicting one.
        self.assertIn(b"Hello from Bazel", table)
        self.assertNotIn(b"Theme greeting", table)


if __name__ == "__main__":
    unittest.main()
