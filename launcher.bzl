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

def rlocation_path(ctx, target):
    """The rlocation path of a target's single file, as $(rlocationpath) prints it.

    Args:
      ctx: the rule context.
      target: a Target from one of the rule's attributes.

    Returns:
      The path to pass to rlocation.
    """
    return ctx.expand_location("$(rlocationpath %s)" % target.label, targets = [target])

def rlocation_path_of_output(ctx, file):
    """The rlocation path of a File this rule declares.

    $(rlocationpath) expands only labels of the rule's attributes, and a declared output has
    none, so this spells out the same mapping: a file in another repository has a short path of
    ../<repository>/<path>, and one in the main repository is <workspace name>/<path>.

    Args:
      ctx: the rule context.
      file: a File the rule declared.

    Returns:
      The path to pass to rlocation.
    """
    path = file.short_path
    return path[len("../"):] if path.startswith("../") else ctx.workspace_name + "/" + path
