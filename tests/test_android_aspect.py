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
            [bazel(), "build", "//ui", "//feature", f"--aspects={ASPECT}",
             "--output_groups=compose_preview_metadata,compose_preview_runtime",
             f"--build_event_json_file={events}"],
            cwd=WORKSPACE, check=True)
        cls.root = Path(subprocess.run([bazel(), "info", "execution_root"], cwd=WORKSPACE,
                                       check=True, capture_output=True, text=True).stdout.strip())
        cls.metadata = json.loads((WORKSPACE / "bazel-bin/ui/ui.compose-preview.json").read_text())
        cls.feature = json.loads((WORKSPACE / "bazel-bin/feature/feature.compose-preview.json").read_text())

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

    def test_exported_sources_are_the_targets_own(self):
        # //ui has no srcs of its own; its sources are those of the ui_impl it exports
        # (compose-ai-tools#6112).
        sources = self.metadata["sourceFiles"]
        self.assertTrue(any(s.endswith("com/example/ui/Greeting.java") for s in sources), sources)
        self.assertFalse(any("/theme/" in s for s in sources), sources)

    def test_final_id_r_classes_precede_every_library_r_class(self):
        runtime = self.metadata["runtimeJars"]
        self.assertEqual(self.metadata["android"]["rJar"], runtime[0])
        with zipfile.ZipFile(self.root / runtime[0]) as jar:
            names = set(jar.namelist())
        self.assertIn("com/example/ui/R$string.class", names)
        self.assertIn("com/example/theme/R$color.class", names)
        # An aar_import's package, which rules_android does not report, read from its manifest.
        self.assertIn("com/example/widget/R$string.class", names)

    def test_every_runtime_jar_names_its_producing_target(self):
        runtime = self.metadata["runtimeJars"]
        owners = self.metadata["runtimeJarOwners"]
        self.assertTrue(set(owners) <= set(runtime), set(owners) - set(runtime))
        self.assertEqual(set(runtime), set(owners))
        for path, label in owners.items():
            self.assertRegex(label, r"^@*[^/]*//", path)
        # The selected target's own jars are owned by targets in the fixture's own packages.
        project = self.metadata["projectJars"]
        self.assertTrue(all(owners[jar].lstrip("@").startswith("//") for jar in project), owners)

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

    def test_resource_apk_packs_transitive_assets(self):
        apk = self.root / self.metadata["android"]["resourceApk"]
        with zipfile.ZipFile(apk) as archive:
            names = set(archive.namelist())
            self.assertIn("assets/theme-only.txt", names)
            self.assertIn("assets/nested/ui-only.txt", names)
            # The target's own asset replaces a dependency's at the same path.
            self.assertEqual(b"ui", archive.read("assets/shared.txt"))

    def test_link_inputs_are_recorded_and_materialized(self):
        link = self.metadata["android"]["link"]
        self.assertEqual("com.example.ui", link["packages"][0])
        self.assertIn("com.example.theme", link["packages"])
        for key in ("script", "aapt2", "androidJar"):
            self.assertTrue((self.root / link[key]).is_file(), key)
        # The SDK's real binary, which runs outside Bazel, not the toolchain's runfiles launcher.
        self.assertIn("/build-tools/", link["aapt2"])
        self.assertNotIn("_binary", link["aapt2"])
        self.assertTrue((self.root / link["javaHome"] / "bin/javac").exists()
                        or Path(link["javaHome"], "bin/javac").exists())
        archives = link["compiledResources"]
        self.assertTrue(archives and all((self.root / a).is_file() for a in archives), archives)
        # Overlay order: the theme's archive before the target's own.
        self.assertLess(next(i for i, a in enumerate(archives) if "/theme/" in a),
                        next(i for i, a in enumerate(archives) if "/ui/" in a))
        self.assertIn("theme-only.txt", {a["path"] for a in link["assets"]})

    def test_only_the_selected_target_links(self):
        # The link aspect does not propagate: the libraries the collector walks (theme) get no
        # link action, and so carry none of the link's tools as dependencies.
        query = subprocess.run(
            [bazel(), "aquery", 'mnemonic("ComposePreviewAndroidLink", deps(//ui))',
             f"--aspects={ASPECT}", "--output=text"],
            cwd=WORKSPACE, check=True, capture_output=True, text=True).stdout
        owners = [line for line in query.splitlines() if line.strip().startswith("Target:")]
        self.assertEqual(["Target: //ui:ui"], [o.strip() for o in owners], query[-2000:])

    def test_one_link_over_two_libraries_renders_both(self):
        # What the CLI does for a selection: one link.sh run over the union of the targets' inputs.
        targets = [self.metadata["android"]["link"], self.feature["android"]["link"]]
        archives, packages, assets = [], [], {}
        for link in targets:
            archives += [a for a in link["compiledResources"] if a not in archives]
            packages += [p for p in link["packages"] if p not in packages]
            for asset in link["assets"]:
                assets[asset["path"]] = asset["file"]
        out = WORKSPACE / "bazel-merged"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir()
        manifest = out / "AndroidManifest.xml"
        manifest.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
                            f'package="{packages[0]}"><application/></manifest>')
        listing = out / "assets.txt"
        listing.write_text("".join(f"{f}\t{p}\n" for p, f in assets.items()))
        link = targets[0]
        subprocess.run(["/bin/sh", link["script"], link["aapt2"], link["javaHome"], str(out / "merged.ap_"),
                        str(out / "r-merged.jar"), str(manifest), link["androidJar"], ":".join(packages[1:]),
                        str(listing), *archives], cwd=self.root, check=True)
        with zipfile.ZipFile(out / "merged.ap_") as archive:
            names = set(archive.namelist())
            table = archive.read("resources.arsc")
        for value in (b"greeting", b"feature_title", b"brand", b"Hello from Bazel", b"Feature from Bazel",
                      b"Widget from an AAR"):
            self.assertIn(value, table)
        self.assertNotIn(b"Theme greeting", table)
        self.assertTrue({"assets/theme-only.txt", "assets/nested/ui-only.txt", "assets/feature-only.txt"} <= names)
        with zipfile.ZipFile(out / "r-merged.jar") as jar:
            classes = set(jar.namelist())
        for package in ("com/example/ui", "com/example/feature", "com/example/theme", "com/example/widget"):
            self.assertIn(f"{package}/R$string.class", classes)


if __name__ == "__main__":
    unittest.main()
