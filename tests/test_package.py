import importlib.util
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("package_rules", Path(__file__).resolve().parents[1] / "scripts/package.py")
package_rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package_rules)


class PackageTest(unittest.TestCase):
    def test_reproducible_archive_excludes_repository_and_build_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "module"
            source.mkdir()
            (source / "MODULE.bazel").write_text('module(name="rules_compose_preview", version="0.1.0-rc.1")\n')
            (source / "defs.bzl").write_text('# rules\n')
            (source / "README.md").write_text('# README\n')
            for name in (".git", "build", "__pycache__"):
                (source / name).mkdir()
                (source / name / "private-state").write_text("must never publish")
            (source / "MODULE.bazel.lock").write_text("local state")
            first = package_rules.package(source, root / "first")
            second = package_rules.package(source, root / "second")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first.name, "rules_compose_preview-0.1.0-rc.1.tar.gz")
            self.assertTrue(first.with_name(first.name + ".sha256").exists())
            with tarfile.open(first) as archive:
                self.assertEqual({Path(p).name for p in archive.getnames()}, {"MODULE.bazel", "defs.bzl", "README.md"})
                self.assertTrue(all(p.uid == 0 and p.mtime == 0 for p in archive.getmembers()))


if __name__ == "__main__":
    unittest.main()
