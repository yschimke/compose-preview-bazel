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
import zipfile


def invoke(argv, cwd, log, capture=False, env=None):
    with log.open("w") as stderr:
        result = subprocess.run(argv, cwd=cwd, text=True, stdout=subprocess.PIPE,
                                stderr=stderr, timeout=600, env=env)
    log.with_suffix(".stdout").write_text(result.stdout)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {argv}\n{log.read_text()[-8000:]}")
    return result.stdout


def png(path):
    content = path.read_bytes()
    assert content[:8] == b"\x89PNG\r\n\x1a\n", f"Invalid PNG: {path}"
    width, height = struct.unpack(">II", content[16:24])
    assert width > 1 and height > 1, (path, width, height)
    return hashlib.sha256(content).hexdigest()


def copy_evidence(source, destination):
    # Bazel marks outputs read-only; do not preserve that mode on repeatable evidence files.
    destination.unlink(missing_ok=True)
    shutil.copyfile(source, destination)


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
        producer_archive = temporary / "producer.tar.gz"
        with tarfile.open(producer_archive, "w:gz") as tar:
            tar.add(args.producer_dist.resolve(), arcname="producer-test")
        consumer = temporary / "consumer"
        # Only tracked fixture inputs. Never copy output symlinks, generated tools or caches.
        consumer.mkdir()
        try:
            shutil.copytree(source / "app", consumer / "app")
            for name in ("BUILD.bazel", "MODULE.bazel", "maven_install.json", ".bazelversion", "compose_preview.bzl"):
                shutil.copy2(source / name, consumer / name)
            module = (consumer / "MODULE.bazel").read_text()
            version = re.search(r'version\s*=\s*"([^"]+)"', (rules / "MODULE.bazel").read_text())[1]
            module = re.sub(r'(bazel_dep\(name = "rules_compose_preview", version = ")[^"]+', lambda match: match[1] + version, module)
            module = re.sub(r'(?:local_path_override|git_override)\(\s*module_name = "rules_compose_preview",.*?\)',
                            'archive_override(module_name = "rules_compose_preview", urls = [%s], '
                            'sha256 = "%s", strip_prefix = "%s")' %
                            (json.dumps(rules_archive.as_uri()), hashlib.sha256(rules_archive.read_bytes()).hexdigest(), rules_archive.name.removesuffix(".tar.gz")), module, flags=re.S)
            module += '\ncli_repository = use_repo_rule("@rules_compose_preview//:repositories.bzl", "compose_preview_cli_repository")\n'
            module += 'cli_repository(name = "compose_preview_cli", urls = [%s], sha256 = "%s", strip_prefix = "compose-preview-test")\n' % (
                json.dumps(cli_archive.as_uri()), hashlib.sha256(cli_archive.read_bytes()).hexdigest())
            module += '\nproducer_repository = use_repo_rule("@rules_compose_preview//:repositories.bzl", "compose_preview_producer_repository")\n'
            module += 'producer_repository(name = "compose_preview_producer", urls = [%s], sha256 = "%s", strip_prefix = "producer-test")\n' % (
                json.dumps(producer_archive.as_uri()), hashlib.sha256(producer_archive.read_bytes()).hexdigest())
            (consumer / "MODULE.bazel").write_text(module)
            build = (consumer / "BUILD.bazel").read_text()
            build = re.sub(r'compose_preview_cli\(\s*name = "preview_cli",.*?\n\)',
                           'alias(name = "preview_cli", actual = "@compose_preview_cli//:cli")', build, flags=re.S)
            # Normalize the fixture while migrating the old macro API.
            build = re.sub(r"\s*bundle = True,", "", build)
            build = re.sub(r"\s*formats = \[\"png\"\],", "", build)
            build = re.sub(r'\s*outputs = \[.*?\],', '', build, flags=re.S)
            build = build.replace('name = "ui-preview",', 'name = "ui-preview",\n    outputs = ["bundle"],')
            if 'maven_install = ":maven_install.json"' not in build:
                build = build.replace('name = "ui-preview",', 'name = "ui-preview",\n    maven_install = ":maven_install.json",')
            def producer_attribute(match):
                block = match[0]
                if 'producer = ' in block:
                    return re.sub(r'producer = "[^"]+"', 'producer = "@compose_preview_producer//:producer"', block)
                if 'producer = ' not in block:
                    block = block.replace('name = "ui-preview",', 'name = "ui-preview",\n    producer = "@compose_preview_producer//:producer",')
                return block
            build = re.sub(r'compose_preview\(.*?\n\)', producer_attribute, build, flags=re.S)
            maven_repo = os.environ.get("COMPOSE_PREVIEW_BAZEL_MAVEN_REPO")
            if maven_repo:
                build = build.replace('name = "ui-preview",', 'name = "ui-preview",\n    env = {"JAVA_TOOL_OPTIONS": %s},' % json.dumps("-Dmaven.repo.local=" + maven_repo + " -Duser.home=" + str(evidence / "renderer-home")))
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
            response = json.loads(invoke(bazel + ["run", "//:ui-preview", "--jobs=4", "--execution_log_json_file=" + str(evidence / "preparation-actions.json"), "--action_env=JAVA_HOME", "--action_env=XDG_CACHE_HOME", "--",
                                                 "render", "--filter", "RuntimeGreeting", "--json", "--output", str(native_png)],
                                         consumer, evidence / "native-run.log"))
            assert len(response["previews"]) == 1 and response["previews"][0]["sha256"]
            assert png(normal_png) == png(native_png), "CLI-first and Bazel-run images differ"
            invoke(bazel + ["build", "//:ui-preview", "--output_groups=png", "--jobs=4", "--action_env=JAVA_HOME", "--action_env=XDG_CACHE_HOME"],
                   consumer, evidence / "native-build.log")
            tree = consumer / "bazel-bin/ui-preview.renders"
            renders = [p for p in tree.glob("*.png")
                       if re.fullmatch(r".*(RuntimeGreeting|GeneratedGreeting).*\.png", p.name)]
            assert len(renders) == 2, renders
            assert all(png(path) for path in renders)
            runtime = next(path for path in renders if "RuntimeGreeting" in path.name)
            assert png(runtime) == png(normal_png), "CLI-first and Bazel-build images differ"
            copy_evidence(runtime, evidence / "bazel-build.png")
            assert not (consumer / ".compose-preview/bazel").exists(), "Native module should avoid CLI aspect injection"
            invoke(bazel + ["build", "//:ui-preview", "--output_groups=bundle", "--jobs=4", "--action_env=JAVA_HOME", "--action_env=XDG_CACHE_HOME"],
                   consumer, evidence / "native-bundle.log")
            bundle = consumer / "bazel-bin/ui-preview.bundle.png"
            assert bundle.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
            with zipfile.ZipFile(bundle) as archive:
                manifest = json.loads(archive.read("bundle.json"))
                assert "/tmp/compose-preview-external" not in archive.read("previews.json").decode(), "Bundle must not encode its checkout root"
                assert manifest["producer"] == "bazel"
                coordinates = [entry for entry in manifest["classpath"] if entry["kind"] == "maven"]
                assert any(entry["artifact"] == "ui-desktop" for entry in coordinates)
                assert any(entry["artifact"] == "runtime-desktop" for entry in coordinates)
                assert len(coordinates) > 20, "Published framework dependencies should use Maven coordinates"
                assert "classes/app.jar" in archive.namelist()
                assert not any(name.startswith("previews/") and name.endswith(".png") for name in archive.namelist()), "Prepared bundle must be renderer-free"
                assert "dependency-resolution.json" in archive.namelist()
            copy_evidence(bundle, evidence / "runnable-bundle.png")
            # Launcher works from another cwd, independently of BUILD_WORKSPACE_DIRECTORY.
            launcher = consumer / "bazel-bin/ui-preview.sh"
            invoke([str(launcher), "--filter", "RuntimeGreeting", "--output", str(evidence / "outside-cwd.png")],
                   temporary, evidence / "outside-cwd.log")
            assert png(evidence / "outside-cwd.png") == png(normal_png)

            labels = invoke(bazel + ["query", "//:all", "--output=label"], consumer, evidence / "labels.log").splitlines()
            assert "//:ui-preview" in labels
            assert "//:ui-preview-run" not in labels and "//:ui-preview-bundle" not in labels
            graph = json.loads(invoke(bazel + ["aquery", "//:ui-preview", "--output_groups=bundle", "--output=jsonproto"], consumer, evidence / "actions.log"))
            actions = graph.get("actions", [])
            # aquery lists registered actions, including optional unrequested products. Execution
            # logs prove that bundle/run prerequisites did not execute either render or bake.
            executed = (evidence / "preparation-actions.json").read_text()
            assert "ComposePreviewLocalRender" not in executed and "ComposePreviewBake" not in executed
            assert sum(a.get("mnemonic") == "ComposePreviewPrepare" for a in actions) == 1
            before = hashlib.sha256(bundle.read_bytes()).hexdigest()
            invoke(bazel + ["build", "//:ui-preview", "--output_groups=bundle", "--jobs=4", "--execution_log_json_file=" + str(evidence / "cached-actions.json")], consumer, evidence / "cached-bundle.log")
            assert "ComposePreviewPrepare" not in (evidence / "cached-actions.json").read_text(), "Unchanged preparation must be cached"
            assert hashlib.sha256(bundle.read_bytes()).hexdigest() == before
            # Re-run the standalone producer against byte-identical inputs under distinct roots
            # and JVM locales/timezones. This is a fresh execution, not a Bazel cache observation.
            # Reproduce an evicted intermediate output even on a fully materialized local cache.
            generated = consumer / "bazel-bin/GeneratedGreeting.kt"
            assert generated.is_file()
            generated.unlink()
            # A cached top-level bundle does not require Bazel to retain/materialize intermediate
            # sources or runtime JARs. Request the contract's raw input group explicitly.
            invoke(bazel + ["build", "//:ui-preview", "--output_groups=compose_preview_metadata,compose_preview_runtime",
                            "--remote_download_outputs=all", "--jobs=4"], consumer, evidence / "materialize-inputs.log")
            assert generated.is_file(), "The raw input group must rematerialize generated sources"
            execution_root = Path(invoke(bazel + ["info", "execution_root"], consumer, evidence / "execution-root.log").strip())
            metadata = json.loads((consumer / "bazel-bin/ui-preview.artifacts.json").read_text())
            produced = []
            for index, (timezone, language) in enumerate((("Pacific/Honolulu", "en"), ("Pacific/Kiritimati", "fr"))):
                relocated = temporary / f"relocated-{index}"
                relocated.mkdir()
                paths = set(path for field in ("projectJars", "runtimeJars", "sourceFiles", "dataFiles") for path in metadata.get(field, []))
                for path in paths:
                    assert not Path(path).is_absolute() and ".." not in Path(path).parts
                    destination = relocated / path
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(execution_root / path, destination)
                (relocated / "artifacts.json").write_text(json.dumps(metadata))
                shutil.copyfile(consumer / "maven_install.json", relocated / "maven_install.json")
                output = relocated / "bundle.png"
                environment = dict(os.environ)
                environment["JAVA_OPTS"] = f"-Duser.timezone={timezone} -Duser.language={language} -Dfile.encoding=US-ASCII"
                invoke([str(args.producer_dist.resolve() / "bin/compose-preview-producer"), "--artifacts", "artifacts.json",
                        "--maven-install", "maven_install.json", "--output", str(output)], relocated,
                       evidence / f"reproducibility-{index}.log", env=environment)
                produced.append(hashlib.sha256(output.read_bytes()).hexdigest())
            assert produced == [before, before], "Prepared artifacts must reproduce across checkouts/locales/timezones"
            # Invalidate a runtime-only resource. Preparation must rerun even when preview source is unchanged.
            resource = consumer / "app/runtime/runtime-message.txt"
            if not resource.is_file():
                resource = next((consumer / "app").rglob("runtime-message.txt"))
            original = resource.read_bytes()
            resource.write_bytes(original + b"\nchanged input\n")
            invoke(bazel + ["build", "//:ui-preview", "--output_groups=bundle", "--jobs=4"], consumer, evidence / "invalidated-bundle.log")
            assert hashlib.sha256(bundle.read_bytes()).hexdigest() != before
            resource.write_bytes(original)
            invoke(bazel + ["build", "//:ui-preview", "--output_groups=rendered_bundle", "--jobs=4", "--action_env=JAVA_HOME", "--action_env=XDG_CACHE_HOME"], consumer, evidence / "baked-bundle.log")
            baked = consumer / "bazel-bin/ui-preview.rendered-bundle.png"
            with zipfile.ZipFile(baked) as archive:
                assert len([name for name in archive.namelist() if name.startswith("previews/") and name.endswith(".png")]) == 2
            copy_evidence(baked, evidence / "rendered-bundle.png")
            # Source-only mode has no CLI repository or target, and exposes no render actions.
            source_module = re.sub(r'\ncli_repository = use_repo_rule\(.*?strip_prefix = "compose-preview-test"\)\n', '', module, flags=re.S)
            (consumer / "MODULE.bazel").write_text(source_module)
            source_build = re.sub(r'alias\(name = "preview_cli", actual = "@compose_preview_cli//:cli"\)', '', build)
            source_build = re.sub(r'\s*cli = ":preview_cli",', '', source_build)
            (consumer / "BUILD.bazel").write_text(source_build)
            invoke(bazel + ["build", "//:ui-preview", "--jobs=4"], consumer, evidence / "source-only.log")
            source_graph = json.loads(invoke(bazel + ["aquery", "//:ui-preview", "--output=jsonproto"], consumer, evidence / "source-only-actions.log"))
            assert not any(a.get("mnemonic") in ("ComposePreviewLocalRender", "ComposePreviewBake") for a in source_graph.get("actions", []))
            assert "compose_preview_cli" not in json.dumps(source_graph), "Source-only preparation must have no CLI inputs"
            forbidden = ("daemon-client-", "daemon-core-", "daemon-bta-", "kotlin-build-tools-api-", "skiko-", "ktor-")
            assert not any(jar.name.startswith(forbidden) for jar in (args.producer_dist / "lib").glob("*.jar"))
            evidence.joinpath("summary.json").write_text(json.dumps({"cliFirst": "passed", "bazelRun": "passed",
                                                                    "bazelBuild": "passed", "externalArchives": "passed", "sourceOnly": "passed", "preparationCache": "passed", "reproduciblePreparation": "passed",
                                                                    "pngSha256": png(normal_png)}, indent=2) + "\n")
            print("CLI-first, Bazel run/build, external rules/CLI archives and outside-cwd rendering passed.")
        finally:
            shutdown = [str(args.bazel)]
            if args.output_user_root:
                shutdown.append(f"--output_user_root={args.output_user_root.resolve()}")
            try:
                subprocess.run(shutdown + ["shutdown"], cwd=consumer, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=30, check=False)
            except subprocess.TimeoutExpired:
                pass



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--cli-dist", type=Path, required=True)
    parser.add_argument("--producer-dist", type=Path, required=True)
    parser.add_argument("--bazel", default="bazel")
    parser.add_argument("--output-user-root", type=Path)
    parser.add_argument("--evidence", type=Path, default=Path("build/bazel-native"))
    check(parser.parse_args())
