"""link.sh on a large resource closure: scratch beside the output, batched javac, no /tmp.

aapt2 is replaced by a stub that writes as many R.java files as a large target generates; the JDK
is real, so the batching and the R jar are what an executor would produce.
"""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
import zipfile

LINK = Path(__file__).resolve().parents[1] / "link.sh"
PACKAGES = 250


def jdk_home():
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin/javac").is_file():
        return Path(home)
    javac = shutil.which("javac")
    return Path(javac).resolve().parents[1] if javac else None


# Writes the APK and one R.java per package into the `--java` directory, as aapt2 link does.
FAKE_AAPT2 = """#!/bin/sh
set -eu
while [ $# -gt 0 ]; do
  case "$1" in
    -o) apk="$2"; shift 2 ;;
    --java) gen="$2"; shift 2 ;;
    *) shift ;;
  esac
done
printf 'apk' > "$apk"
i=0
while [ $i -lt %d ]; do
  mkdir -p "$gen/pkg$i"
  printf 'package pkg%%s;\\npublic final class R { public static final class string { public static final int app_name = 0x7f010000; } }\\n' "$i" > "$gen/pkg$i/R.java"
  i=$((i + 1))
done
""" % PACKAGES


@unittest.skipIf(jdk_home() is None, "needs a JDK (javac)")
class LinkTest(unittest.TestCase):
    def test_large_closure_links_beside_the_output_without_tmp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            aapt2 = root / "tools/aapt2"
            aapt2.parent.mkdir()
            aapt2.write_text(FAKE_AAPT2)
            aapt2.chmod(aapt2.stat().st_mode | stat.S_IXUSR)
            out = root / "out dir"
            out.mkdir()
            apk = out / "resources.ap_"
            # A caller's own directory beside the output, named as a fixed scratch path would be.
            mine = out / "resources.ap_.work"
            mine.mkdir()
            (mine / "keep").write_text("caller-owned")
            rjar = out / "r-final.jar"
            manifest = root / "AndroidManifest.xml"
            manifest.write_text("<manifest package='pkg0'/>")
            android_jar = root / "android.jar"
            android_jar.write_bytes(b"")
            assets = root / "assets.tsv"
            assets.write_text("")
            # A temp directory that does not exist: any mktemp would fail, even as root.
            env = dict(os.environ, TMPDIR=str(root / "no-tmp"))
            subprocess.run(
                ["sh", str(LINK), str(aapt2), str(jdk_home()), str(apk), str(rjar), str(manifest),
                 str(android_jar), "", str(assets)],
                check=True, env=env, capture_output=True, text=True,
            )
            with zipfile.ZipFile(rjar) as jar:
                classes = {n for n in jar.namelist() if n.endswith("/R.class")}
                header = jar.read("pkg0/R.class")[:8]
            self.assertEqual(PACKAGES, len(classes))
            # Class file major version 55: loadable on Java 11 whatever JDK compiled it.
            self.assertEqual(55, int.from_bytes(header[6:8], "big"))
            self.assertEqual({"resources.ap_", "r-final.jar", "resources.ap_.work"}, {p.name for p in out.iterdir()})
            self.assertEqual("caller-owned", (mine / "keep").read_text())

    def test_manifest_entries_stand_for_their_packages(self):
        # rules_android keys an aar_import's R.txt under no package, so the aspect passes the
        # AAR's manifest instead, and link.sh reads the package from it.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            aapt2 = root / "aapt2"
            aapt2.write_text(
                "#!/bin/sh\nset -eu\nwhile [ $# -gt 0 ]; do case \"$1\" in\n"
                "  -o) printf apk > \"$2\"; shift 2 ;;\n"
                "  --java) mkdir -p \"$2\"; shift 2 ;;\n"
                "  --extra-packages) printf '%s' \"$2\" > \"$EXTRA_OUT\"; shift 2 ;;\n"
                "  *) shift ;;\nesac; done\n"
            )
            aapt2.chmod(aapt2.stat().st_mode | stat.S_IXUSR)
            manifest = root / "AndroidManifest.xml"
            manifest.write_text('<manifest package="com.example.app"/>')
            aar = root / "external/maven/ui_aar/AndroidManifest.xml"
            aar.parent.mkdir(parents=True)
            # Multi-line, with the package attribute after others, as AGP writes an AAR's.
            aar.write_text(
                '<?xml version="1.0" encoding="utf-8"?>\n<manifest\n'
                '    xmlns:android="http://schemas.android.com/apk/res/android"\n'
                '    package="androidx.compose.ui" >\n  <uses-sdk android:minSdkVersion="23" />\n'
                '</manifest>\n'
            )
            own = root / "own/AndroidManifest.xml"
            own.parent.mkdir()
            own.write_text("<manifest package='com.example.app'/>")
            none = root / "none/AndroidManifest.xml"
            none.parent.mkdir()
            none.write_text("<manifest/>")
            out = root / "out"
            out.mkdir()
            extra_out = root / "extra.txt"
            # A read-only asset directory, as Bazel leaves its outputs: staging copies its mode,
            # and the cleanup must still remove it.
            generated = root / "generated"
            generated.mkdir()
            (generated / "empty_asset_generated_by_bazel~").write_text("")
            generated.chmod(0o555)
            assets = root / "assets.tsv"
            assets.write_text(f"{generated}\twidget/assets\n")
            extra = ":".join(
                ["androidx.core", str(aar), str(own), str(none), "androidx.core", str(root / "missing/AndroidManifest.xml")]
            )
            result = subprocess.run(
                ["sh", str(LINK), str(aapt2), str(jdk_home()), str(out / "resources.ap_"),
                 str(out / "r-final.jar"), str(manifest), str(root / "android.jar"), extra, str(assets)],
                check=True, env=dict(os.environ, EXTRA_OUT=str(extra_out)), capture_output=True, text=True,
            )
            generated.chmod(0o755)
            # Deduplicated and sorted; the APK's own package and a package-less manifest add none.
            self.assertEqual("androidx.compose.ui:androidx.core", extra_out.read_text())
            self.assertIn("no manifest at", result.stderr)


if __name__ == "__main__":
    unittest.main()
