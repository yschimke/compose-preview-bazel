"""A render toolchain: every input a Compose preview render needs, declared to the build.

Without a registered toolchain, compose_preview's render action provisions its inputs at run
time (the daemon jars from a GitHub release, Skiko's native library from Maven,
fonts from Google Fonts) into the host cache, so it must run locally, uncached and never remotely.

A toolchain names those inputs instead. compose_preview then stages them into the action,
turns the CLI's network off (COMPOSE_PREVIEW_NO_NETWORK), pins the JVM's locale, timezone and
encoding, and drops `local`/`no-cache`/`no-remote`, so a render is an ordinary cacheable,
remotely executable action.

Point each attribute at the same released artifacts the CLI would download, pinned by checksum
(an `http_file`/`http_archive` per artifact, or `maven_install`), or at vendored copies of them.

compose_preview renders Desktop (JVM) and Android targets. The Desktop attributes declare the
desktop daemon, renderer and Skiko native; the optional Android ones declare the Android daemon
(which carries the Robolectric renderer) and Robolectric's android-all-instrumented jars, the
pieces `compose-preview provision --android` fetches (compose-ai-tools' docs/OFFLINE.md). An
Android target's render is cacheable only when both Android attributes are set; otherwise it runs
locally and uncached, as a render without a toolchain does. android.jar comes from the target's
own Android SDK toolchain, which the link already uses.
"""

load(":java_info.bzl", "JDK_RUNTIME", "JavaRuntimeInfo")

ComposePreviewRenderInfo = provider(
    doc = "The declared inputs of a Compose preview render.",
    fields = {
        "java_runtime": "JavaRuntimeInfo the CLI and its daemons run on (Java 17+)",
        "desktop_daemon": "depset of the CLI's lib-daemon-desktop jars",
        "renderer": "depset of the CLI's lib-renderer jars",
        "skiko": "depset of the host's skiko-awt-runtime native jar",
        "fonts": "depset of pre-fetched GoogleFont files (the composeai fonts cache)",
        "android_daemon": "depset of the CLI's lib-daemon-android jars (the Robolectric renderer)",
        "android_all": "depset of Robolectric android-all-instrumented jars, one per rendered SDK",
    },
)

def _impl(ctx):
    java = ctx.attr.java_runtime[JavaRuntimeInfo]
    if java.version and java.version < 17:
        fail("compose_preview_render_toolchain requires a Java 17+ runtime")
    return [platform_common.ToolchainInfo(
        compose_preview_render = ComposePreviewRenderInfo(
            java_runtime = java,
            desktop_daemon = depset(ctx.files.desktop_daemon),
            renderer = depset(ctx.files.renderer),
            skiko = depset(ctx.files.skiko),
            fonts = depset(ctx.files.fonts),
            android_daemon = depset(ctx.files.android_daemon),
            android_all = depset(ctx.files.android_all),
        ),
    )]

compose_preview_render_toolchain = rule(
    implementation = _impl,
    attrs = {
        "java_runtime": attr.label(default = JDK_RUNTIME, providers = [JavaRuntimeInfo], cfg = "exec"),
        "desktop_daemon": attr.label_list(allow_files = [".jar"], cfg = "exec"),
        "renderer": attr.label_list(allow_files = [".jar"], cfg = "exec"),
        "skiko": attr.label_list(allow_files = [".jar"], cfg = "exec"),
        "fonts": attr.label_list(allow_files = True, cfg = "exec"),
        "android_daemon": attr.label_list(
            allow_files = [".jar"],
            cfg = "exec",
            doc = "Optional: the lib-daemon-android jars (compose-preview-android-daemon-<v>.zip). With android_all, makes Android renders cacheable.",
        ),
        "android_all": attr.label_list(
            allow_files = [".jar"],
            cfg = "exec",
            doc = "Optional: android-all-instrumented jars matching the Android daemon's Robolectric, for each SDK rendered.",
        ),
    },
    doc = "Declare a Compose preview render's inputs; register it with toolchain(toolchain_type = '@rules_compose_preview//:render_toolchain_type').",
)

RENDER_TOOLCHAIN_TYPE = Label(":render_toolchain_type")

def renders_android(info):
    """Whether a toolchain declares what an Android render needs, so it can be cached.

    Args:
      info: the registered toolchain's ComposePreviewRenderInfo.

    Returns:
      True when both the Android daemon and the android-all jars are declared.
    """
    return bool(info.android_daemon) and bool(info.android_all)

