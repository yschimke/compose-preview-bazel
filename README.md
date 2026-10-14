# Compose Preview for Bazel

`rules_compose_preview` shares Compose discovery and the runnable PNG/ZIP bundle
format with [compose-ai-tools](https://github.com/yschimke/compose-ai-tools).
Experimental Desktop JVM support is tested with Bazel 9.2, Kotlin 2.4.20 and
Compose Multiplatform 1.11.1. Blaze needs internal validation. Android targets
render through the CLI with the Android aspect (below); native CMP and additional
exports remain future work.

## CLI first

Normal CLI commands prefer Gradle settings/wrapper, then fall back to Bazel/Blaze
workspace/BUILD indicators:

```sh
compose-preview list
compose-preview render --filter Greeting --output greeting.png
compose-preview bazel bundle --target //app:ui --no-render --output runnable.png \
  --maven-install maven_install.json
```

The CLI attaches an aspect to the existing application target without editing its
BUILD file. For ambiguous packages commit `.compose-preview/bazel.json` with
`{"schemaVersion":1,"target":"//app:ui"}`. When these rules are installed, set
`aspect` to `@rules_compose_preview//:runtime.bzl%compose_preview_aspect`.
Custom aspects/providers and Blaze command wrappers use the same
[configuration contract](https://github.com/yschimke/compose-ai-tools/blob/main/docs/BAZEL.md).

## Android targets (experimental)

`android.bzl%compose_preview_android_aspect` attaches to an existing
`android_library` (or `kt_android_library`) and writes artifacts schema 2
(compose-preview-contracts' `build-artifacts-protocol`): the JVM fields of
`runtime.bzl` plus an `android` block with a linked resource APK, the manifest it
was linked against and R classes with the APK's final IDs, first on the runtime
classpath. A library never links its own resources, so the aspect runs the SDK's
`aapt2 link` over the compiled resources rules_android already produced; it uses
only the public `StarlarkAndroidResourcesInfo` provider and the Android SDK
toolchain (`android_providers.bzl` is the shim to substitute elsewhere).

```sh
bazel build //app:ui \
  --aspects=@rules_compose_preview//:android.bzl%compose_preview_android_aspect \
  --output_groups=compose_preview_metadata,compose_preview_runtime
```

Requires rules_android 0.6.6 or newer and a registered Android SDK toolchain.
Assets reached through `deps` and `exports` are packed into the APK under
`assets/`, a target's own replacing a dependency's at the same path.

The CLI renders these artifacts on its Robolectric daemon when
`.compose-preview/bazel.json` names this aspect:

```json
{
  "schemaVersion": 1,
  "target": "//app:ui",
  "aspect": "@rules_compose_preview//:android.bzl%compose_preview_android_aspect"
}
```

`compose-preview render` and `compose-preview bazel bundle` then work as for
Desktop targets. compose-ai-contrib's `bazel-android-render` job is the
end-to-end proof on a rules_android `kt_android_library`.

## Source-bundle-only monorepos

Omit `cli` and declare only the small producer tool:

```starlark
compose_preview(
    name = "ui-source-bundle",
    module = ":ui",
    producer = "@compose_preview_producer//:producer",
    srcs = glob(["src/**/*.kt"]),
    maven_install = ":maven_install.json",
)
```

This target performs annotation discovery, compiled-artifact scanning and ZIP
packaging. It needs neither the full compose-ai-tools CLI nor a daemon/renderer
distribution. Consumers can download the independent producer binary or resolve
its published Maven dependencies; they do not need a checkout of compose-ai-tools
or a nested Gradle build. No Compose runtime is loaded to discover previews.
The runnable bundle records application runtime Maven coordinates for a separate
server or CLI to resolve later. Here “source bundle” means an **unrendered runnable
bundle containing compiled project bytecode/resources**, rather than a Kotlin-source
archive. It does not compile Kotlin on the consuming server.

`compose_preview_producer` also accepts `deps = [":producer_core"]` with JavaInfo:
use a small producer compiled from source, or a Maven-resolved `bundle-producer`
target, and the tool includes its transitive runtime JARs. `java_runtime` can select
the monorepo's existing declared Java 17+ target; it defaults to rules_java's pinned
remote JDK 17. The producer artifact must be released before resolving it from
Maven Central; the independent distribution is available from a source build now.

The producer uses JVM discovery (ClassGraph, ASM, Kotlin metadata), Kotlin
serialization, bundle format/IO and shared preview DTO/device contracts. Some
contract artifact names retain `daemon-*`, but executable daemon client/core/BTA,
renderer, native Compose/Skiko, HTTP stacks and the full CLI are excluded and a
build check guards that boundary. CLI support is an optional attribute; requesting
PNG defaults without it fails clearly. `bazel run` on a source-only target prints
the prepared bundle path.

## Bazel first

```starlark
load("@rules_compose_preview//:defs.bzl", "compose_preview")

compose_preview(
    name = "ui-preview",
    module = ":ui",  # existing JavaInfo-producing JVM library
    producer = "@compose_preview_producer//:producer",
    cli = "@compose_preview_cli//:cli",
    srcs = glob(["src/**/*.kt"]),  # optional own and generated sources
    maven_install = ":maven_install.json",
    outputs = ["bundle"],  # default; png and rendered_bundle are optional
)
```

```sh
bazel build //app:ui-preview
bazel run //app:ui-preview -- render --filter Greeting --output greeting.png --json
bazel run //app:ui-preview -- inspect
bazel build //app:ui-preview --output_groups=png
bazel build //app:ui-preview --output_groups=rendered_bundle
```

One target exposes three products:

| Group | Product | Execution |
| --- | --- | --- |
| `bundle` | `ui-preview.bundle.png`, runnable PNG/ZIP with a one-pixel stub cover | Offline preparation with normal Bazel caching |
| `png` | `ui-preview.renders/`, `<preview-id>.png` files | Existing CLI bundle renderer, local and uncached |
| `rendered_bundle` | `ui-preview.rendered-bundle.png`, actual cover and baked previews | Local render followed by cacheable offline packaging |

Bundle-only builds and the default `run` prerequisites do not render. Preparation
uses declared compiled project/runtime JARs, sources, Maven lockfile, producer
JARs and a Bazel Java runtime toolchain. It does not download Maven dependencies,
provision renderer sidecars, or inherit host render settings. Unchanged inputs
reuse the action cache; changed runtime resources invalidate it. `filter` on the
rule changes the preparation action key. Runtime selection after `bazel run`
affects only the CLI render, which can render different selections from the same
prepared bundle. No sibling targets or nested Bazel builds are generated.

`run` defaults to rendering (the `render` word is optional) and also supports
`inspect` and `extract`. Render outputs default to
`.compose-preview/outputs/ui-preview`, overrideable with `COMPOSE_PREVIEW_OUT`
or `--output`. A PNG file output requires one selected preview. Rendering uses the
existing CLI bundle path, so Maven dependencies resolve from local caches or
Central/Google Maven. Rendering requires a local Unix host and Java 17+.

Render actions are deliberately local, uncached and non-remote while the CLI
provisions renderer/native libraries and uses host fonts. `env` and
`--action_env=JAVA_HOME --action_env=XDG_CACHE_HOME` apply only to local rendering.
Do not pass credential-bearing environment variables into actions. Preparation
is cacheable; hermetic rendering is a separate future toolchain project.

Coordinates are preferred even for transformed Bazel JARs. Recovery considers
lockfile paths/names, Maven layout, pom.properties, manifest versions and the
closest identifiable lockfile artifact. Published checksums describe published
bytes. `dependency-resolution.json` records the evidence. Unknown/unpublished
project dependencies are embedded; full resources and runtime closure are kept
for reflection. Plain arbitrary runfiles do not establish JNI/data layout support;
JVM resources packaged in JARs are tested.

## Install and publish

The rules, producer and CLI are independently pinned. There is no BCR entry yet.
<!-- x-release-please-start-version -->
Use `git_override` at a tested immutable commit or the `v0.1.0-rc.5` source archive:
<!-- x-release-please-end -->

```starlark
bazel_dep(name = "rules_compose_preview", version = "0.1.0-rc.5")  # x-release-please-version
git_override(
    module_name = "rules_compose_preview",
    remote = "https://github.com/yschimke/compose-preview-bazel.git",
    commit = "<full tested SHA>",
)

producer_repository = use_repo_rule(
    "@rules_compose_preview//:repositories.bzl",
    "compose_preview_producer_repository",
)
producer_repository(
    name = "compose_preview_producer",
    urls = ["<immutable compose-preview-producer distribution .tar.gz URL>"],
    sha256 = "<archive SHA-256>",
    strip_prefix = "compose-preview-producer-<version>",
)
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

Until the producer is released, build compose-ai-tools with
`scripts/agent-gradle.sh :bundle-producer:distTar :cli:distTar` and pin those
archives. Repository rules use Bazel's downloader during fetch, never during
preparation. For local development use `local_path_override`, and declare staged
producer `lib/*.jar` with `compose_preview_producer` from `tool.bzl`. A staged CLI
uses `compose_preview_cli` with its launcher and distribution files.

`python3 scripts/package.py --out build/release` produces a deterministic compact
rules archive and checksum. CLI/producer binaries stay out of the rules archive.
CI publishes these artifacts; tagged prereleases have GitHub source archives.

Releases go through release-please (`.github/workflows/release-please.yml`). It
keeps a `chore(main): release X` pull request open from the conventional commits on
`main`. Merging it tags `vX`, creates the GitHub prerelease, and attaches the
compact archive and checksum; any later run attaches them if a release still lacks them. Versions stay on `0.1.0-rc.N` (`versioning:
prerelease`) until the config drops `prerelease`; `MODULE.bazel` and this README
carry the version markers it rewrites. Older prereleases used different APIs and
required rendering to prepare bundles.

## Verification and project setup

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
python3 tests/integration.py \
  --fixture /path/to/compose-ai-contrib/bazel-desktop \
  --cli-dist /path/to/tools/cli/build/install/compose-preview \
  --producer-dist /path/to/tools/bundle/producer/build/install/compose-preview-producer
```

Prepared bundles use sorted archive entries, fixed timezone-independent ZIP timestamps,
UTF-8/canonical JSON and portable source paths. Classpath order is preserved.
The same declared compiled inputs and pinned producer/JDK reproduce byte-identical
bundles across workspace locations/locales/timezones; the test executes the producer
afresh in two relocated roots and compares full SHA-256s. Baking is reproducible
for identical PNG inputs. Rendering still needs a declared native/font toolchain
before image reproducibility across hosts can be guaranteed.

The external-consumer test packages rules and both tools, imports pinned archives
into a fresh workspace, compares CLI/native PNGs, renders outside the workspace,
checks the bundle-only action graph, verifies a cache hit, changes a runtime
resource to verify invalidation, and checks the optional baked bundle.

Renovate and project governance use `yschimke/renovate-config`: squash-only merges,
branch cleanup, common checks and Protect Main. The shared policy has been applied.
