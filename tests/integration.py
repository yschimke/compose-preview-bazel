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


def execution_log(path):
    """The SpawnExec entries of an --execution_log_json_file (concatenated JSON objects)."""
    text, decoder, entries, index = path.read_text(), json.JSONDecoder(), [], 0
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            return entries
        entry, index = decoder.raw_decode(text, index)
        entries.append(entry)


def render_inputs(args):
    """The sidecar jars, Skiko native and fonts the CLI-first render above provisioned."""
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "composeai"
    roots = [args.cli_dist.resolve(), cache]

    def sidecar(name):
        for root in roots:
            for directory in [root / name, *sorted(root.rglob(name))]:
                jars = sorted(p for p in directory.glob("*.jar") if not p.name.startswith("skiko-awt-runtime-"))
                if directory.is_dir() and jars:
                    return jars
        raise AssertionError(f"no provisioned {name} under {roots}")

    skiko = sorted({p.resolve() for root in roots for p in root.rglob("skiko-awt-runtime-*.jar")})
    assert skiko, f"no provisioned Skiko native under {roots}"
    fonts = sorted(p for p in (cache / "fonts").rglob("*") if p.is_file()) if (cache / "fonts").is_dir() else []
    return {"desktop_daemon": sidecar("lib-daemon-desktop"), "renderer": sidecar("lib-renderer"),
            "skiko": skiko[:1], "fonts": fonts}


def check_render_toolchain(args, consumer, bazel, evidence, expected_png):
    inputs = render_inputs(args)
    package = consumer / "render"
    package.mkdir()
    labels = {}
    for kind, files in inputs.items():
        (package / kind).mkdir()
        labels[kind] = []
        for source in files:
            shutil.copyfile(source, package / kind / source.name)
            labels[kind].append(f"{kind}/{source.name}")
    (package / "BUILD.bazel").write_text(
        'load("@rules_compose_preview//:render_toolchain.bzl", "compose_preview_render_toolchain")\n'
        "compose_preview_render_toolchain(\n    name = \"inputs\",\n" +
        "".join(f"    {kind} = {json.dumps(files)},\n" for kind, files in labels.items()) +
        ")\n"
        "toolchain(\n    name = \"toolchain\",\n    toolchain = \":inputs\",\n"
        "    toolchain_type = \"@rules_compose_preview//:render_toolchain_type\",\n)\n")
    disk_cache = evidence.parent / "render-toolchain-disk-cache"
    shutil.rmtree(disk_cache, ignore_errors=True)
    flags = ["--extra_toolchains=//render:toolchain", "--incompatible_strict_action_env",
             f"--disk_cache={disk_cache}", "--output_groups=png", "--jobs=4"]
    first = evidence / "toolchain-render-actions.json"
    invoke(bazel + ["build", "//:ui-preview", *flags, f"--execution_log_json_file={first}"],
           consumer, evidence / "toolchain-render.log")
    executed = first.read_text()
    assert "ComposePreviewRender" in executed and "ComposePreviewLocalRender" not in executed
    renders = consumer / "bazel-bin/ui-preview.renders"
    runtime = next(p for p in renders.glob("*.png") if "RuntimeGreeting" in p.name)
    assert png(runtime) == png(expected_png), "Toolchain and CLI-first images differ"
    copy_evidence(runtime, evidence / "toolchain-render.png")
    invoke(bazel + ["clean"], consumer, evidence / "toolchain-clean.log")
    second = evidence / "toolchain-cached-actions.json"
    invoke(bazel + ["build", "//:ui-preview", *flags, f"--execution_log_json_file={second}"],
           consumer, evidence / "toolchain-cached.log")
    cached = execution_log(second)
    renders_run = [entry for entry in cached if entry.get("mnemonic") == "ComposePreviewRender"]
    assert renders_run and all(entry.get("cacheHit") for entry in renders_run), "Toolchain render must be served from the cache"
    shutil.rmtree(package)


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
            # CI's disk cache may already have omitted this intermediate source.
            generated.unlink(missing_ok=True)
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
            # A registered render toolchain declares every render input, so the render runs with a
            # strict environment and no network, and is cached like any other action.
            check_render_toolchain(args, consumer, bazel, evidence, normal_png)
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



def strip_rules_module(module):
    """MODULE.bazel without its own rules_compose_preview dependency and override, if any."""
    module = re.sub(r'bazel_dep\(name = "rules_compose_preview"[^)]*\)\n?', "", module)
    return re.sub(r'(?:local_path_override|git_override|archive_override)\(\s*module_name = "rules_compose_preview",.*?\)\n?',
                  "", module, flags=re.S)