def render_inputs(info, maven_jars = None, stage_maven = None, lockfile = None, android = False):
    """The render's declared inputs, and the shell that stages them and points the CLI at them.

    Args:
      info: the registered toolchain's ComposePreviewRenderInfo.
      maven_jars: File listing the bundle's runtime jars, one execroot path per line, or None.
        They are laid out as a local Maven repository (stage_maven.sh), so the bundle's Maven
        coordinates resolve without a fetch.
      stage_maven: the stage_maven.sh File; required with maven_jars.
      lockfile: the rules_jvm_external lockfile the bundle's coordinates came from, or None. Jars
        are matched to its pins by file name, which holds wherever the jar is kept.
      android: stage the Android daemon and android-all jars (an Android target) instead of the
        desktop daemon, renderer and Skiko native, so each render depends only on its own inputs.

    Returns:
      (inputs depset, setup shell lines). The lines run in the action's working directory, lay
      the inputs out as the CLI's own cache and sidecar directories under a fresh temporary
      directory, and export the environment that makes the CLI read them, refuse any fetch, and
      render the same on every worker. That directory is not a declared output: an EXIT trap
      removes it, so the caller must run its command as a child of the shell, not `exec` it.
    """
    if android:
        groups = [
            ("lib-daemon-android", info.android_daemon),
            ("robolectric-deps", info.android_all),
        ]
    else:
        groups = [
            ("lib-daemon-desktop", info.desktop_daemon),
            ("lib-renderer", info.renderer),
            ("skiko", info.skiko),
        ]
    groups.append(("cache/composeai/fonts", info.fonts))
    lines = [
        # Private to this action, so concurrent renders never touch each other's inputs, and
        # never inside the output tree, where only declared outputs may be written.
        'stage="$(mktemp -d "${TMPDIR:-/tmp}/compose-preview-stage.XXXXXX")"',
        "trap 'rm -rf \"$stage\"' EXIT",
    ]
    for name, files in groups:
        lines.append('mkdir -p "$stage/%s"' % name)
        for f in files.to_list():
            lines.append('ln -sf "$PWD/%s" "$stage/%s/%s"' % (f.path, name, f.basename))
    if maven_jars:
        lines.append('sh "%s" "$stage/m2" "%s"%s' % (
            stage_maven.path,
            maven_jars.path,
            ' "%s"' % lockfile.path if lockfile else "",
        ))
    options = [
        "-Dcomposeai.network=off",
        "-Dmaven.repo.local=$stage/m2",
    ]
    if android:
        options += [
            "-Dcomposeai.cli.libDaemonAndroidDir=$stage/lib-daemon-android",
            # The daemon sidecar carries the one-shot Robolectric renderer `bundle render` runs;
            # CLIs that predate looking for it there read only this property.
            "-Dcomposeai.cli.libRendererAndroidDir=$stage/lib-daemon-android",
        ]
    else:
        options += [
            "-Dcomposeai.cli.libDaemonDesktopDir=$stage/lib-daemon-desktop",
            "-Dcomposeai.cli.libRendererDir=$stage/lib-renderer",
            "-Dcomposeai.cli.skikoDir=$stage/skiko",
        ]
    options += [
        # Host defaults must not reach a cached image.
        "-Duser.timezone=UTC",
        "-Duser.language=en",
        "-Duser.country=US",
        "-Dfile.encoding=UTF-8",
    ]
    lines += [
        'export JAVA_HOME="$PWD/%s"' % info.java_runtime.java_home,
        'export PATH="$JAVA_HOME/bin:/usr/bin:/bin"',
        'export HOME="$stage/home"',
        'mkdir -p "$HOME"',
        'export XDG_CACHE_HOME="$stage/cache"',
        "export COMPOSE_PREVIEW_NO_NETWORK=1",
        "export COMPOSE_PREVIEW_OFFLINE=1",
    ]
    if android:
        # Robolectric reads android-all from here (robolectric.offline) instead of Maven Central.
        lines.append('export COMPOSE_PREVIEW_ROBOLECTRIC_DEPS_DIR="$stage/robolectric-deps"')
    lines += [
        # Inherited by the daemons the CLI starts, which do not see its JAVA_OPTS.
        "export TZ=UTC",
        "export LANG=C.UTF-8",
        "export LC_ALL=C.UTF-8",
        'export JAVA_OPTS="%s"' % " ".join(options),
    ]
    inputs = [info.java_runtime.files] + [files for _, files in groups]
    direct = [f for f in [maven_jars, stage_maven, lockfile] if f]
    return depset(direct, transitive = inputs), lines
