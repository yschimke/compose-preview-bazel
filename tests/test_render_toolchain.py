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
                        "-Dcomposeai.network=off", "XDG_CACHE_HOME", "-Dmaven.repo.local"):
            self.assertIn(setting, source)

    def test_toolchain_type_is_declared(self):
        self.assertIn('toolchain_type(name = "render_toolchain_type")', (ROOT / "BUILD.bazel").read_text())


if __name__ == "__main__":
    unittest.main()
