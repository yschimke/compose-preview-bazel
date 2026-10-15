#!/usr/bin/env python3
"""The render action's execution requirements, read with aquery from tests/analysis's targets.

Starlark analysis tests cannot see execution requirements, so this complements
tests/analysis. Needs Bazel, so it runs only when COMPOSE_PREVIEW_BAZEL_TEST=1; the plain
`unittest discover` run stays hermetic.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "//tests/analysis"
LOCAL_ONLY = {"local", "no-cache", "no-remote"}


def bazel():
    return shutil.which("bazelisk") or shutil.which("bazel")


def render_requirements(target, *flags):
    query = subprocess.run(
        [bazel(), "aquery", f"mnemonic('ComposePreview(Local)?Render', {PACKAGE}:{target})",
         "--output=jsonproto", *flags],
        cwd=ROOT, check=True, capture_output=True, text=True).stdout
    actions = json.loads(query).get("actions", [])
    return [(a["mnemonic"], {e["key"] for e in a.get("executionInfo", [])}) for a in actions]


@unittest.skipUnless(os.environ.get("COMPOSE_PREVIEW_BAZEL_TEST") == "1" and bazel(),
                     "set COMPOSE_PREVIEW_BAZEL_TEST=1 with Bazel")
class RenderRequirementsTest(unittest.TestCase):
    def test_toolchain_render_has_no_local_requirements(self):
        actions = render_requirements("test_toolchain_render_is_cacheable_subject",
                                      f"--extra_toolchains={PACKAGE}:render_toolchain")
        self.assertEqual(["ComposePreviewRender"], [mnemonic for mnemonic, _ in actions])
        self.assertFalse(actions[0][1] & LOCAL_ONLY, actions)

    def test_render_without_toolchain_is_local_and_uncached(self):
        actions = render_requirements("test_render_without_toolchain_is_local_subject")
        self.assertEqual(["ComposePreviewLocalRender"], [mnemonic for mnemonic, _ in actions])
        self.assertTrue(LOCAL_ONLY <= actions[0][1], actions)

    def test_android_render_with_android_inputs_has_no_local_requirements(self):
        actions = render_requirements("test_android_render_with_android_toolchain_is_cacheable_subject",
                                      f"--extra_toolchains={PACKAGE}:android_sdk_toolchain",
                                      f"--extra_toolchains={PACKAGE}:android_render_toolchain")
        self.assertEqual(["ComposePreviewRender"], [mnemonic for mnemonic, _ in actions])
        self.assertFalse(actions[0][1] & LOCAL_ONLY, actions)

    def test_android_render_without_android_inputs_is_local_and_uncached(self):
        actions = render_requirements("test_android_render_with_desktop_toolchain_is_local_subject",
                                      f"--extra_toolchains={PACKAGE}:android_sdk_toolchain",
                                      f"--extra_toolchains={PACKAGE}:render_toolchain")
        self.assertEqual(["ComposePreviewLocalRender"], [mnemonic for mnemonic, _ in actions])
        self.assertTrue(LOCAL_ONLY <= actions[0][1], actions)


if __name__ == "__main__":
    unittest.main()
