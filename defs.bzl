"""Local Desktop PNG build/run targets using the existing CLI artifact contract.

Rendering is intentionally local and uncached while the CLI provisions renderer
sidecars on the host. No nested Bazel invocation or application aspect is needed.
"""

load("@rules_java//java/common:java_info.bzl", "JavaInfo")

def _key(ctx, file):
    path = file.short_path
    return path[3:] if path.startswith("../") else ctx.workspace_name + "/" + path

def _paths(values, runfiles):
    return [f.short_path if runfiles else f.path for f in values]

def _metadata(ctx, runfiles = False):
    if ctx.attr.timeout <= 0:
        fail("timeout must be positive")
    java = ctx.attr.module[JavaInfo]
    project = java.runtime_output_jars
    if not project:
        fail("module must provide its own runtime output jars")
    runtime = depset(project, transitive = [java.transitive_runtime_jars])
    data = ctx.attr.module[DefaultInfo].default_runfiles
    files = depset(ctx.files.srcs, transitive = [runtime, data.files])
    manifest = ctx.actions.declare_file(ctx.label.name + ".artifacts.json")

    # short_path resolves relative to the main workspace directory in the runfiles tree.
    ctx.actions.write(manifest, json.encode({
        "schemaVersion": 1,
        "target": str(ctx.attr.module.label),
        "configuration": ctx.bin_dir.path,
        "projectJars": _paths(project, runfiles),
        "runtimeJars": _paths(runtime.to_list(), runfiles),
        "sourceFiles": _paths(ctx.files.srcs, runfiles),
        "dataFiles": _paths(data.files.to_list(), runfiles),
    }))
    return manifest, files, data

def _render_impl(ctx):
    metadata, files, data = _metadata(ctx)
    output = ctx.actions.declare_directory(ctx.label.name)
    args = ctx.actions.args()
    args.add_all([
        "bazel",
        "render",
        "--project",
        ".",
        "--artifacts",
        metadata.path,
        "--execution-root",
        ".",
        "--out",
        output.path,
        "--format",
        "png",
        "--timeout",
        str(ctx.attr.timeout),
        "--json",
    ])
    if ctx.attr.filter:
        args.add("--filter", ctx.attr.filter)
    ctx.actions.run(
        executable = ctx.executable.cli,
        tools = [ctx.attr.cli[DefaultInfo].files_to_run],
        inputs = depset([metadata], transitive = [files]),
        outputs = [output],
        arguments = [args],
        env = ctx.attr.env,
        use_default_shell_env = True,
        execution_requirements = {"local": "1", "no-cache": "1", "no-remote": "1"},
        mnemonic = "ComposePreviewLocalRender",
        progress_message = "Rendering local Desktop PNG previews for %{label}",
    )
    return [
        DefaultInfo(files = depset([output])),
        OutputGroupInfo(
            compose_preview_metadata = depset([metadata]),
            compose_preview_runtime = files,
            preview_png = depset([output]),
        ),
    ]

def _run_impl(ctx):
    metadata, files, data = _metadata(ctx, runfiles = True)
    launcher = ctx.actions.declare_file(ctx.label.name + ".sh")

    # Bazel supplies a directory runfiles tree on the supported Unix hosts.
    # Preserve the caller's cwd so --output and --out stay relative to the user.
    ctx.actions.write(launcher, """#!/usr/bin/env bash
set -euo pipefail
rf="${RUNFILES_DIR:-$0.runfiles}"
if [[ ! -d "$rf" ]]; then
  echo 'compose-preview requires directory runfiles on a Unix host' >&2
  exit 1
fi
rf="$(cd "$rf" && pwd -P)"
export RUNFILES_DIR="$rf"
out="${COMPOSE_PREVIEW_OUT:-${BUILD_WORKSPACE_DIRECTORY:-$PWD}/.compose-preview/outputs/%s}"
exec "$rf/%s" bazel render --project "$PWD" --artifacts "$rf/%s" \\
  --execution-root "$rf/%s" --out "$out" --timeout %s %s "$@"
""" % (
        ctx.label.name,
        _key(ctx, ctx.executable.cli),
        _key(ctx, metadata),
        ctx.workspace_name,
        str(ctx.attr.timeout),
        ("--filter " + _shell_quote(ctx.attr.filter)) if ctx.attr.filter else "",
    ), is_executable = True)
    runfiles = ctx.runfiles(files = [metadata, ctx.executable.cli], transitive_files = files)
    runfiles = runfiles.merge(data).merge(ctx.attr.cli[DefaultInfo].default_runfiles)
    return [
        DefaultInfo(executable = launcher, runfiles = runfiles),
        OutputGroupInfo(
            compose_preview_metadata = depset([metadata]),
            compose_preview_runtime = files,
        ),
    ]

def _shell_quote(value):
    return "'" + value.replace("'", "'\"'\"'") + "'"

_common = {
    "module": attr.label(mandatory = True, providers = [JavaInfo]),
    "cli": attr.label(mandatory = True, executable = True, cfg = "exec", allow_files = True),
    "srcs": attr.label_list(allow_files = [".kt", ".java"], doc = "Optional own sources, including generated sources."),
    "timeout": attr.int(default = 300),
    "filter": attr.string(doc = "Optional preview-id substring; discovery otherwise renders all own previews."),
}

_render = rule(implementation = _render_impl, attrs = dict(_common, **{
    "env": attr.string_dict(doc = "Explicit local render environment; action_env flags also apply."),
}))
_run = rule(implementation = _run_impl, attrs = _common, executable = True)

def compose_preview(name, module, cli, srcs = [], filter = "", formats = ["png"], timeout = 300, env = {}, **kwargs):
    """Declare <name> PNG build outputs and <name>-run interactive rendering.

    cli must be an executable target carrying its distribution in runfiles. Use
    compose_preview_cli_repository for a checksum-pinned CLI archive. Only static
    Desktop PNG is currently supported; unsupported formats fail at analysis.
    """
    if formats != ["png"]:
        fail("compose_preview currently supports formats = [\"png\"] only")
    _render(
        name = name,
        module = module,
        cli = cli,
        srcs = srcs,
        filter = filter,
        timeout = timeout,
        env = env,
        **kwargs
    )
    _run(name = name + "-run", module = module, cli = cli, srcs = srcs, filter = filter, timeout = timeout, **kwargs)