def tool_repositories(cli_archive, producer_archive):
    """The use_repo_rule lines that import the CLI and producer test archives."""
    return (
        '\ncli_repository = use_repo_rule("@rules_compose_preview//:repositories.bzl", "compose_preview_cli_repository")\n'
        'cli_repository(name = "compose_preview_cli", urls = [%s], sha256 = "%s", strip_prefix = "compose-preview-test")\n' % (
            json.dumps(cli_archive.as_uri()), hashlib.sha256(cli_archive.read_bytes()).hexdigest()) +
        '\nproducer_repository = use_repo_rule("@rules_compose_preview//:repositories.bzl", "compose_preview_producer_repository")\n'
        'producer_repository(name = "compose_preview_producer", urls = [%s], sha256 = "%s", strip_prefix = "producer-test")\n' % (
            json.dumps(producer_archive.as_uri()), hashlib.sha256(producer_archive.read_bytes()).hexdigest()))


ANDROID_PREVIEW = """
compose_preview(
    name = "android-preview",
    cli = "@compose_preview_cli//:cli",
    filter = "Greeting",
    maven_install = ":maven_install.json",
    module = ":%s",
    outputs = ["png"],
    producer = "@compose_preview_producer//:producer",
    srcs = glob(["app/src/main/kotlin/**/*.kt"]),
)
"""


