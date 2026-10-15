"""A render toolchain: every input a Compose preview render needs, declared to the build.

Without a registered toolchain, compose_preview's render action provisions its inputs at run
time (the daemon jars from a GitHub release, Skiko's native library and android-all from Maven,
fonts from Google Fonts) into the host cache, so it must run locally, uncached and never remotely.

A toolchain names those inputs instead. compose_preview then stages them into the action,
turns the CLI's network off (COMPOSE_PREVIEW_NO_NETWORK) and drops `local`/`no-cache`/
`no-remote`, so a render is an ordinary cacheable, remotely executable action.

Point each attribute at the same released artifacts the CLI would download, pinned by checksum
(an `http_file`/`http_archive` per artifact, or `maven_install`), or at vendored copies of
them. Robolectric and the android-all-instrumented jars must come from the same Robolectric
build as the Android daemon jars: android-all instrumented by another Robolectric breaks the
sandbox in ways that do not fail loudly.
"""

load(":java_info.bzl", "JDK_RUNTIME", "JavaRuntimeInfo")

ComposePreviewRenderInfo = provider(
    doc = "The declared inputs of a Compose preview render.",
    fields = {
        "java_runtime": "JavaRuntimeInfo the CLI and its daemons run on (Java 17+)",
        "desktop_daemon": "depset of the CLI's lib-daemon-desktop jars",
        "renderer": "depset of the CLI's lib-renderer jars",
        "android_daemon": "depset of the CLI's lib-daemon-android jars",
        "android_renderer": "depset of the CLI's lib-renderer-android jars",
        "skiko": "depset of the host's skiko-awt-runtime native jar",
        "robolectric_deps": "depset of android-all-instrumented jars, named as Maven names them",
        "fonts": "depset of pre-fetched GoogleFont files (the composeai fonts cache)",
        "android_jar": "File: the SDK platform's android.jar, or None",
        "android_api": "int: the API level of android_jar, or 0",
    },
)

def _impl(ctx):
    java = ctx.attr.java_runtime[JavaRuntimeInfo]
    if java.version and java.version < 17:
        fail("compose_preview_render_toolchain requires a Java 17+ runtime")
    if ctx.file.android_jar and not ctx.attr.android_api:
        fail("android_api is required with android_jar")
    return [platform_common.ToolchainInfo(
        compose_preview_render = ComposePreviewRenderInfo(
            java_runtime = java,
            desktop_daemon = depset(ctx.files.desktop_daemon),
            renderer = depset(ctx.files.renderer),
            android_daemon = depset(ctx.files.android_daemon),
            android_renderer = depset(ctx.files.android_renderer),
            skiko = depset(ctx.files.skiko),
            robolectric_deps = depset(ctx.files.robolectric_deps),
            fonts = depset(ctx.files.fonts),
            android_jar = ctx.file.android_jar,
            android_api = ctx.attr.android_api,
        ),
    )]

compose_preview_render_toolchain = rule(
    implementation = _impl,
    attrs = {
        "java_runtime": attr.label(default = JDK_RUNTIME, providers = [JavaRuntimeInfo], cfg = "exec"),
        "desktop_daemon": attr.label_list(allow_files = [".jar"], cfg = "exec"),
        "renderer": attr.label_list(allow_files = [".jar"], cfg = "exec"),
        "android_daemon": attr.label_list(allow_files = [".jar"], cfg = "exec"),
        "android_renderer": attr.label_list(allow_files = [".jar"], cfg = "exec"),
        "skiko": attr.label_list(allow_files = [".jar"], cfg = "exec"),
        "robolectric_deps": attr.label_list(allow_files = [".jar"], cfg = "exec"),
        "fonts": attr.label_list(allow_files = True, cfg = "exec"),
        "android_jar": attr.label(allow_single_file = [".jar"], cfg = "exec"),
        "android_api": attr.int(doc = "API level of android_jar; names its platforms/android-<api> directory."),
    },
    doc = "Declare a Compose preview render's inputs; register it with toolchain(toolchain_type = '@rules_compose_preview//:render_toolchain_type').",
)

RENDER_TOOLCHAIN_TYPE = Label(":render_toolchain_type")

