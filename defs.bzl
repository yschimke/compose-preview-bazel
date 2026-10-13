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
    files = depset(ctx.files.srcs + ctx.files.maven_install, transitive = [runtime, data.files])
    manifest = ctx.actions.declare_file(ctx.label.name + (".runfiles-artifacts.json" if runfiles else ".artifacts.json"))

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

def _impl(ctx):
    if not ctx.attr.outputs or len(depset(ctx.attr.outputs).to_list()) != len(ctx.attr.outputs):
        fail("outputs must be a nonempty list of distinct output kinds")
    if any([kind not in ["png", "bundle"] for kind in ctx.attr.outputs]):
        fail("supported outputs are png and bundle")
    metadata, files, data = _metadata(ctx)
    run_metadata, _, _ = _metadata(ctx, runfiles = True)
    output = ctx.actions.declare_directory(ctx.label.name)
    args = ctx.actions.args()
    args.add(ctx.executable.cli.path)
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
        args.add_all(["--filter", ctx.attr.filter])
    requirements = {"local": "1", "no-cache": "1", "no-remote": "1"}
    ctx.actions.run_shell(
        command = 'exec "$@" >&2',
        tools = [ctx.attr.cli[DefaultInfo].files_to_run],
        inputs = depset([metadata], transitive = [files]),
        outputs = [output],
        arguments = [args],
        env = ctx.attr.env,
        use_default_shell_env = True,
        execution_requirements = requirements,
        mnemonic = "ComposePreviewLocalRender",
        progress_message = "Rendering local Desktop PNG previews for %{label}",
    )
    bundle = None
    if "bundle" in ctx.attr.outputs:
        bundle = ctx.actions.declare_file(ctx.label.name + ".bundle.png")
        work = ctx.actions.declare_directory(ctx.label.name + ".bundle-work")
        pack_args = ctx.actions.args()
        pack_args.add_all([
            ctx.executable.cli.path,
            output.path,
            work.path,
            "bazel",
            "bundle",
            "--reuse-renders",
            "--project",
            ".",
            "--artifacts",
            metadata.path,
            "--execution-root",
            ".",
            "--out",
            work.path,
            "--output",
            bundle.path,
            "--timeout",
            str(ctx.attr.timeout),
            "--json",
        ])
        if ctx.file.maven_install:
            pack_args.add_all(["--maven-install", ctx.file.maven_install.path])
        if ctx.attr.filter:
            pack_args.add_all(["--filter", ctx.attr.filter])
        ctx.actions.run_shell(
            command = 'set -eu; cli="$1"; captures="$2"; work="$3"; shift 3; mkdir -p "$work"; cp -R "$captures/." "$work/"; chmod -R u+w "$work"; exec "$cli" "$@" >&2',
            tools = [ctx.attr.cli[DefaultInfo].files_to_run],
            inputs = depset([metadata, output], transitive = [files]),
            outputs = [bundle, work],
            arguments = [pack_args],
            env = ctx.attr.env,
            use_default_shell_env = True,
            execution_requirements = requirements,
            mnemonic = "ComposePreviewBundle",
            progress_message = "Packing existing captures for %{label}",
        )
    launcher = ctx.actions.declare_file(ctx.label.name + ".sh")
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
mkdir -p "$out"
cp -R "$rf/%s/." "$out/"
chmod -R u+w "$out"
exec "$rf/%s" bazel render --reuse-renders --project "$PWD" --artifacts "$rf/%s" \
  --execution-root "$rf/%s" --out "$out" --timeout %s %s %s "$@"
""" % (
        ctx.label.name,
        _key(ctx, output),
        _key(ctx, ctx.executable.cli),
        _key(ctx, run_metadata),
        ctx.workspace_name,
        str(ctx.attr.timeout),
        ('--maven-install "$rf/%s"' % _key(ctx, ctx.file.maven_install)) if ctx.file.maven_install else "",
        ("--filter " + _shell_quote(ctx.attr.filter)) if ctx.attr.filter else "",
    ), is_executable = True)
    runfiles = ctx.runfiles(files = [run_metadata, ctx.executable.cli, output] + ([bundle] if bundle else []), transitive_files = files)
    runfiles = runfiles.merge(data).merge(ctx.attr.cli[DefaultInfo].default_runfiles)
    defaults = ([output] if "png" in ctx.attr.outputs else []) + ([bundle] if bundle else [])
    return [
        DefaultInfo(executable = launcher, files = depset(defaults), runfiles = runfiles),
        OutputGroupInfo(
            png = depset([output]),
            bundle = depset([bundle] if bundle else []),
            compose_preview_metadata = depset([metadata]),
            compose_preview_runtime = files,
        ),
    ]

def _shell_quote(value):
    return "'" + value.replace("'", "'\"'\"'") + "'"

_common = {
    "maven_install": attr.label(allow_single_file = [".json"], doc = "rules_jvm_external lockfile for portable Maven dependencies."),
    "module": attr.label(mandatory = True, providers = [JavaInfo]),
    "cli": attr.label(mandatory = True, executable = True, cfg = "exec", allow_files = True),
    "srcs": attr.label_list(allow_files = [".kt", ".java"], doc = "Optional own sources, including generated sources."),
    "timeout": attr.int(default = 300),
    "filter": attr.string(doc = "Optional preview-id substring; discovery otherwise renders all own previews."),
}

compose_preview = rule(
    implementation = _impl,
    executable = True,
    attrs = dict(_common, **{
        "outputs": attr.string_list(default = ["png"], doc = "Default output groups: png and/or bundle. Bundle depends on the shared PNG render."),
        "env": attr.string_dict(doc = "Explicit local render environment; action_env flags also apply."),
    }),
    doc = "One preview target for build/run, with png and bundle output groups. No sibling target names are generated.",
)
