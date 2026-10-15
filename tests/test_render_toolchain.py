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
        self.assertNotIn("_compose_preview_render", (ROOT / "render_toolchain.bzl").read_text())
        self.assertIn('output.path + ".stage"', (ROOT / "defs.bzl").read_text())

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
                self.assertEqual(os.path.realpath(staged), os.path.realpath(source))
            self.assertFalse(Path("m2/com/example").exists())
            os.chdir(ROOT)

    def test_toolchain_type_is_declared(self):
        self.assertIn('toolchain_type(name = "render_toolchain_type")', (ROOT / "BUILD.bazel").read_text())


if __name__ == "__main__":
    unittest.main()
