"""Offline cached preparation plus optional rendering through compose-preview, for Desktop (JVM)
and Android library targets."""

load(":android.bzl", "ComposePreviewAndroidInfo", "compose_preview_android_module_aspect")
load(":java_info.bzl", "JavaInfo")
load(":launcher.bzl", "RUNFILES_INIT", "RUNFILES_LIBRARY", "rlocation_path")
load(":render_toolchain.bzl", "RENDER_TOOLCHAIN_TYPE", "render_inputs", "renders_android")
load(":runtime.bzl", "compose_preview_metadata_args")

# `bazel run` launchers; str.format templates, so a literal shell brace is doubled. rlocation is
# given the launcher's own repository, which applies the repository mapping without the runfiles
# library having to work out where the script sits.
_RENDER_LAUNCHER = """\
#!/usr/bin/env bash
{init}
set -euo pipefail
runfiles_export_envvars
resolve() {{
  local path
  if ! path="$(rlocation "$1" '{repo}')" || [[ -z "$path" ]]; then
    echo "compose-preview: $1 is missing from the runfiles" >&2
    exit 1
  fi
  [[ "$path" == /* ]] || path="$PWD/$path"
  printf '%s' "$path"
}}
cli="$(resolve '{cli}')"
bundle="$(resolve '{bundle}')"
operation=render
if [[ "${{1:-}}" == render || "${{1:-}}" == inspect || "${{1:-}}" == extract ]]; then
  operation="$1"
  shift
fi
if [[ "$operation" == render ]]; then
  out="${{COMPOSE_PREVIEW_OUT:-${{BUILD_WORKSPACE_DIRECTORY:-$PWD}}/.compose-preview/outputs/{name}}}"
  extra=(--output "$out")
  for arg in "$@"; do
    if [[ "$arg" == --output || "$arg" == -o ]]; then
      extra=()
      break
    fi
  done
  exec "$cli" bundle render "$bundle" ${{extra[@]+"${{extra[@]}}"}} "$@"
fi
exec "$cli" bundle "$operation" "$bundle" "$@"
"""

_SOURCE_ONLY_LAUNCHER = """\
#!/usr/bin/env bash
{init}
set -euo pipefail
runfiles_export_envvars
if [[ $# != 0 ]]; then
  echo 'Source-only target prepares bundles; rendering requires an optional cli attribute.' >&2
  exit 64
fi
rlocation '{bundle}' '{repo}'
"""

def _android_metadata(ctx, info):
    # Artifacts schema 2, from the module aspect's link: the producer then writes a
    # backend="android" bundle carrying the linked resources.
    files = depset(
        ctx.files.srcs + ctx.files.maven_install + info.data_files,
        transitive = [info.runtime, info.files],
    )
    manifest = ctx.actions.declare_file(ctx.label.name + ".artifacts.json")
    ctx.actions.write(manifest, compose_preview_metadata_args(
        ctx,
        schema_version = 2,
        target = ctx.attr.module.label,
        project_jars = info.project_jars,
        runtime = info.runtime,
        source_files = ctx.files.srcs,
        data_files = info.data_files,
        extra = {"android": info.android},
    ))
    return manifest, files

def _clock_packages(ctx, info):
    # `pinAppClock`: the target's own classes read the pinned preview clock, as a Gradle module's
    # namespace does. The CLI drops any prefix it must not instrument (the JDK, the framework).
    if not ctx.attr.pin_app_clock:
        return []
    packages = ctx.attr.app_clock_packages or ([info.package + "."] if info.package else [])
    return [p for p in packages if p.strip().strip(".")]

def _metadata(ctx):
    if ComposePreviewAndroidInfo in ctx.attr.module:
        return _android_metadata(ctx, ctx.attr.module[ComposePreviewAndroidInfo])
    java = ctx.attr.module[JavaInfo]
    project = java.runtime_output_jars
    if not project:
        fail("module must provide its own runtime output jars")
    runtime = depset(project, transitive = [java.transitive_runtime_jars])
    data = ctx.attr.module[DefaultInfo].default_runfiles
    files = depset(ctx.files.srcs + ctx.files.maven_install, transitive = [runtime, data.files])
    manifest = ctx.actions.declare_file(ctx.label.name + ".artifacts.json")

    # Written from the depsets when the action runs; analysis flattens nothing. Unlike the
    # aspect's, these dataFiles keep the runtime jars, so no set difference is needed.
    ctx.actions.write(manifest, compose_preview_metadata_args(
        ctx,
        schema_version = 1,
        target = ctx.attr.module.label,
        project_jars = project,
        runtime = runtime,
        source_files = ctx.files.srcs,
        data_files = data.files,
    ))
    return manifest, files

