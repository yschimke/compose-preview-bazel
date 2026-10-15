#!/usr/bin/env python3
"""Hermetic checks on the render toolchain wiring; the external-consumer job runs it for real."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent


class RenderToolchainTest(unittest.TestCase):
    def test_toolchain_is_optional_and_only_it_makes_renders_cacheable(self):
        defs = (ROOT / "defs.bzl").read_text()
        self.assertIn("config_common.toolchain_type(RENDER_TOOLCHAIN_TYPE, mandatory = False)", defs)
        cacheable = re.search(r"if toolchain:(.*?)else:", defs, re.S).group(1)
        self.assertNotIn("no-cache", cacheable)
        self.assertNotIn("use_default_shell_env", cacheable)
        self.assertIn('"no-cache": "1"', defs.split("else:", 1)[1])

    def test_staged_render_turns_the_network_off(self):
        source = (ROOT / "render_toolchain.bzl").read_text()
        for setting in ("COMPOSE_PREVIEW_NO_NETWORK=1", "COMPOSE_PREVIEW_OFFLINE=1",
                        "-Dcomposeai.network=off", "XDG_CACHE_HOME", "-Dmaven.repo.local",
                        "-Duser.timezone=UTC", "export TZ=UTC"):
            self.assertIn(setting, source)

    def test_each_render_stages_into_its_own_directory(self):
        source = (ROOT / "render_toolchain.bzl").read_text()
        self.assertNotIn("_compose_preview_render", source)
        # A fresh temporary directory, never an undeclared path beside the output.
        self.assertIn('mktemp -d "${TMPDIR:-/tmp}/compose-preview-stage.XXXXXX"', source)
        self.assertIn("trap 'rm -rf \\\"$stage\\\"' EXIT", source)
        defs = (ROOT / "defs.bzl").read_text()
        self.assertNotIn(".stage", defs)
        # The render must not `exec`, or the trap that removes the stage never runs.
        cacheable = re.search(r"if toolchain:(.*?)else:", defs, re.S).group(1)
        self.assertNotIn('exec "$@"', cacheable)

    def test_staged_setup_runs_and_cleans_up(self):
        import subprocess, tempfile
        source = (ROOT / "render_toolchain.bzl").read_text()
        lines = re.search(r"lines = \[\n(.*?)\n    \]", source, re.S).group(1)
        setup = [eval(line.strip().rstrip(",")) for line in lines.splitlines()
                 if line.strip() and not line.strip().startswith("#")]
        with tempfile.TemporaryDirectory() as tmp:
            script = "\n".join(["set -eu"] + setup + ['mkdir -p "$stage/home"', 'echo "$stage"'])
            result = subprocess.run(["sh", "-c", script], env={"TMPDIR": tmp, "PATH": "/usr/bin:/bin"},
                                    check=True, capture_output=True, text=True)
            stage = Path(result.stdout.strip())
            self.assertEqual(Path(tmp), stage.parent)
            self.assertFalse(stage.exists(), "the EXIT trap removes the stage")

    def test_maven_links_do_not_assume_a_repository_prefix(self):
        source = (ROOT / "render_toolchain.bzl").read_text()
        self.assertNotIn("*/v1/", source)
        self.assertIn("processed_", (ROOT / "stage_maven.sh").read_text())

    def test_maven_staging_matches_lockfile_pins_wherever_the_jar_is_kept(self):
        import os, subprocess, tempfile
        with tempfile.TemporaryDirectory() as work:
            os.chdir(work)
            jars = ["bin/external/rules_jvm_external++maven+maven/processed_ui-desktop-1.11.1.jar",
                    "external/rules_kotlin/kotlin-stdlib.jar",
                    "x/v1/https/h/maven2/androidx/annotation/annotation/1.1.0/annotation-1.1.0.jar"]
            for jar in jars:
                Path(jar).parent.mkdir(parents=True, exist_ok=True)
                Path(jar).touch()
            Path("jars.txt").write_text("\n".join(jars) + "\n")
            Path("lock.json").write_text(
                '{"artifacts": {"org.jetbrains.compose.ui:ui-desktop": {"shasums": {"jar": "ab", "sources": null}, "version": "1.11.1"},'
                ' "org.jetbrains.kotlin:kotlin-stdlib": {"version": "2.2.20"},'
                ' "com.example:with-classifier:jar:natives": {"version": "1"}}, "version": "2"}')
            subprocess.run(["sh", str(ROOT / "stage_maven.sh"), "m2", "jars.txt", "lock.json"], check=True)
            for staged, source in [
                ("m2/org/jetbrains/compose/ui/ui-desktop/1.11.1/ui-desktop-1.11.1.jar", jars[0]),
                ("m2/org/jetbrains/kotlin/kotlin-stdlib/2.2.20/kotlin-stdlib-2.2.20.jar", jars[1]),
                ("m2/androidx/annotation/annotation/1.1.0/annotation-1.1.0.jar", jars[2]),
            ]:
                # A regular file, not a symlink: the CLI's resolver does not follow symlinks.
                self.assertFalse(os.path.islink(staged), staged)
                self.assertTrue(os.path.samefile(staged, source), staged)
            self.assertFalse(Path("m2/com/example").exists())
            os.chdir(ROOT)

    def test_artifacts_name_each_runtime_jars_owner(self):
        runtime = (ROOT / "runtime.bzl").read_text()
        self.assertIn('_json_member(args, "runtimeJarOwners", runtime, map_each = _json_owner, brackets = "{}")', runtime)
        owner = re.search(r"def _json_owner\(file\):(.*?)\n\n", runtime, re.S).group(1)
        self.assertIn("if not file.owner:\n        return None", owner)
        self.assertIn('json.encode(file.path) + ":" + json.encode(str(file.owner))', owner)
        # Every writer of artifacts metadata shares that encoding.
        for name in ("runtime.bzl", "android.bzl", "defs.bzl"):
            self.assertIn("ctx.actions.write(", (ROOT / name).read_text(), name)
            self.assertIn("compose_preview_metadata_args(", (ROOT / name).read_text(), name)

    def test_metadata_is_written_lazily(self):
        # The runtime closure reaches the metadata as a depset through Args, not a flattened list.
        for name in ("runtime.bzl", "android.bzl", "defs.bzl"):
            self.assertNotIn("json.encode({", (ROOT / name).read_text(), name)
        self.assertIn('set_param_file_format("multiline")', (ROOT / "runtime.bzl").read_text())

    def test_android_render_stages_the_android_daemon_and_android_all_offline(self):
        source = (ROOT / "render_toolchain.bzl").read_text()
        for setting in ('("lib-daemon-android", info.android_daemon)', '("robolectric-deps", info.android_all)',
                        "-Dcomposeai.cli.libDaemonAndroidDir=$stage/lib-daemon-android",
                        "-Dcomposeai.cli.libRendererAndroidDir=$stage/lib-daemon-android",
                        'export COMPOSE_PREVIEW_ROBOLECTRIC_DEPS_DIR="$stage/robolectric-deps"'):
            self.assertIn(setting, source)
        # Cacheable only when both Android inputs are declared.
        self.assertIn("return bool(info.android_daemon) and bool(info.android_all)", source)
        defs = (ROOT / "defs.bzl").read_text()
        self.assertIn("if toolchain and android and not renders_android(toolchain.compose_preview_render):", defs)
        self.assertIn("android = bool(android),", defs)

    def test_android_render_passes_its_sdk_jar_and_clock(self):
        defs = (ROOT / "defs.bzl").read_text()
        for flag in ('"--android-jar"', '"--android-sdk"', '"--clock-packages"'):
            self.assertIn(flag, defs)
        # Default on, like Gradle's composePreview.pinAppClock and bazel.json's pinAppClock.
        self.assertRegex(defs, r'"pin_app_clock": attr\.bool\(\s*default = True')

    def test_module_aspect_skips_non_android_targets(self):
        android = (ROOT / "android.bzl").read_text()
        aspect = android.split("compose_preview_android_module_aspect = aspect(", 1)[1]
        # A Desktop module never resolves the Android SDK toolchain.
        self.assertIn("required_providers = [AndroidIdeInfo]", aspect)
        self.assertIn("aspects = [compose_preview_android_module_aspect]", (ROOT / "defs.bzl").read_text())

    def test_toolchain_type_is_declared(self):
        self.assertIn('toolchain_type(name = "render_toolchain_type")', (ROOT / "BUILD.bazel").read_text())


if __name__ == "__main__":
    unittest.main()
