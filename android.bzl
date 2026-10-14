"""Collect an Android Compose target's render inputs, without adding a preview rule.

Extends runtime.bzl's JVM metadata (artifacts schema 1) with the linked resources
a Robolectric render needs (schema 2, compose-preview-contracts'
build-artifacts-protocol): a standalone resource APK, the manifest it was linked
against and R classes carrying the final IDs of that APK.

An android_library never links its resources; only binaries and
android_local_test do. This aspect therefore links them itself with the SDK's
aapt2, using only the public resources provider and the Android SDK toolchain,
so it does not depend on rules_android's internal resource pipeline. Outputs live
under `_compose_preview/` and never collide with the target's own.
"""

load("@rules_java//java/common/rules:java_runtime.bzl", "JavaRuntimeInfo")
load(":android_providers.bzl", "ANDROID_SDK_TOOLCHAIN_TYPE", "StarlarkAndroidResourcesInfo")
load(":java_info.bzl", "JavaInfo")

_OwnJarsInfo = provider(
    doc = "A target's own compiled jars, including those of targets it exports.",
    fields = {"jars": "depset of File"},
)

def _own_jars(target, ctx):
    exported = [
        dep[_OwnJarsInfo].jars
        for dep in getattr(ctx.rule.attr, "exports", [])
        if _OwnJarsInfo in dep
    ]
    direct = target[JavaInfo].runtime_output_jars if JavaInfo in target else []
    return depset(direct, transitive = exported)

def _compile_sdk(android_jar):
    # .../platforms/android-36/android.jar -> 36; null when the layout is unfamiliar.
    for part in android_jar.path.split("/"):
        if part.startswith("android-") and part[len("android-"):].isdigit():
            return int(part[len("android-"):])
    return None

def _manifest(ctx, target, resources):
    for node in resources.direct_resources_nodes.to_list():
        if node.manifest:
            return node.manifest
    manifest = ctx.actions.declare_file("_compose_preview/%s/AndroidManifest.xml" % target.label.name)
    ctx.actions.write(manifest, """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="%s">
  <application />
</manifest>
""" % resources.package)
    return manifest

def _link(ctx, target, resources):
    sdk = ctx.toolchains[ANDROID_SDK_TOOLCHAIN_TYPE].android_sdk_info
    java_runtime = ctx.attr._jdk[JavaRuntimeInfo]
    prefix = "_compose_preview/%s/" % target.label.name
    manifest = _manifest(ctx, target, resources)
    apk = ctx.actions.declare_file(prefix + "resources.ap_")
    r_jar = ctx.actions.declare_file(prefix + "r-final.jar")

    # Dependencies first, the target's own resources last: later -R inputs overlay earlier ones.
    compiled = resources.transitive_compiled_resources.to_list() + \
               resources.direct_compiled_resources.to_list()
    packages = sorted([p for p in resources.packages_to_r_txts.keys() if p != resources.package])

    args = ctx.actions.args()
    args.add(sdk.aapt2.executable)
    args.add(java_runtime.java_home)
    args.add(apk)
    args.add(r_jar)
    args.add(manifest)
    args.add(sdk.android_jar)
    args.add(":".join(packages))
    args.add_all(compiled)

    ctx.actions.run_shell(
        inputs = depset(
            [manifest, sdk.android_jar] + compiled,
            transitive = [java_runtime.files],
        ),
        tools = [sdk.aapt2],
        outputs = [apk, r_jar],
        arguments = [args],
        mnemonic = "ComposePreviewAndroidLink",
        progress_message = "Linking Compose preview resources for %{label}",
        command = """set -eu
aapt2="$1"; jdk="$2"; apk="$3"; rjar="$4"; manifest="$5"; android_jar="$6"; extra="$7"
shift 7
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
set -- "$@"
overlays=""
for zip in "$@"; do overlays="$overlays -R $zip"; done
extra_flag=""
if [ -n "$extra" ]; then extra_flag="--extra-packages $extra"; fi
# shellcheck disable=SC2086
"$aapt2" link -o "$apk" --manifest "$manifest" -I "$android_jar" --auto-add-overlay \
  --no-version-vectors --java "$work/gen" $extra_flag $overlays
mkdir "$work/classes"
find "$work/gen" -name '*.java' > "$work/sources"
"$jdk/bin/javac" -nowarn -encoding UTF-8 -d "$work/classes" "@$work/sources"
"$jdk/bin/jar" --create --file "$rjar" --date=1980-01-01T00:00:02Z -C "$work/classes" .
""",
    )
    return struct(
        manifest = manifest,
        apk = apk,
        r_jar = r_jar,
        compile_sdk = _compile_sdk(sdk.android_jar),
    )

def _impl(target, ctx):
    own = _own_jars(target, ctx)
    if JavaInfo not in target:
        return [_OwnJarsInfo(jars = own)]
    if StarlarkAndroidResourcesInfo not in target:
        fail("compose-preview's Android aspect needs an Android target with resources: %s" % target.label)
    java = target[JavaInfo]
    project_jars = own.to_list()
    if not project_jars:
        fail("compose-preview target has no application runtime jars: %s" % target.label)
    resources = target[StarlarkAndroidResourcesInfo]
    linked = _link(ctx, target, resources)

    # The final-ID R classes must win over every library's non-final R classes.
    runtime = depset(
        [linked.r_jar] + project_jars,
        transitive = [java.transitive_runtime_jars],
        order = "preorder",
    )
    runtime_files = runtime.to_list()
    runtime_paths = {f.path: True for f in runtime_files}
    sources = [f for f in getattr(ctx.rule.files, "srcs", []) if f.extension in ["kt", "java"]]
    runfiles = target[DefaultInfo].default_runfiles.files
    android = {
        "packageName": resources.package,
        "resourceApk": linked.apk.path,
        "manifest": linked.manifest.path,
        "rJar": linked.r_jar.path,
    }
    if linked.compile_sdk:
        android["compileSdk"] = linked.compile_sdk
    metadata = ctx.actions.declare_file(target.label.name + ".compose-preview.json")
    ctx.actions.write(metadata, json.encode({
        "schemaVersion": 2,
        "target": str(target.label),
        # Includes Bazel's configured output-directory key; never combine configurations.
        "configuration": ctx.bin_dir.path,
        "projectJars": [f.path for f in project_jars],
        "runtimeJars": [f.path for f in runtime_files],
        "sourceFiles": [f.path for f in sources],
        "dataFiles": [f.path for f in runfiles.to_list() if f.path not in runtime_paths],
        "android": android,
    }))
    return [
        _OwnJarsInfo(jars = own),
        OutputGroupInfo(
            compose_preview_metadata = depset([metadata]),
            compose_preview_runtime = depset(
                sources + [linked.apk, linked.manifest],
                transitive = [runtime, runfiles],
            ),
        ),
    ]

# Propagates only along `exports`, so a kt_android_library (an android_library
# exporting the Kotlin classes) reports the Kotlin jars as its own. JavaInfo
# already carries the runtime closure.
compose_preview_android_aspect = aspect(
    implementation = _impl,
    attr_aspects = ["exports"],
    attrs = {
        # Compiles the generated R sources; the same pinned JDK tool.bzl defaults to.
        "_jdk": attr.label(
            default = "@rules_java//toolchains:remotejdk_17",
            providers = [JavaRuntimeInfo],
            cfg = "exec",
        ),
    },
    toolchains = [config_common.toolchain_type(ANDROID_SDK_TOOLCHAIN_TYPE, mandatory = True)],
)