def _impl(ctx):
    if not ctx.attr.outputs or len(depset(ctx.attr.outputs).to_list()) != len(ctx.attr.outputs):
        fail("outputs must be a nonempty list of distinct output kinds")
    if any([kind not in ["png", "bundle", "rendered_bundle"] for kind in ctx.attr.outputs]):
        fail("supported outputs are bundle, png and rendered_bundle")
    if not ctx.executable.cli and any([kind != "bundle" for kind in ctx.attr.outputs]):
        fail("png and rendered_bundle require cli; omit cli for source-bundle-only support")
    metadata, files = _metadata(ctx)
    bundle = ctx.actions.declare_file(ctx.label.name + ".bundle.png")
    args = ctx.actions.args()
    args.add_all(["--artifacts", metadata.path, "--output", bundle.path])
    if ctx.file.maven_install:
        args.add_all(["--maven-install", ctx.file.maven_install.path])
    if ctx.attr.filter:
        args.add_all(["--filter", ctx.attr.filter])
    ctx.actions.run(
        executable = ctx.executable.producer,
        tools = [ctx.attr.producer[DefaultInfo].files_to_run],
        inputs = depset([metadata], transitive = [files, ctx.attr.producer[DefaultInfo].files]),
        outputs = [bundle],
        arguments = [args],
        mnemonic = "ComposePreviewPrepare",
        progress_message = "Preparing offline runnable previews for %{label}",
    )
    output = None
    rendered = None
    android = ctx.attr.module[ComposePreviewAndroidInfo] if ComposePreviewAndroidInfo in ctx.attr.module else None
    if ctx.executable.cli:
        output = ctx.actions.declare_directory(ctx.label.name + ".renders")
        render_args = ctx.actions.args()
        render_args.add_all([ctx.executable.cli.path, "bundle", "render", bundle.path, "--output", output.path])
        render_direct = [bundle]
        kind = "Desktop"
        if android:
            # The SDK the target links against, its android.jar and its app clock, each passed
            # explicitly so the render reads nothing of the host's SDK.
            kind = "Android"
            render_args.add_all(["--android-jar", android.android_jar])
            render_direct.append(android.android_jar)
            if android.compile_sdk:
                render_args.add_all(["--android-sdk", str(android.compile_sdk)])
            clock = _clock_packages(ctx, android)
            if clock:
                render_args.add_joined("--clock-packages", clock, join_with = ",")
        toolchain = ctx.toolchains[RENDER_TOOLCHAIN_TYPE]
        if toolchain and android and not renders_android(toolchain.compose_preview_render):
            # A toolchain without the Android inputs cannot stage an Android render.
            toolchain = None
        if toolchain:
            # Every input declared: the render reads only staged files, with the network off, so
            # it can be cached and run remotely like any other action.
            runtime_jars = android.runtime if android else ctx.attr.module[JavaInfo].transitive_runtime_jars
            jars = ctx.actions.declare_file(ctx.label.name + ".render-jars.txt")
            jar_list = ctx.actions.args()
            jar_list.add_all(runtime_jars)
            ctx.actions.write(jars, jar_list)

            # The bundle records, per runtime jar, the coordinate the producer identified and the
            # jar's content hash; the CLI resolves each such coordinate to the very jar with that
            # hash in this list, so no coordinate depends on how stage_maven.sh lays out a guess.
            # A CLI that predates the flag warns and ignores it, and falls back to the staged
            # repository below.
            render_args.add_all(["--local-jars", jars])
            declared, setup = render_inputs(
                toolchain.compose_preview_render,
                jars,
                ctx.file._stage_maven,
                ctx.file.maven_install,
                android = bool(android),
            )
            ctx.actions.run_shell(
                # Not `exec`: the shell stays to run setup's EXIT trap, which removes the stage.
                command = "\n".join(["set -eu"] + setup + ['"$@" >&2']),
                tools = [ctx.attr.cli[DefaultInfo].files_to_run],
                inputs = depset(
                    render_direct,
                    transitive = [declared, runtime_jars],
                ),
                outputs = [output],
                arguments = [render_args],
                env = ctx.attr.env,
                toolchain = RENDER_TOOLCHAIN_TYPE,
                mnemonic = "ComposePreviewRender",
                progress_message = "Rendering prepared %s previews for %%{label}" % kind,
            )
        else:
            # No render toolchain: the CLI provisions its inputs into the host cache at run time,
            # so the action can be neither cached nor run remotely.
            ctx.actions.run_shell(
                command = 'exec "$@" >&2',
                tools = [ctx.attr.cli[DefaultInfo].files_to_run],
                inputs = render_direct,
                outputs = [output],
                arguments = [render_args],
                env = ctx.attr.env,
                use_default_shell_env = True,
                execution_requirements = {"local": "1", "no-cache": "1", "no-remote": "1"},
                mnemonic = "ComposePreviewLocalRender",
                progress_message = "Rendering prepared %s previews for %%{label}" % kind,
            )
        rendered = ctx.actions.declare_file(ctx.label.name + ".rendered-bundle.png")
        bake_args = ctx.actions.args()
        bake_args.add_all(["--bundle", bundle.path, "--renders", output.path, "--output", rendered.path])
        ctx.actions.run(
            executable = ctx.executable.producer,
            tools = [ctx.attr.producer[DefaultInfo].files_to_run],
            inputs = depset([bundle, output], transitive = [ctx.attr.producer[DefaultInfo].files]),
            outputs = [rendered],
            arguments = [bake_args],
            mnemonic = "ComposePreviewBake",
        )
    launcher = ctx.actions.declare_file(ctx.label.name + ".sh")
    runfiles = ctx.runfiles(files = [bundle]).merge(ctx.attr._runfiles_library[DefaultInfo].default_runfiles)
    if ctx.executable.cli:
        ctx.actions.write(launcher, _RENDER_LAUNCHER.format(
            init = RUNFILES_INIT,
            cli = rlocation_path(ctx, ctx.executable.cli),
            bundle = rlocation_path(ctx, bundle),
            name = ctx.label.name,
            repo = ctx.label.repo_name,
        ), is_executable = True)
        runfiles = runfiles.merge_all([
            ctx.runfiles(files = [ctx.executable.cli]),
            ctx.attr.cli[DefaultInfo].default_runfiles,
        ])
    else:
        ctx.actions.write(launcher, _SOURCE_ONLY_LAUNCHER.format(
            init = RUNFILES_INIT,
            bundle = rlocation_path(ctx, bundle),
            repo = ctx.label.repo_name,
        ), is_executable = True)
    groups = {"bundle": depset([bundle]), "png": depset([output] if output else []), "rendered_bundle": depset([rendered] if rendered else [])}
    defaults = depset(transitive = [groups[kind] for kind in ctx.attr.outputs])
    return [
        DefaultInfo(executable = launcher, files = defaults, runfiles = runfiles),
        OutputGroupInfo(
            bundle = groups["bundle"],
            png = groups["png"],
            rendered_bundle = groups["rendered_bundle"],
            compose_preview_metadata = depset([metadata]),
            compose_preview_runtime = files,
        ),
    ]

