"""Shell launcher pieces shared by defs.bzl and tool.bzl. Internal to these rules; not an API."""

# The Bash runfiles library. On every Bazel version rules_shell supports, this target puts
# runfiles.bash at the legacy bazel_tools path that RUNFILES_INIT looks up.
RUNFILES_LIBRARY = Label("@rules_shell//shell/runfiles")

# The Bash runfiles library's standard v3 initialization, copied verbatim. It finds the library
# through RUNFILES_DIR, a runfiles manifest, or the launcher's own <launcher>.runfiles, so the
# launcher works under `bazel run`, run directly from bazel-bin, and without a runfiles tree.
RUNFILES_INIT = """\
# --- begin runfiles.bash initialization v3 ---
# Copy-pasted from the Bazel Bash runfiles library v3.
set -uo pipefail; set +e; f=bazel_tools/tools/bash/runfiles/runfiles.bash
# shellcheck disable=SC1090
source "${RUNFILES_DIR:-/dev/null}/$f" 2>/dev/null || \\
  source "$(grep -sm1 "^$f " "${RUNFILES_MANIFEST_FILE:-/dev/null}" | cut -f2- -d' ')" 2>/dev/null || \\
  source "$0.runfiles/$f" 2>/dev/null || \\
  source "$(grep -sm1 "^$f " "$0.runfiles_manifest" | cut -f2- -d' ')" 2>/dev/null || \\
  source "$(grep -sm1 "^$f " "$0.exe.runfiles_manifest" | cut -f2- -d' ')" 2>/dev/null || \\
  { echo>&2 "ERROR: cannot find $f"; exit 1; }; f=; set -e
# --- end runfiles.bash initialization v3 ---
"""

def rlocation_path(ctx, file):
    """The rlocation path of a File, as $(rlocationpath) prints it.

    $(rlocationpath) cannot be used for these files. The bundle is an output this rule declares,
    so no attribute names it; and the CLI attribute is often an alias (as for a CLI from
    compose_preview_cli_repository), whose Target.label is the actual target's, which
    ctx.expand_location does not accept for the attribute. So this spells out the same mapping:
    a file in another repository has a short path of ../<repository>/<path>, and one in the
    main repository is <workspace name>/<path>.

    Args:
      ctx: the rule context.
      file: a File in the rule's runfiles.

    Returns:
      The path to pass to rlocation.
    """
    path = file.short_path
    return path[len("../"):] if path.startswith("../") else ctx.workspace_name + "/" + path
