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
                "  --java) mkdir -p \"$2/com/example/app\";"
                " printf 'package com.example.app;\\npublic final class R {}\\n' > \"$2/com/example/app/R.java\"; shift 2 ;;\n"
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
            # A root package's manifest path has no directory.
            (root / "root.xml").write_text('<manifest package="com.example.root"/>')
            extra = ":".join(
                ["androidx.core", "root.xml", "com.example.xml", str(aar), str(own), str(none), "androidx.core", str(root / "missing/AndroidManifest.xml")]
            )
            result = subprocess.run(
                ["sh", str(LINK), str(aapt2), str(jdk_home()), str(out / "resources.ap_"),
                 str(out / "r-final.jar"), str(manifest), str(root / "android.jar"), extra, str(assets)],
                check=True, env=dict(os.environ, EXTRA_OUT=str(extra_out)), capture_output=True, text=True,
                cwd=root,
            )
            generated.chmod(0o755)
            # Deduplicated; the APK's own package and a package-less manifest add none. aapt2 is no
            # longer asked for the copies: link.sh clones the own package's compiled R classes.
            self.assertFalse(extra_out.exists())
            with zipfile.ZipFile(out / "r-final.jar") as jar:
                classes = sorted(n for n in jar.namelist() if n.endswith(".class"))
            self.assertEqual(
                ["androidx/compose/ui/R.class", "androidx/core/R.class", "com/example/app/R.class",
                 "com/example/root/R.class", "com/example/xml/R.class"],
                classes,
            )
            self.assertIn("no manifest at", result.stderr)


# R sources shaped as aapt2 writes them for an APK: final IDs, nested classes per type, and
# styleable arrays, which javac initialises in <clinit>.
R_JAVA = """package %s;

public final class R {
  public static final class attr {
    public static final int size=0x7f010000;
  }
  public static final class color {
    public static final int brand=0x7f020000;
  }
  public static final class id {
    public static final int root=0x7f030000;
  }
  public static final class string {
    public static final int app_name=0x7f040000;
    public static final int greeting=0x7f040001;
  }
  public static final class style {
    public static final int Theme_App=0x7f050000;
  }
  public static final class styleable {
    public static final int[] Widget={
      0x0101014f, 0x7f010000
    };
    public static final int Widget_android_text=0;
    public static final int Widget_size=1;
  }
}
"""

RICH_AAPT2 = """#!/bin/sh
set -eu
extra=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) printf apk > "$2"; shift 2 ;;
    --java) gen="$2"; shift 2 ;;
    --extra-packages) extra="$2"; shift 2 ;;
    *) shift ;;
  esac
done
# As aapt2 does: the manifest's package, then one identical copy per extra package.
for package in com.example.app $(printf '%s' "$extra" | tr ':' ' '); do
  dir="$gen/$(printf '%s' "$package" | tr . /)"
  mkdir -p "$dir"
  printf '%s' "$R_JAVA" | sed "1s/.*/package $package;/" > "$dir/R.java"
done
"""


def javac_reference(root, packages, source_for):
    """What link.sh produced before #6110: javac over one R.java per package."""
    gen = root / "reference-src"
    for package in packages:
        target = gen / package.replace(".", "/") / "R.java"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source_for(package))
    classes = root / "reference-classes"
    subprocess.run(
        [str(jdk_home() / "bin/javac"), "--release", "11", "-nowarn", "-encoding", "UTF-8",
         "-d", str(classes), *map(str, sorted(gen.rglob("*.java")))],
        check=True, capture_output=True, text=True,
    )
    return {str(f.relative_to(classes)): f.read_bytes() for f in classes.rglob("*.class")}


def jar_classes(jar_path):
    with zipfile.ZipFile(jar_path) as jar:
        return {n: jar.read(n) for n in jar.namelist() if n.endswith(".class")}