compose_preview = rule(
    implementation = _impl,
    executable = True,
    attrs = {
        "maven_install": attr.label(allow_single_file = [".json"]),
        "_stage_maven": attr.label(default = Label(":stage_maven.sh"), allow_single_file = True),
        "_runfiles_library": attr.label(default = RUNFILES_LIBRARY),
        "module": attr.label(
            mandatory = True,
            providers = [JavaInfo],
            # An Android library (android_library, kt_android_library) is linked by this aspect and
            # prepared as a backend="android" bundle; any other JVM target is untouched by it.
            aspects = [compose_preview_android_module_aspect],
            doc = "The JVM library (Desktop) or Android library whose previews to prepare and render.",
        ),
        "producer": attr.label(mandatory = True, executable = True, cfg = "exec"),
        "cli": attr.label(executable = True, cfg = "exec", doc = "Optional renderer CLI; omit for source-bundle-only preparation."),
        "srcs": attr.label_list(allow_files = [".kt", ".java"]),
        "filter": attr.string(doc = "Build-time preview-ID substring selection; part of the action key."),
        "outputs": attr.string_list(default = ["bundle"], doc = "Default products: bundle, png and/or rendered_bundle."),
        "env": attr.string_dict(doc = "Local rendering environment only; preparation does not inherit it."),
        "pin_app_clock": attr.bool(
            default = True,
            doc = "Android: the target's own System.currentTimeMillis() reads the pinned preview clock, as Gradle's composePreview.pinAppClock. Part of the render action key.",
        ),
        "app_clock_packages": attr.string_list(
            doc = "Android: class-name prefixes to pin instead of the target's resource package (with a trailing '.').",
        ),
    },
    # Optional: with a registered render toolchain the render is cacheable and remote-executable.
    toolchains = [config_common.toolchain_type(RENDER_TOOLCHAIN_TYPE, mandatory = False)],
    doc = "One target: cacheable offline bundle preparation, optional rendering (cacheable with a render toolchain) and baked packaging, for a Desktop JVM or an Android library module.",
)
