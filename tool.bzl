"""Wrap separately declared producer and optional renderer tools."""

load(":java_info.bzl", "JDK_RUNTIME", "JavaInfo", "JavaRuntimeInfo")
load(":launcher.bzl", "RUNFILES_INIT", "RUNFILES_LIBRARY", "rlocation_path")

_CLI_LAUNCHER = """\
#!/usr/bin/env bash
{init}
set -euo pipefail
runfiles_export_envvars
if ! launcher="$(rlocation '{launcher}' '{repo}')" || [[ -z "$launcher" ]]; then
  echo 'compose-preview: {launcher} is missing from the runfiles' >&2
  exit 1
fi
exec "$launcher" "$@"
"""

def _cli_impl(ctx):
    wrapper = ctx.actions.declare_file(ctx.label.name + ".sh")
    ctx.actions.write(wrapper, _CLI_LAUNCHER.format(
        init = RUNFILES_INIT,
        launcher = rlocation_path(ctx, ctx.attr.launcher),
        repo = ctx.label.repo_name,
    ), is_executable = True)
    return [DefaultInfo(
        executable = wrapper,
        runfiles = ctx.runfiles(files = ctx.files.files + [ctx.file.launcher]).merge(
            ctx.attr._runfiles_library[DefaultInfo].default_runfiles,
        ),
    )]

compose_preview_cli = rule(
    implementation = _cli_impl,
    executable = True,
    attrs = {
        "launcher": attr.label(mandatory = True, allow_single_file = True),
        "files": attr.label_list(allow_files = True),
        "_runfiles_library": attr.label(default = RUNFILES_LIBRARY),
    },
    doc = "Expose a staged CLI distribution; launcher is its bin/compose-preview script.",
)

# The producer runs with Bazel's declared Java runtime and classpath, without its distribution's
# host-Java launcher or any renderer/cache environment. It is an exec tool, not a run target:
# compose_preview runs it as an action's executable from the execroot, so its wrapper names the
# JDK and jars by exec path and needs no runfiles library. `bazel run` on it is not supported.
def _producer_impl(ctx):
    jars = depset(ctx.files.jars, transitive = [dep[JavaInfo].transitive_runtime_jars for dep in ctx.attr.deps]).to_list()
    if not jars:
        fail("Stage the independent compose-preview-producer lib/*.jar files first")
    java = ctx.attr.java_runtime[JavaRuntimeInfo]
    if java.version and java.version < 17:
        fail("compose-preview producer requires a declared Java 17+ runtime")
    wrapper = ctx.actions.declare_file(ctx.label.name + ".sh")
    ctx.actions.write(wrapper, """#!/usr/bin/env bash
set -euo pipefail
exec '%s' -Dfile.encoding=UTF-8 -Duser.timezone=UTC -Duser.language=en -Duser.country=US -cp '%s' ee.schimke.composeai.bundle.producer.BundleProducerCli "$@"
""" % (java.java_executable_exec_path, ":".join([f.path for f in jars])), is_executable = True)
    return [DefaultInfo(
        executable = wrapper,
        files = depset([wrapper] + jars, transitive = [java.files]),
        runfiles = ctx.runfiles(files = jars, transitive_files = java.files),
    )]

compose_preview_producer = rule(
    implementation = _producer_impl,
    executable = True,
    attrs = {
        "jars": attr.label_list(allow_files = [".jar"]),
        "deps": attr.label_list(providers = [JavaInfo], doc = "Producer JavaInfo targets, compiled from source or resolved from Maven; includes full runtime closure."),
        "java_runtime": attr.label(default = JDK_RUNTIME, providers = [JavaRuntimeInfo], cfg = "exec", doc = "Declared Java 17+ runtime; override to reuse the monorepo's existing JDK."),
    },
    doc = "Declare the small offline producer distribution's lib/*.jar files and Java toolchain.",
)
