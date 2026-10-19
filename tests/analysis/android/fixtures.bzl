"""Stand-ins for an rules_android library and Android SDK toolchain, for analysis only.

compose_preview's module aspect reads only public providers: the AndroidIdeInfo an
android_library advertises, its StarlarkAndroidResourcesInfo and JavaInfo, and the SDK
toolchain's aapt2 and android.jar. These return exactly those, so the Android render path is
analysed without an Android SDK, which the analysis job does not install.
"""

load("@rules_android//providers:providers.bzl", "AndroidIdeInfo", "StarlarkAndroidResourcesInfo")
load("@rules_java//java/common:java_info.bzl", "JavaInfo")

def _library_impl(ctx):
    node = struct(
        label = ctx.label,
        compiled_resources = ctx.file.compiled_resources,
        manifest = ctx.file.manifest,
        assets = depset(),
        assets_dir = None,
    )
    return [
        ctx.attr.java[JavaInfo],
        AndroidIdeInfo(java_package = ctx.attr.package),
        StarlarkAndroidResourcesInfo(
            direct_resources_nodes = depset([node]),
            direct_compiled_resources = depset([ctx.file.compiled_resources]),
            transitive_compiled_resources = depset(),
            packages_to_r_txts = {},
            package = ctx.attr.package,
        ),
    ]

fake_android_library = rule(
    implementation = _library_impl,
    attrs = {
        "java": attr.label(providers = [JavaInfo]),
        "compiled_resources": attr.label(allow_single_file = True),
        "manifest": attr.label(allow_single_file = True),
        "package": attr.string(),
        # Read by the aspects as android_library's own: srcs for sourceFiles, exports for the
        # libraries a wrapper (kt_android_library, a macro's umbrella target) re-exports.
        "srcs": attr.label_list(allow_files = True),
        "exports": attr.label_list(),
    },
    provides = [AndroidIdeInfo, JavaInfo, StarlarkAndroidResourcesInfo],
)

def _sdk_impl(ctx):
    return [platform_common.ToolchainInfo(android_sdk_info = struct(
        aapt2 = ctx.attr.aapt2[DefaultInfo].files_to_run,
        android_jar = ctx.file.android_jar,
    ))]

fake_android_sdk = rule(
    implementation = _sdk_impl,
    attrs = {
        "aapt2": attr.label(executable = True, cfg = "exec"),
        "android_jar": attr.label(allow_single_file = True),
    },
)
