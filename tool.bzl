"""Wrap a declared CLI distribution with executable/runfiles semantics."""

def _cli_impl(ctx):
    wrapper = ctx.actions.declare_file(ctx.label.name + ".sh")
    path = ctx.file.launcher.short_path
    key = path[3:] if path.startswith("../") else ctx.workspace_name + "/" + path
    ctx.actions.write(wrapper, """#!/usr/bin/env bash
set -euo pipefail
rf="${RUNFILES_DIR:-$0.runfiles}"
if [[ ! -d "$rf" ]]; then
  echo 'compose-preview CLI requires directory runfiles on a Unix host' >&2
  exit 1
fi
exec "$rf/%s" "$@"
""" % key, is_executable = True)
    return [DefaultInfo(
        executable = wrapper,
        runfiles = ctx.runfiles(files = ctx.files.files + [ctx.file.launcher]),
    )]

compose_preview_cli = rule(
    implementation = _cli_impl,
    executable = True,
    attrs = {
        "launcher": attr.label(mandatory = True, allow_single_file = True),
        "files": attr.label_list(allow_files = True),
    },
    doc = "Expose a staged CLI distribution; launcher is its bin/compose-preview script.",
)
