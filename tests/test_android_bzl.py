#!/usr/bin/env python3
"""Hermetic checks on android.bzl that need no Bazel."""
from pathlib import Path
import re
import unittest

ANDROID_BZL = Path(__file__).resolve().parent.parent / "android.bzl"


class AndroidBzlTest(unittest.TestCase):
    def test_link_action_names_its_toolchain(self):
        # aapt2 comes from the SDK toolchain, so automatic exec groups
        # (--incompatible_auto_exec_groups) need the action to name it. Checked here rather than
        # by analysing the fixture with the flag: rules_android 0.8.0's own run_singlejar rule
        # fails analysis under it ("Action declared for non-existent toolchain").
        source = ANDROID_BZL.read_text()
        call = re.search(r"ctx\.actions\.run_shell\((.*?)\n    \)", source, re.S)
        self.assertIsNotNone(call)
        self.assertIn("toolchain = ANDROID_SDK_TOOLCHAIN_TYPE", call.group(1))

    def test_tools_are_not_on_the_propagating_aspect(self):
        source = ANDROID_BZL.read_text()
        collector = re.search(r"compose_preview_android_collector = aspect\((.*?)\n\)", source, re.S)
        self.assertIsNotNone(collector)
        self.assertNotIn("attrs", collector.group(1))
        self.assertNotIn("toolchains", collector.group(1))
        link = re.search(r"compose_preview_android_aspect = aspect\((.*?)\n\)", source, re.S)
        self.assertIsNotNone(link)
        self.assertNotIn("attr_aspects", link.group(1))
        self.assertIn("requires = [compose_preview_android_collector]", link.group(1))


if __name__ == "__main__":
    unittest.main()