def check_android(args):
    """An Android library through compose_preview: a cacheable, deterministic render.

    The fixture is compose-ai-contrib's bazel-apk (a rules_kotlin kt_android_library whose
    Greeting preview reads string, colour and drawable resources). The render toolchain declares
    the Android daemon and android-all jars `compose-preview provision --android` fetched, so the
    render runs with the network off and is cached; a second execution from an empty cache must
    produce the same bytes.
    """
    source = args.android_fixture.resolve()
    rules = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("package_rules", rules / "scripts" / "package.py")
    package_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package_module)
    evidence = (args.evidence / "android").resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="compose-preview-android-") as temporary:
        temporary = Path(temporary)
        rules_archive = package_module.package(rules, temporary)
        cli_archive = temporary / "cli.tar.gz"
        with tarfile.open(cli_archive, "w:gz") as tar:
            tar.add(args.cli_dist.resolve(), arcname="compose-preview-test")
        producer_archive = temporary / "producer.tar.gz"
        with tarfile.open(producer_archive, "w:gz") as tar:
            tar.add(args.producer_dist.resolve(), arcname="producer-test")
        consumer = temporary / "consumer"
        consumer.mkdir()
        startup = [f"--output_user_root={args.output_user_root.resolve()}"] if args.output_user_root else []
        bazel = [str(args.bazel)] + startup
        try:
            shutil.copytree(source / "app", consumer / "app")
            for name in ("BUILD.bazel", "MODULE.bazel", "maven_install.json", ".bazelversion", ".bazelrc", "compose_preview.bzl"):
                if (source / name).is_file():
                    shutil.copy2(source / name, consumer / name)
            version = re.search(r'version\s*=\s*"([^"]+)"', (rules / "MODULE.bazel").read_text())[1]
            module = strip_rules_module((consumer / "MODULE.bazel").read_text())
            module += '\nbazel_dep(name = "rules_compose_preview", version = "%s")\n' % version
            module += 'archive_override(module_name = "rules_compose_preview", urls = [%s], sha256 = "%s", strip_prefix = "%s")\n' % (
                json.dumps(rules_archive.as_uri()), hashlib.sha256(rules_archive.read_bytes()).hexdigest(),
                rules_archive.name.removesuffix(".tar.gz"))
            module += tool_repositories(cli_archive, producer_archive)
            (consumer / "MODULE.bazel").write_text(module)
            library = re.search(r'kt_android_library\(\s*name = "([^"]+)"', (consumer / "BUILD.bazel").read_text())[1]
            build = 'load("@rules_compose_preview//:defs.bzl", "compose_preview")\n' + (consumer / "BUILD.bazel").read_text()
            (consumer / "BUILD.bazel").write_text(build + ANDROID_PREVIEW % library)

            # The Android render's inputs, as `provision` fetches them for an offline machine.
            environment = dict(os.environ, XDG_CACHE_HOME=str(temporary / "xdg"))
            report = json.loads(invoke([str(args.cli_dist.resolve() / "bin/compose-preview"), "provision", "--android",
                                        "--android-sdk", str(args.android_sdk), "--json"],
                                       temporary, evidence / "provision.log", env=environment))
            items = {item["name"]: Path(item["path"]) for item in report["items"] if item["ok"]}
            daemon = sorted(items["android daemon"].glob("*.jar"))
            android_all = [items[f"android-all SDK {args.android_sdk}"]]
            fonts = sorted(p for p in (Path.home() / ".cache/composeai/fonts").glob("*") if p.is_file())
            package = consumer / "render"
            labels = {}
            for kind, files in (("android_daemon", daemon), ("android_all", android_all), ("fonts", fonts)):
                (package / kind).mkdir(parents=True)
                labels[kind] = []
                for path in files:
                    shutil.copyfile(path, package / kind / path.name)
                    labels[kind].append(f"{kind}/{path.name}")
            (package / "BUILD.bazel").write_text(
                'load("@rules_compose_preview//:render_toolchain.bzl", "compose_preview_render_toolchain")\n'
                "compose_preview_render_toolchain(\n    name = \"inputs\",\n" +
                "".join(f"    {kind} = {json.dumps(files)},\n" for kind, files in labels.items()) +
                # SDK 36 renders need Java 21 (on 17 the CLI clamps to 35).
                '    java_runtime = "@rules_java//toolchains:remotejdk_21",\n)\n'
                "toolchain(\n    name = \"toolchain\",\n    toolchain = \":inputs\",\n"
                "    toolchain_type = \"@rules_compose_preview//:render_toolchain_type\",\n)\n")

            def build_render(name, disk_cache):
                log = evidence / f"{name}-actions.json"
                flags = ["--extra_toolchains=//render:toolchain", "--incompatible_strict_action_env",
                         f"--disk_cache={disk_cache}", "--output_groups=png,rendered_bundle", "--jobs=4"]
                if os.environ.get("ANDROID_HOME"):
                    flags.append(f"--repo_env=ANDROID_HOME={os.environ['ANDROID_HOME']}")
                invoke(bazel + ["build", "//:android-preview", *flags, f"--execution_log_json_file={log}"],
                       consumer, evidence / f"{name}.log")
                renders = execution_log(log)
                ran = [e for e in renders if e.get("mnemonic") in ("ComposePreviewRender", "ComposePreviewLocalRender")]
                assert ran and all(e["mnemonic"] == "ComposePreviewRender" for e in ran), \
                    "An Android render with the Android toolchain inputs must be the cacheable action"
                pngs = sorted((consumer / "bazel-bin/android-preview.renders").glob("*.png"))
                assert pngs, "The Android render produced no PNG"
                return ran, {p.name: png(p) for p in pngs}, pngs

            first_cache, second_cache = temporary / "disk-cache-1", temporary / "disk-cache-2"
            ran, first, pngs = build_render("android-render", first_cache)
            assert not any(e.get("cacheHit") for e in ran), "The first Android render must execute"
            greeting = next(p for p in pngs if "Greeting" in p.name)
            copy_evidence(greeting, evidence / "android-render.png")
            if args.android_check:
                invoke(["python3", str(args.android_check.resolve()), str(evidence / "android-render.png")],
                       temporary, evidence / "android-check.log")
            with zipfile.ZipFile(consumer / "bazel-bin/android-preview.rendered-bundle.png") as baked:
                manifest = json.loads(baked.read("bundle.json"))
                assert manifest["backend"] == "android", manifest["backend"]
                assert "android/resources.ap_" in baked.namelist()
                assert any(n.startswith("previews/") and n.endswith(".png") for n in baked.namelist())
            # Determinism: a second execution, from an empty cache, gives the same bytes.
            invoke(bazel + ["clean"], consumer, evidence / "android-clean-1.log")
            ran, second, _ = build_render("android-rerender", second_cache)
            assert not any(e.get("cacheHit") for e in ran), "The re-render must execute, not hit a cache"
            assert first == second, f"Android renders differ between executions: {first} != {second}"
            # Cacheability: a rebuild against the first cache is served from it.
            invoke(bazel + ["clean"], consumer, evidence / "android-clean-2.log")
            ran, third, _ = build_render("android-cached", first_cache)
            assert all(e.get("cacheHit") for e in ran), "The Android render must be served from the cache"
            assert third == first
            evidence.joinpath("summary.json").write_text(json.dumps(
                {"androidRender": "passed", "deterministic": True, "cached": True, "pngSha256": first}, indent=2) + "\n")
            print("Android compose_preview render: cacheable, byte-identical across executions, served from cache.")
        finally:
            subprocess.run(bazel + ["shutdown"], cwd=consumer, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=60, check=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, help="compose-ai-contrib's bazel-desktop")
    parser.add_argument("--android-fixture", type=Path, help="compose-ai-contrib's bazel-apk")
    parser.add_argument("--android-sdk", type=int, default=36)
    parser.add_argument("--android-check", type=Path, help="a script that checks the Android render's pixels")
    parser.add_argument("--cli-dist", type=Path, required=True)
    parser.add_argument("--producer-dist", type=Path, required=True)
    parser.add_argument("--bazel", default="bazel")
    parser.add_argument("--output-user-root", type=Path)
    parser.add_argument("--evidence", type=Path, default=Path("build/bazel-native"))
    arguments = parser.parse_args()
    if not arguments.fixture and not arguments.android_fixture:
        parser.error("give --fixture, --android-fixture or both")
    if arguments.fixture:
        check(arguments)
    if arguments.android_fixture:
        check_android(arguments)
