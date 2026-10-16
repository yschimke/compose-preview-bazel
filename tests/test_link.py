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
            self.assertEqual(PACKAGES, len(classes))
            self.assertEqual({"resources.ap_", "r-final.jar"}, {p.name for p in out.iterdir()})


if __name__ == "__main__":
    unittest.main()