@unittest.skipIf(jdk_home() is None, "needs a JDK (javac)")
class CloneTest(unittest.TestCase):
    def test_cloned_r_classes_are_what_javac_compiles_for_each_package(self):
        # compose-ai-tools#6110: one compile, cloned into each extra package, must be byte for
        # byte what javac made of aapt2's per-package copies. The extras include a package nested
        # in the own one and names that share a prefix with it, which a substring rename would hit.
        own = "com.example.app"
        extras = ["com.example.app.sub", "com.example.apps", "com.example", "org.b.c", "a"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            aapt2 = root / "aapt2"
            aapt2.write_text(RICH_AAPT2)
            aapt2.chmod(aapt2.stat().st_mode | stat.S_IXUSR)
            manifest = root / "AndroidManifest.xml"
            manifest.write_text(f'<manifest package="{own}"/>')
            assets = root / "assets.tsv"
            assets.write_text("")
            rjar = root / "r-final.jar"
            subprocess.run(
                ["sh", str(LINK), str(aapt2), str(jdk_home()), str(root / "resources.ap_"), str(rjar),
                 str(manifest), str(root / "android.jar"), ":".join(extras), str(assets)],
                check=True, env=dict(os.environ, R_JAVA=R_JAVA), capture_output=True, text=True,
            )
            actual = jar_classes(rjar)
            expected = javac_reference(root, [own] + extras, lambda package: R_JAVA % package)
            self.assertEqual(sorted(expected), sorted(actual))
            self.assertIn("org/b/c/R$styleable.class", actual)
            for name in expected:
                self.assertEqual(expected[name], actual[name], name)


def android_sdk():
    home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not home:
        return None
    aapt2s = sorted(Path(home).glob("build-tools/*/aapt2"))
    jars = sorted(Path(home).glob("platforms/android-*/android.jar"))
    return (aapt2s[-1], jars[-1]) if aapt2s and jars else None


@unittest.skipIf(jdk_home() is None or android_sdk() is None, "needs a JDK and an Android SDK (ANDROID_HOME)")
class RealAapt2CloneTest(unittest.TestCase):
    def test_link_matches_aapt2_extra_packages_and_javac(self):
        aapt2, android_jar = android_sdk()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            res = root / "res"
            (res / "values").mkdir(parents=True)
            (res / "values/values.xml").write_text(
                '<resources xmlns:android="http://schemas.android.com/apk/res/android">\n'
                '  <string name="app_name">App</string>\n  <color name="brand">#ff0000</color>\n'
                '  <attr name="size" format="dimension"/>\n'
                '  <declare-styleable name="Widget"><attr name="size"/><attr name="android:text"/></declare-styleable>\n'
                '  <style name="Theme.App" parent=""><item name="size">4dp</item></style>\n'
                '  <item type="id" name="root"/>\n</resources>\n'
            )
            compiled = root / "res.zip"
            subprocess.run([str(aapt2), "compile", "--dir", str(res), "-o", str(compiled)], check=True)
            manifest = root / "AndroidManifest.xml"
            manifest.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.app"/>')
            extras = ["com.example.app.sub", "org.b.c", "androidx.core"]
            # Before #6110: aapt2 writes a copy per package and javac compiles every one.
            gen = root / "gen"
            subprocess.run(
                [str(aapt2), "link", "-o", str(root / "reference.ap_"), "--manifest", str(manifest),
                 "-I", str(android_jar), "--auto-add-overlay", "--no-version-vectors", "--java", str(gen),
                 "--extra-packages", ":".join(extras), "-R", str(compiled)],
                check=True,
            )
            sources = {f: f.read_text() for f in gen.rglob("R.java")}
            self.assertEqual(4, len(sources))
            expected = javac_reference(
                root, ["com.example.app"] + extras,
                lambda package: sources[gen / package.replace(".", "/") / "R.java"],
            )
            assets = root / "assets.tsv"
            assets.write_text("")
            out = root / "out"
            out.mkdir()
            subprocess.run(
                ["sh", str(LINK), str(aapt2), str(jdk_home()), str(out / "resources.ap_"),
                 str(out / "r-final.jar"), str(manifest), str(android_jar), ":".join(extras), str(assets),
                 str(compiled)],
                check=True, capture_output=True, text=True,
            )
            actual = jar_classes(out / "r-final.jar")
            self.assertEqual(sorted(expected), sorted(actual))
            for name in expected:
                self.assertEqual(expected[name], actual[name], name)


if __name__ == "__main__":
    unittest.main()
