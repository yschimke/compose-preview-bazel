"""Offline cached preparation plus optional local rendering through compose-preview."""

load(":java_info.bzl", "JavaInfo")
load(":render_toolchain.bzl", "RENDER_TOOLCHAIN_TYPE", "render_inputs")

def _key(ctx, file):
    path = file.short_path
    return path[3:] if path.startswith("../") else ctx.workspace_name + "/" + path

def _metadata(ctx):
    java = ctx.attr.module[JavaInfo]
    project = java.runtime_output_jars
    if not project:
        fail("module must provide its own runtime output jars")
    runtime = depset(project, transitive = [java.transitive_runtime_jars])
    runtime_files = runtime.to_list()
    data = ctx.attr.module[DefaultInfo].default_runfiles
    files = depset(ctx.files.srcs + ctx.files.maven_install, transitive = [runtime, data.files])
    manifest = ctx.actions.declare_file(ctx.label.name + ".artifacts.json")
    ctx.actions.write(manifest, json.encode({
        "schemaVersion": 1,
        "target": str(ctx.attr.module.label),
        "configuration": ctx.bin_dir.path,
        "projectJars": [f.path for f in project],
        "runtimeJars": [f.path for f in runtime_files],
        # The target that produced each jar; see runtime.bzl.
        "runtimeJarOwners": {f.path: str(f.owner) for f in runtime_files if f.owner},
        "sourceFiles": [f.path for f in ctx.files.srcs],
        "dataFiles": [f.path for f in data.files.to_list()],
    }))
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
    if ctx.executable.cli:
        output = ctx.actions.declare_directory(ctx.label.name + ".renders")
        render_args = ctx.actions.args()
        render_args.add_all([ctx.executable.cli.path, "bundle", "render", bundle.path, "--output", output.path])
        toolchain = ctx.toolchains[RENDER_TOOLCHAIN_TYPE]
        if toolchain:
            # Every input declared: the render reads only staged files, with the network off, so
            # it can be cached and run remotely like any other action.
            jars = ctx.actions.declare_file(ctx.label.name + ".render-jars.txt")
            jar_list = ctx.actions.args()
            jar_list.add_all(ctx.attr.module[JavaInfo].transitive_runtime_jars)
            ctx.actions.write(jars, jar_list)
            declared, setup = render_inputs(
                toolchain.compose_preview_render,
                output.path + ".stage",
                jars,
                ctx.file._stage_maven,
                ctx.file.maven_install,
            )
            ctx.actions.run_shell(
                command = "\n".join(["set -eu"] + setup + ['exec "$@" >&2']),
                tools = [ctx.attr.cli[DefaultInfo].files_to_run],
                inputs = depset(
                    [bundle],
                    transitive = [declared, ctx.attr.module[JavaInfo].transitive_runtime_jars],
                ),
                outputs = [output],
                arguments = [render_args],
                env = ctx.attr.env,
                toolchain = RENDER_TOOLCHAIN_TYPE,
                mnemonic = "ComposePreviewRender",
                progress_message = "Rendering prepared Desktop previews for %{label}",
            )
        else:
            # No render toolchain: the CLI provisions its inputs into the host cache at run time,
            # so the action can be neither cached nor run remotely.
            ctx.actions.run_shell(
                command = 'exec "$@" >&2',
                tools = [ctx.attr.cli[DefaultInfo].files_to_run],
                inputs = [bundle],
                outputs = [output],
                arguments = [render_args],
                env = ctx.attr.env,
                use_default_shell_env = True,
                execution_requirements = {"local": "1", "no-cache": "1", "no-remote": "1"},
                mnemonic = "ComposePreviewLocalRender",
                progress_message = "Rendering prepared Desktop previews for %{label}",
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
    if ctx.executable.cli:
        ctx.actions.write(launcher, """#!/usr/bin/env bash
    set -euo pipefail
    rf="${RUNFILES_DIR:-$0.runfiles}"
    if [[ ! -d "$rf" ]]; then
      echo 'compose-preview requires directory runfiles on a Unix host' >&2
      exit 1
    fi
    rf="$(cd "$rf" && pwd -P)"
    export RUNFILES_DIR="$rf"
    operation=render
    if [[ "${1:-}" == render || "${1:-}" == inspect || "${1:-}" == extract ]]; then
      operation="$1"; shift
    fi
    if [[ "$operation" == render ]]; then
      out="${COMPOSE_PREVIEW_OUT:-${BUILD_WORKSPACE_DIRECTORY:-$PWD}/.compose-preview/outputs/%s}"
      extra=(--output "$out")
  for arg in "$@"; do
    if [[ "$arg" == --output || "$arg" == -o ]]; then extra=(); break; fi
  done
  exec "$rf/%s" bundle render "$rf/%s" "${extra[@]}" "$@"
    fi
    exec "$rf/%s" bundle "$operation" "$rf/%s" "$@"
    """ % (ctx.label.name, _key(ctx, ctx.executable.cli), _key(ctx, bundle), _key(ctx, ctx.executable.cli), _key(ctx, bundle)), is_executable = True)
        runfiles = ctx.runfiles(files = [bundle, ctx.executable.cli]).merge(ctx.attr.cli[DefaultInfo].default_runfiles)
    else:
        ctx.actions.write(launcher, """#!/usr/bin/env bash
set -euo pipefail
rf="${RUNFILES_DIR:-$0.runfiles}"
if [[ $# != 0 ]]; then
  echo 'Source-only target prepares bundles; rendering requires an optional cli attribute.' >&2
  exit 64
fi
printf '%%s\n' "$rf/%s"
""" % _key(ctx, bundle), is_executable = True)
        runfiles = ctx.runfiles(files = [bundle])
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
        "module": attr.label(mandatory = True, providers = [JavaInfo]),
        "producer": attr.label(mandatory = True, executable = True, cfg = "exec"),
        "cli": attr.label(executable = True, cfg = "exec", doc = "Optional renderer CLI; omit for source-bundle-only preparation."),
        "srcs": attr.label_list(allow_files = [".kt", ".java"]),
        "filter": attr.string(doc = "Build-time preview-ID substring selection; part of the action key."),
        "outputs": attr.string_list(default = ["bundle"], doc = "Default products: bundle, png and/or rendered_bundle."),
        "env": attr.string_dict(doc = "Local rendering environment only; preparation does not inherit it."),
    },
    # Optional: with a registered render toolchain the render is cacheable and remote-executable.
    toolchains = [config_common.toolchain_type(RENDER_TOOLCHAIN_TYPE, mandatory = False)],
    doc = "One target: cacheable offline bundle preparation, optional rendering (cacheable with a render toolchain) and baked packaging.",
)
