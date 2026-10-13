# Compose Preview for Bazel

Reusable Bazel build/run rules for Compose UI previews, sharing the
[`compose-preview` CLI](https://github.com/yschimke/compose-ai-tools) artifact
contract. The Bzlmod module is `rules_compose_preview`.

Status: experimental Desktop JVM PNG support, tested with Bazel 9.2,
Kotlin/rules_kotlin 2.4.20 and CMP 1.11.1. Blaze uses the same provider/configuration
seam but needs internal validation. Android resource rendering, native CMP,
watch mode and additional export formats are not implemented here yet.

## CLI first

Normal CLI commands prefer Gradle settings/wrapper within the workspace and fall
back to Bazel/Blaze workspace/BUILD indicators:

```sh
compose-preview list
compose-preview render --filter Greeting --output greeting.png
```

The CLI attaches an aspect to the existing application target; no application BUILD
edit is required. For ambiguous packages commit `.compose-preview/bazel.json`:

```json
{"schemaVersion": 1, "target": "//app:ui"}
```

When this rules module is installed, configure the aspect as
`@rules_compose_preview//:runtime.bzl%compose_preview_aspect` to reuse it rather
than inject a second tool package. Provider imports/custom aspects and approved
Blaze wrappers can be selected through
[CLI configuration](https://github.com/yschimke/compose-ai-tools/blob/agent/bazel-aspect-driver/docs/BAZEL.md).

## Bazel first

Install the module and provide a declared executable CLI target, then in `app/BUILD.bazel`:

```starlark
load("@rules_compose_preview//:defs.bzl", "compose_preview")

compose_preview(
    name = "ui-preview",
    module = ":ui",  # existing JavaInfo-producing Compose JVM library
    cli = "@compose_preview_cli//:cli",
    srcs = glob(["src/**/*.kt"]),  # optional own/generated Kotlin or Java sources
    formats = ["png"],
    bundle = True,
    maven_install = ":maven_install.json",
)
```

```sh
bazel run //app:ui-preview-run -- --filter Greeting --output greeting.png
bazel build //app:ui-preview
bazel build //app:ui-preview-bundle
```

`run` builds the application's runtime closure and runs discovery/rendering locally.
It accepts normal Bazel-adapter selection/output flags after `--`. Default run
outputs go to `.compose-preview/outputs/ui-preview-run` in the workspace; override
with `COMPOSE_PREVIEW_OUT` or `--out`. It works from another directory too.

`build` renders all application-owned previews into the declared tree artifact
`bazel-bin/app/ui-preview/build/compose-previews/`. The existing manifest, captures,
PNG paths and state files are included; `--output_groups=preview_png` selects that
tree. `filter` on the rule narrows build-time selection. Dependency-owned previews
are excluded, but runtime-only classes/resources remain available. Neither form
starts a nested build.

**Rendering currently requires a local Unix host and Java 17+.** Build actions are
marked local, uncached and non-remote because the CLI still provisions renderer,
Skiko/native libraries and fonts through the host. They are not hermetic remote
render actions. `--action_env=JAVA_HOME --action_env=XDG_CACHE_HOME` can select an
already-provisioned runtime/cache; `env` on the rule declares additional local
settings. Do not pass credential-bearing environment variables into build actions.
Plain files in arbitrary runfiles trees do not establish JNI/data layout support;
JVM resources packaged in jars are tested.

## Install and pin in another repository

No Bazel Central Registry entry is claimed yet. Start with an immutable commit:

```starlark
# MODULE.bazel
bazel_dep(name = "rules_compose_preview", version = "0.1.0-rc.1")
git_override(
    module_name = "rules_compose_preview",
    remote = "https://github.com/yschimke/compose-preview-bazel.git",
    commit = "<full tested commit SHA>",
)
```

The initial prerelease is available now as a GitHub source archive (about 25 KB).
This pin has been downloaded and checked:

```starlark
archive_override(
    module_name = "rules_compose_preview",
    urls = ["https://github.com/yschimke/compose-preview-bazel/archive/refs/tags/v0.1.0-rc.1.tar.gz"],
    sha256 = "10a53c36d420a029f9272e2b6c8188c84ea89cd84bd4bae2b25c7079d09aa718",
    strip_prefix = "compose-preview-bazel-0.1.0-rc.1",
)
```

The compact ~10 KB module archive/checksum is also a CI artifact. Initial custom
release-asset upload was forbidden from the development environment; the tag and
source archive are published. Subsequent releases can attach compact assets from
the default-branch release workflow after bumping the module version.

Pin the CLI distribution separately using `use_repo_rule`:

```starlark
cli_repository = use_repo_rule(
    "@rules_compose_preview//:repositories.bzl",
    "compose_preview_cli_repository",
)
cli_repository(
    name = "compose_preview_cli",
    urls = ["<immutable compose-preview distribution .tar.gz URL>"],
    sha256 = "<archive SHA-256>",
    strip_prefix = "compose-preview-<version>",
)
```

Repository download uses Bazel's downloader at fetch time. It does not download
rule code or CLI distributions while rendering. Renderer sidecar provisioning is
still local, as described above.

Until a CLI release includes [tools #5947](https://github.com/yschimke/compose-ai-tools/pull/5947),
build that branch with `scripts/agent-gradle.sh :cli:installDist :cli:distTar` and use
the resulting archive. Existing released CLIs without the Bazel adapter cannot
consume this module. The CLI is independently versioned; rule releases do not
silently upgrade it.

For local development use `local_path_override` for the module. A staged CLI can
be declared with `compose_preview_cli` from `tool.bzl`, supplying its
`bin/compose-preview` launcher and distribution files as runfiles.

## Publishing and testing

`python3 scripts/package.py --out build/release` produces a deterministic module
archive and `.sha256` file. The archive contains only rule/provider files, module
metadata, license and README; CLI/renderer binaries are separate. Publish those
assets as a prerelease first. A stable BCR submission can follow after compatibility
and API stabilization; use the same archive, checksum and strip prefix.

The live external-consumer test packages both rules and CLI, creates a fresh
workspace, uses `archive_override` and the CLI repository rule, then exercises
normal CLI, native run/build and a launcher invoked outside the workspace:

```sh
python3 tests/integration.py \
  --fixture /path/to/compose-ai-contrib/bazel-desktop \
  --cli-dist /path/to/compose-ai-tools/cli/build/install/compose-preview
```

It compares PNG hashes across all entry points and checks runtime-only/generated
previews. CI publishes logs and actual rendered images. The
[export design](https://github.com/yschimke/compose-ai-tools/blob/agent/bazel-aspect-driver/docs/design/BAZEL_PREVIEW_TARGETS.md)
covers future Remote Compose `.rc`, APNG, layout JSON and Android accessibility
exports. Unsupported `formats` fail rather than produce placeholder files.

## Shared project setup

Renovate extends `github>yschimke/renovate-config`. Common PR-title and attribution
checks use the names in its repository policy. CI runs packaging validation and a
separate live external Compose consumer; release publication requires successful
default-branch CI. [Policy PR #22](https://github.com/yschimke/renovate-config/pull/22)
registers this repository for squash-only merges, branch cleanup, Actions
permissions and Protect Main with required `Bazel rules validation` plus common
checks. Applying the policy requires an owner credential with Administration
read/write; the development integration returned HTTP 403. From a checkout of
that policy change, run `repo-policy/apply-repo-policy.sh --apply compose-preview-bazel`.

## Runnable PNG/ZIP bundles

Set `bundle = True` and provide the `rules_jvm_external` `maven_install` lockfile.
`bazel build //app:ui-preview-bundle` writes `bazel-bin/app/ui-preview-bundle.png`,
a PNG/ZIP polyglot in the compose-ai-tools runnable bundle format. Any PNG viewer
shows its real cover; the preview server can resolve dependencies and re-render it.
The ordinary CLI supports `compose-preview render --bundle --maven-install maven_install.json`
and `compose-preview bazel bundle --target //app:ui --output bundle.png`.

Coordinates are preferred even when Bazel transforms JAR bytes. Recovery uses
lockfile group/version paths, artifact filenames, pom.properties and manifest
version hints, with a closest identifiable lockfile artifact as a fallback.
Published checksums are recorded when available; they describe the Maven artifact,
not transformed Bazel bytes. `dependency-resolution.json` records each decision.
Unknown/unpublished application dependencies are embedded, while Compose and other
identified libraries resolve from Maven Central or Google Maven. Full runtime
closure and resources are retained for reflection. `--embed-deps` explicitly opts
into an offline bundle. The CLI and server still require their renderer sidecars.
