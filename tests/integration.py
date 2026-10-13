#!/usr/bin/env python3
"""Exercise CLI-first and native-first rendering in a fresh archive-consuming repo.

Uses only the Python standard library. The caller supplies a current CLI
installDist; published CLIs before the Bazel adapter will not work.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile


def invoke(argv, cwd, log, capture=False):
    with log.open("w") as stderr:
        result = subprocess.run(argv, cwd=cwd, text=True, stdout=subprocess.PIPE,
                                stderr=stderr, timeout=600)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {argv}\n{log.read_text()[-8000:]}")
    return result.stdout


def png(path):
    content = path.read_bytes()
    assert content[:8] == b"\x89PNG\r\n\x1a\n", f"Invalid PNG: {path}"
    width, height = struct.unpack(">II", content[16:24])
    assert width > 1 and height > 1, (path, width, height)
    return hashlib.sha256(content).hexdigest()


def check(args):
    source = args.fixture.resolve()
    rules = Path(__file__).resolve().parents[1]
    scripts = rules / "scripts"
    spec = importlib.util.spec_from_file_location("package_rules", scripts / "package.py")
    package_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package_module)
    args.evidence.mkdir(parents=True, exist_ok=True)
    evidence = args.evidence.resolve()
    with tempfile.TemporaryDirectory(prefix="compose-preview-external-") as temporary:
        temporary = Path(temporary)
        rules_archive = package_module.package(rules, temporary)
        cli_archive = temporary / "cli.tar.gz"
        with tarfile.open(cli_archive, "w:gz") as tar:
            tar.add(args.cli_dist.resolve(), arcname="compose-preview-test")
        consumer = temporary / "consumer"
        # Only tracked fixture inputs. Never copy output symlinks, generated tools or caches.
        consumer.mkdir()
        shutil.copytree(source / "app", consumer / "app")
        for name in ("BUILD.bazel", "MODULE.bazel", "maven_install.json", ".bazelversion", "compose_preview.bzl"):
            shutil.copy2(source / name, consumer / name)
        module = (consumer / "MODULE.bazel").read_text()
        module = re.sub(r'local_path_override\(module_name = "rules_compose_preview", path = "[^\"]+"\)',
                        'archive_override(module_name = "rules_compose_preview", urls = [%s], '
                        'sha256 = "%s", strip_prefix = "%s")' %
                        (json.dumps(rules_archive.as_uri()), hashlib.sha256(rules_archive.read_bytes()).hexdigest(), rules_archive.name.removesuffix(".tar.gz")), module)
        module += '\ncli_repository = use_repo_rule("@rules_compose_preview//:repositories.bzl", "compose_preview_cli_repository")\n'
        module += 'cli_repository(name = "compose_preview_cli", urls = [%s], sha256 = "%s", strip_prefix = "compose-preview-test")\n' % (
            json.dumps(cli_archive.as_uri()), hashlib.sha256(cli_archive.read_bytes()).hexdigest())
        (consumer / "MODULE.bazel").write_text(module)
        build = (consumer / "BUILD.bazel").read_text()
        build = re.sub(r'compose_preview_cli\(\s*name = "preview_cli",.*?\n\)',
                       'alias(name = "preview_cli", actual = "@compose_preview_cli//:cli")', build, flags=re.S)
        (consumer / "BUILD.bazel").write_text(build)
        config = consumer / ".compose-preview/bazel.json"
        config.parent.mkdir()
        startup = [f"--output_user_root={args.output_user_root.resolve()}"] if args.output_user_root else []
        config.write_text(json.dumps({"schemaVersion": 1, "target": "//:aspect_app",
                                     "buildCommand": [str(args.bazel)], "startupArgs": startup,
                                     "buildArgs": ["--jobs=4", "--remote_download_outputs=all"],
                                     "aspect": "@rules_compose_preview//:runtime.bzl%compose_preview_aspect"}))
        cli = str(args.cli_dist.resolve() / "bin/compose-preview")
        listed = json.loads(invoke([cli, "list", "--json"], consumer, evidence / "cli-list.log"))
        assert {p["functionName"] for p in listed["previews"]} == {"RuntimeGreeting", "GeneratedGreeting"}
        normal_png = evidence / "cli-first.png"
        invoke([cli, "render", "--filter", "RuntimeGreeting", "--output", str(normal_png)], consumer,
               evidence / "cli-render.log")
        bazel = [str(args.bazel)] + startup
        native_png = evidence / "bazel-run.png"
        response = json.loads(invoke(bazel + ["run", "//:ui-preview-run", "--jobs=4", "--",
                                             "--filter", "RuntimeGreeting", "--json", "--output", str(native_png)],
                                     consumer, evidence / "native-run.log"))
        assert len(response["previews"]) == 1 and response["previews"][0]["sha256"]
        assert png(normal_png) == png(native_png), "CLI-first and Bazel-run images differ"
        invoke(bazel + ["build", "//:ui-preview", "--jobs=4", "--action_env=JAVA_HOME", "--action_env=XDG_CACHE_HOME"],
               consumer, evidence / "native-build.log")
        tree = consumer / "bazel-bin/ui-preview/build/compose-previews"
        renders = [p for p in (tree / "renders").glob("*.png")
                   if re.fullmatch(r"(RuntimeGreeting|GeneratedGreeting)-[0-9a-f]+\.png", p.name)]
        assert len(renders) == 2, renders
        assert all(png(path) for path in renders)
        runtime = next(path for path in renders if path.name.startswith("RuntimeGreeting-"))
        assert png(runtime) == png(normal_png), "CLI-first and Bazel-build images differ"
        shutil.copy2(runtime, evidence / "bazel-build.png")
        assert not (consumer / ".compose-preview/bazel").exists(), "Native module should avoid CLI aspect injection"
        # Launcher works from another cwd, independently of BUILD_WORKSPACE_DIRECTORY.
        launcher = consumer / "bazel-bin/ui-preview-run.sh"
        invoke([str(launcher), "--filter", "RuntimeGreeting", "--output", str(evidence / "outside-cwd.png")],
               temporary, evidence / "outside-cwd.log")
        assert png(evidence / "outside-cwd.png") == png(normal_png)
        evidence.joinpath("summary.json").write_text(json.dumps({"cliFirst": "passed", "bazelRun": "passed",
                                                                "bazelBuild": "passed", "externalArchives": "passed",
                                                                "pngSha256": png(normal_png)}, indent=2) + "\n")
        print("CLI-first, Bazel run/build, external rules/CLI archives and outside-cwd rendering passed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--cli-dist", type=Path, required=True)
    parser.add_argument("--bazel", default="bazel")
    parser.add_argument("--output-user-root", type=Path)
    parser.add_argument("--evidence", type=Path, default=Path("build/bazel-native"))
    check(parser.parse_args())
