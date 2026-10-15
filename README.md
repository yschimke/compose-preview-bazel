# Compose Preview for Bazel

`rules_compose_preview` shares Compose discovery and the runnable PNG/ZIP bundle
format with [compose-ai-tools](https://github.com/yschimke/compose-ai-tools).
Experimental Desktop JVM support is tested with Bazel 9.2, Kotlin 2.4.20 and
Compose Multiplatform 1.11.1. Blaze needs internal validation. Android targets
render through the CLI with the Android aspect (below), or as ordinary Bazel
actions through `compose_preview` ([Android modules](#android-modules-in-compose_preview));
native CMP and additional exports remain future work.

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

The aspect the CLI names does not propagate. It requires
`compose_preview_android_collector`, which walks `deps` and `exports` carrying no
tools and flattening nothing, and then links and writes metadata once, for the
target it is applied to. So `link.sh`, the JDK and the SDK toolchain are
dependencies of the selected target only, never of the libraries it reaches, and
analysis of a large graph stays linear.

Requires rules_android 0.6.6 or newer and a registered Android SDK toolchain.
`java_info.bzl` is the one place the rules load rules_java: `JavaInfo`,
`JavaRuntimeInfo` and `JDK_RUNTIME`, the JDK the link compiles R classes with.
Edit it, with `android_providers.bzl`, to run the rules where Java and Android
rules live elsewhere; the labels the rules use are package-relative, so a vendored
copy below a repository root loads unchanged.
Assets reached through `deps` and `exports` are packed into the APK under
`assets/`, a target's own replacing a dependency's at the same path.

The `android.link` block records what that link was made from: the compiled
resource archives in overlay order, the staged assets, every R package (the
target's own first) and the aapt2, JDK and `android.jar` it used, all
materialized by the `compose_preview_runtime` output group. `link.sh` is the link
itself, run by the aspect per target and by the CLI once over the union of a
selection's targets, so several libraries render from one resource APK.

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

### Android modules in `compose_preview`

The CLI path above renders outside Bazel, so nothing it renders is cached.
`compose_preview` also takes an Android library as its `module`, and then
prepares, renders and bakes it with the same three actions as a Desktop module:

```starlark
compose_preview(
    name = "ui-preview",
    module = ":ui",  # android_library or kt_android_library
    producer = "@compose_preview_producer//:producer",
    cli = "@compose_preview_cli//:cli",
    srcs = glob(["src/**/*.kt"]),
    maven_install = ":maven_install.json",
    outputs = ["png"],
)
```

How it fits together, reusing what already exists rather than adding a mode:

- **Linking.** `module` carries `compose_preview_android_module_aspect`, the
  Android aspect's own implementation returning a provider instead of an output
  group. It applies only to targets advertising `AndroidIdeInfo`
  (`android_library`, and so `kt_android_library`'s wrapper), so a Desktop module
  never resolves the Android SDK toolchain and its actions are unchanged.
- **Preparation.** The rule writes artifacts schema 2 (the `android` block:
  package, linked resource APK, manifest, final-ID R jar, `compileSdk`) and the
  producer turns it into a `backend="android"` bundle carrying those resources,
  exactly as `compose-preview bazel bundle` does. No bundle-format change.
- **Rendering.** `bundle render` on that bundle runs the Robolectric renderer
  from the Android daemon sidecar. The rule passes the target's SDK level
  (`--android-sdk`), the `android.jar` its link used (`--android-jar`, a declared
  input, so the host SDK is never read) and its app clock (`--clock-packages`).
- **App clock.** `pin_app_clock` (default on, as Gradle's
  `composePreview.pinAppClock` and `bazel.json`'s `pinAppClock`) pins
  `System.currentTimeMillis()` for the target's own package (`<package>.`), or
  for `app_clock_packages` when set. The bundle format is untouched: the list is
  a render flag, part of the render action's key.
- **Caching.** Cacheable only when the registered render toolchain declares both
  Android inputs, `android_daemon` and `android_all` (below). Without them, or
  without a toolchain, the render is local, uncached and non-remote, as a Desktop
  render without a toolchain is. Preparation and baking are cacheable either way.

An SDK 36 render needs a Java 21 runtime (the toolchain's `java_runtime`); on
Java 17 the CLI clamps the sandbox to SDK 35 and says so.

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
| `png` | `ui-preview.renders/`, `<preview-id>.png` files | Existing CLI bundle renderer: local and uncached, or cacheable with a [render toolchain](#cacheable-rendering-a-render-toolchain) |
| `rendered_bundle` | `ui-preview.rendered-bundle.png`, actual cover and baked previews | The render followed by cacheable offline packaging |

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

Without a render toolchain, render actions are deliberately local, uncached and
non-remote: the CLI provisions renderer/native libraries into the host cache and
uses host fonts. `env` and `--action_env=JAVA_HOME --action_env=XDG_CACHE_HOME`
apply only to that local rendering. Do not pass credential-bearing environment
variables into actions. Preparation is cacheable either way.

### Cacheable rendering: a render toolchain

Register a `compose_preview_render_toolchain` and the render declares every input
instead: the CLI's daemon and renderer jars, the host's Skiko native, fonts and the
Java runtime. The action stages them as the CLI's own sidecar and cache directories
in a fresh temporary directory of its own (removed when the action exits), and runs
the CLI with the network off (`COMPOSE_PREVIEW_NO_NETWORK`, plus
`COMPOSE_PREVIEW_OFFLINE` for CLIs that predate it), a strict environment, its own
`user.home` and a pinned locale, timezone and encoding.

The bundle's Maven dependencies are already inputs: the module's runtime jars. The
prepared bundle records, for each one it travels as a coordinate, the SHA-256 of
the jar the producer identified (`dependency-resolution.json`), and the render
passes the runtime jar list as `bundle render --local-jars`, so each coordinate
resolves to the jar with that content: a stamped `processed_` jar, rules_kotlin's
unversioned `kotlin-stdlib.jar` or an AAR's classes jar alike, whatever the
lockfile pins. The jars are also laid out as a local Maven repository, for CLIs
that predate `--local-jars`.
It drops `local`/`no-cache`/`no-remote`, so renders are cached and can run remotely.

```starlark
load("@rules_compose_preview//:render_toolchain.bzl", "compose_preview_render_toolchain")

compose_preview_render_toolchain(
    name = "render_inputs",
    desktop_daemon = ["@compose_preview_sidecars//:lib-daemon-desktop"],
    renderer = ["@compose_preview_sidecars//:lib-renderer"],
    skiko = ["@skiko_awt_runtime_linux_x64//file"],
    fonts = glob(["fonts/**"]),
)

toolchain(
    name = "render_toolchain",
    toolchain = ":render_inputs",
    toolchain_type = "@rules_compose_preview//:render_toolchain_type",
)
```

Then `register_toolchains("//:render_toolchain")` in `MODULE.bazel`, or
`--extra_toolchains=//:render_toolchain`. Point each attribute at the released
artifacts the CLI would otherwise download, pinned by checksum (`http_file`,
`http_archive`, `maven_install`) or vendored; `compose-preview provision` fetches
them once into a directory you can import. The
external-consumer CI job registers a toolchain from the files a CLI render
provisioned, checks the image matches the CLI's byte-for-byte, and checks a rebuild
after `bazel clean` is served from the cache.

Android modules add two attributes, the pieces `compose-preview provision
--android --android-sdk <n>` fetches:

```starlark
compose_preview_render_toolchain(
    name = "render_inputs",
    # ... the Desktop attributes above, if Desktop modules render too ...
    # compose-preview-android-daemon-<v>.zip from compose-preview-daemon's release
    android_daemon = ["@compose_preview_android_daemon//:lib-daemon-android"],
    # android-all-instrumented jars matching that daemon's Robolectric, one per SDK
    android_all = ["@android_all_36//file"],
    fonts = glob(["fonts/**"]),
    java_runtime = "@rules_java//toolchains:remotejdk_21",  # SDK 36 needs Java 21
)
```

An Android render stages only these (and the fonts), as `lib-daemon-android/`
and `COMPOSE_PREVIEW_ROBOLECTRIC_DEPS_DIR`, so a Desktop render's inputs, and its
cache key, do not change when they are added. The `android_all` jars must match
the Robolectric inside that Android daemon (compose-ai-tools' `docs/OFFLINE.md`).

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
Use `git_override` at a tested immutable commit or the `v0.1.0-rc.9` source archive:
<!-- x-release-please-end -->

```starlark
bazel_dep(name = "rules_compose_preview", version = "0.1.0-rc.9")  # x-release-please-version
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
buildifier -mode=check -lint=warn -r .   # CI pins v8.2.1
python3 -m unittest discover -s tests -p 'test_*.py'
bazel test //tests/analysis/...           # rules_testing analysis tests (Bazel 9)
COMPOSE_PREVIEW_BAZEL_TEST=1 python3 -m unittest tests/test_analysis_aquery.py
python3 tests/integration.py \
  --fixture /path/to/compose-ai-contrib/bazel-desktop \
  --cli-dist /path/to/tools/cli/build/install/compose-preview \
  --producer-dist /path/to/tools/bundle/producer/build/install/compose-preview-producer
python3 tests/integration.py \
  --android-fixture /path/to/compose-ai-contrib/bazel-apk \
  --android-check /path/to/compose-ai-contrib/.github/scripts/check-android-render.py \
  --cli-dist /path/to/tools/cli/build/install/compose-preview \
  --producer-dist /path/to/tools/bundle/producer/build/install/compose-preview-producer
```

The Android run (CI's External Android consumer job) renders bazel-apk's
`kt_android_library` through `compose_preview` with a render toolchain built from
what `compose-preview provision --android` fetched, and checks that the render is
the cacheable action, that executing it twice from empty caches gives the same
bytes, and that a rebuild after `bazel clean` is served from the cache.

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
