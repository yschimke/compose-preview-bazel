#!/usr/bin/env python3
"""Hermetic checks on the `bazel run` launchers; the external-consumer job runs them for real."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent


class LauncherTest(unittest.TestCase):
    def test_launchers_resolve_through_the_runfiles_library(self):
        launcher = (ROOT / "launcher.bzl").read_text()
        self.assertIn('Label("@rules_shell//shell/runfiles")', launcher)
        self.assertIn("# --- begin runfiles.bash initialization v3 ---", launcher)
        self.assertIn('$(rlocationpath %s)', launcher)
        for name in ("defs.bzl", "tool.bzl"):
            source = (ROOT / name).read_text()
            self.assertIn("RUNFILES_INIT", source, name)
            self.assertIn('"_runfiles_library": attr.label(default = RUNFILES_LIBRARY)', source, name)
            self.assertIn("ctx.attr._runfiles_library[DefaultInfo].default_runfiles", source, name)
            self.assertIn("rlocation '", source, name)
            # Neither requires a runfiles directory nor hand-builds a path into one.
            self.assertNotIn("requires directory runfiles", source, name)
            self.assertNotIn('"$rf/', source, name)

    def test_rules_depend_on_rules_shell(self):
        self.assertRegex((ROOT / "MODULE.bazel").read_text(), r'bazel_dep\(name = "rules_shell", version = "[^"]+"\)')

    def test_run_launcher_keeps_its_interface(self):
        defs = (ROOT / "defs.bzl").read_text()
        render = re.search(r'_RENDER_LAUNCHER = """(.*?)"""', defs, re.S).group(1)
        self.assertIn("render || \"${{1:-}}\" == inspect || \"${{1:-}}\" == extract", render)
        self.assertIn("${{BUILD_WORKSPACE_DIRECTORY:-$PWD}}/.compose-preview/outputs/{name}", render)
        self.assertIn("COMPOSE_PREVIEW_OUT", render)
        self.assertIn('[[ "$arg" == --output || "$arg" == -o ]]', render)
        source_only = re.search(r'_SOURCE_ONLY_LAUNCHER = """(.*?)"""', defs, re.S).group(1)
        self.assertIn("exit 64", source_only)
        # The launcher bodies are indented consistently: no line starts with a stray indent.
        for line in (render + source_only).splitlines():
            self.assertEqual(0, (len(line) - len(line.lstrip(" "))) % 2, line)

    def test_producer_stays_an_exec_tool(self):
        tool = (ROOT / "tool.bzl").read_text()
        self.assertIn("It is an exec tool, not a run target", tool)
        self.assertIn("java.java_executable_exec_path", tool)


if __name__ == "__main__":
    unittest.main()