def render_inputs(info, maven_jars = None):
    """The render's declared inputs, and the shell that stages them and points the CLI at them.

    Args:
      info: the registered toolchain's ComposePreviewRenderInfo.
      maven_jars: File listing the bundle's runtime jars, one execroot path per line, or None.
        Those under a rules_jvm_external `v1/<scheme>/<host>/` tree are linked into a local Maven
        repository, so the bundle's Maven coordinates resolve without a fetch.

    Returns:
      (inputs depset, setup shell lines). The lines run in the action's working directory, lay
      the inputs out as the CLI's own cache and sidecar directories under
      `_compose_preview_render`, and export the environment that makes the CLI read them and
      refuse any fetch.
    """
    groups = [
        ("lib-daemon-desktop", info.desktop_daemon),
        ("lib-renderer", info.renderer),
        ("lib-daemon-android", info.android_daemon),
        ("lib-renderer-android", info.android_renderer),
        ("skiko", info.skiko),
        ("robolectric-deps", info.robolectric_deps),
        ("cache/composeai/fonts", info.fonts),
    ]
    lines = [
        'stage="$PWD/_compose_preview_render"',
        'rm -rf "$stage"',
    ]
    for name, files in groups:
        lines.append('mkdir -p "$stage/%s"' % name)
        for f in files.to_list():
            lines.append('ln -sf "$PWD/%s" "$stage/%s/%s"' % (f.path, name, f.basename))
    if maven_jars:
        # rules_jvm_external keeps each jar at v1/<scheme>/<host>/<repository path>/<group>/
        # <artifact>/<version>/<file>. The repository path is unknown, so link every suffix that
        # still holds a group, artifact, version and file; the resolver finds the one that fits.
        # Its jvm_import stamps each jar as processed_<file> beside the original name's place.
        lines += [
            'mkdir -p "$stage/m2"',
            "while IFS= read -r jar; do",
            '  case "$jar" in */v1/*/*) ;; *) continue ;; esac',
            '  rest="${jar#*/v1/*/}"; rest="${rest#*/}"',
            '  base="${rest##*/}"; base="${base#processed_}"; dir="${rest%/*}"',
            "  while :; do",
            '    mkdir -p "$stage/m2/$dir"',
            '    ln -sf "$PWD/$jar" "$stage/m2/$dir/$base"',
            '    case "$dir" in */*/*/*) dir="${dir#*/}" ;; *) break ;; esac',
            "  done",
            'done < "%s"' % maven_jars.path,
            'echo "compose_preview: staged $(find "$stage/m2" -name "*.jar" | wc -l) Maven jar links" >&2',
        ]
    options = [
        "-Dcomposeai.network=off",
        "-Dmaven.repo.local=$stage/m2",
        "-Dcomposeai.cli.libDaemonDesktopDir=$stage/lib-daemon-desktop",
        "-Dcomposeai.cli.libRendererDir=$stage/lib-renderer",
        "-Dcomposeai.cli.libDaemonAndroidDir=$stage/lib-daemon-android",
        "-Dcomposeai.cli.libRendererAndroidDir=$stage/lib-renderer-android",
        "-Dcomposeai.cli.skikoDir=$stage/skiko",
    ]
    lines += [
        'export JAVA_HOME="$PWD/%s"' % info.java_runtime.java_home,
        'export PATH="$JAVA_HOME/bin:/usr/bin:/bin"',
        'export HOME="$stage/home"',
        'mkdir -p "$HOME"',
        'export XDG_CACHE_HOME="$stage/cache"',
        "export COMPOSE_PREVIEW_NO_NETWORK=1",
        "export COMPOSE_PREVIEW_OFFLINE=1",
        'export COMPOSE_PREVIEW_ROBOLECTRIC_DEPS_DIR="$stage/robolectric-deps"',
        'export JAVA_OPTS="%s"' % " ".join(options),
    ]
    inputs = [info.java_runtime.files] + [files for _, files in groups]
    direct = [maven_jars] if maven_jars else []
    if info.android_jar:
        platform = "$stage/sdk/platforms/android-%d" % info.android_api
        lines += [
            'mkdir -p "%s"' % platform,
            'ln -sf "$PWD/%s" "%s/android.jar"' % (info.android_jar.path, platform),
            'export ANDROID_HOME="$stage/sdk"',
        ]
        direct.append(info.android_jar)
    return depset(direct, transitive = inputs), lines
