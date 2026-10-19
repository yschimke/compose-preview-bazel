"""Analysis tests for compose_preview: attribute validation and render cacheability."""

load("@rules_testing//lib:analysis_test.bzl", "analysis_test", "test_suite")
load("@rules_testing//lib:truth.bzl", "matching")
load("@rules_testing//lib:util.bzl", "util")
load("//:android.bzl", "ComposePreviewAndroidInfo", "compose_preview_android_module_aspect")
load("//:defs.bzl", "compose_preview")

def _preview(name, module = ":module", **kwargs):
    util.helper_target(
        compose_preview,
        name = name,
        module = module,
        producer = ":producer",
        **kwargs
    )

def _failure_test(name, impl, outputs, cli = None):
    _preview(name = name + "_subject", outputs = outputs, cli = cli)
    analysis_test(name = name, impl = impl, target = name + "_subject", expect_failure = True)

def _expect_failure(env, target, message):
    env.expect.that_target(target).failures().contains_predicate(
        matching.custom("contains '%s'" % message, lambda failure: message in failure),
    )

def _test_rejects_unknown_outputs(name):
    _failure_test(name, _rejects_unknown_outputs_impl, ["bundle", "pdf"])

def _rejects_unknown_outputs_impl(env, target):
    _expect_failure(env, target, "supported outputs are bundle, png and rendered_bundle")

def _test_rejects_duplicate_outputs(name):
    _failure_test(name, _rejects_duplicate_or_empty_outputs_impl, ["bundle", "bundle"])

def _test_rejects_empty_outputs(name):
    _failure_test(name, _rejects_duplicate_or_empty_outputs_impl, [])

def _rejects_duplicate_or_empty_outputs_impl(env, target):
    _expect_failure(env, target, "outputs must be a nonempty list of distinct output kinds")

def _test_png_requires_cli(name):
    _failure_test(name, _png_requires_cli_impl, ["png"])

def _png_requires_cli_impl(env, target):
    _expect_failure(env, target, "png and rendered_bundle require cli")

# Starlark's Action exposes no execution requirements, so these assert which of compose_preview's
# two render branches ran; tests/test_analysis_aquery.py checks the requirements themselves
# with aquery, against the same targets.
def _mnemonics(target):
    return [a.mnemonic for a in target.actions]

def _test_toolchain_render_is_cacheable(name):
    _preview(name = name + "_subject", cli = ":cli", outputs = ["png"])
    analysis_test(
        name = name,
        impl = _test_toolchain_render_is_cacheable_impl,
        target = name + "_subject",
        config_settings = {
            "//command_line_option:extra_toolchains": [str(Label(":render_toolchain"))],
        },
    )

def _test_toolchain_render_is_cacheable_impl(env, target):
    mnemonics = env.expect.that_collection(_mnemonics(target))
    mnemonics.contains("ComposePreviewRender")
    mnemonics.not_contains("ComposePreviewLocalRender")

def _test_render_without_toolchain_is_local(name):
    _preview(name = name + "_subject", cli = ":cli", outputs = ["png"])
    analysis_test(
        name = name,
        impl = _test_render_without_toolchain_is_local_impl,
        target = name + "_subject",
    )

def _test_render_without_toolchain_is_local_impl(env, target):
    mnemonics = env.expect.that_collection(_mnemonics(target))
    mnemonics.contains("ComposePreviewLocalRender")
    mnemonics.not_contains("ComposePreviewRender")

def _test_cli_may_be_an_alias(name):
    # As tests/integration.py and a CLI from compose_preview_cli_repository wire it.
    util.helper_target(native.alias, name = name + "_cli", actual = ":cli")
    _preview(name = name + "_subject", cli = name + "_cli", outputs = ["bundle"])
    analysis_test(name = name, impl = _test_cli_may_be_an_alias_impl, target = name + "_subject")

def _test_cli_may_be_an_alias_impl(env, target):
    env.expect.that_target(target).default_outputs().contains_predicate(
        matching.file_basename_equals(target.label.name + ".bundle.png"),
    )

# Android modules: compose_preview's module aspect links them, the bundle is schema 2, and the
# render is cacheable only with a toolchain that declares the Android daemon and android-all.
_ANDROID_SDK = str(Label(":android_sdk_toolchain"))
_ANDROID_RENDER = str(Label(":android_render_toolchain"))
_DESKTOP_RENDER = str(Label(":render_toolchain"))

def _render_action(env, target):
    actions = [a for a in target.actions if a.mnemonic in ("ComposePreviewRender", "ComposePreviewLocalRender")]
    env.expect.that_int(len(actions)).equals(1)
    return actions[0]

def _android_test(name, impl, toolchains, **kwargs):
    _preview(name = name + "_subject", module = ":android_module", cli = ":cli", outputs = ["png"], **kwargs)
    analysis_test(
        name = name,
        impl = impl,
        target = name + "_subject",
        config_settings = {"//command_line_option:extra_toolchains": [_ANDROID_SDK] + toolchains},
    )

def _test_android_render_with_android_toolchain_is_cacheable(name):
    _android_test(name, _android_render_with_android_toolchain_is_cacheable_impl, [_ANDROID_RENDER])

def _android_render_with_android_toolchain_is_cacheable_impl(env, target):
    mnemonics = env.expect.that_collection(_mnemonics(target))
    mnemonics.contains("ComposePreviewRender")
    mnemonics.not_contains("ComposePreviewLocalRender")
    action = _render_action(env, target)
    argv = env.expect.that_collection(action.argv)
    argv.contains_at_least(["--android-sdk", "36", "--clock-packages", "com.example.ui.,androidx.wear.compose."])
    argv.contains_predicate(matching.str_endswith("android-36/android.jar"))

    # The recorded coordinates resolve from the module's own runtime jars, by content.
    argv.contains("--local-jars")

    # Only the Android inputs are staged; the Desktop daemon is not an input of this action.
    inputs = [f.short_path for f in action.inputs.to_list()]
    env.expect.that_collection(inputs).contains_predicate(matching.str_endswith("android/0000-daemon.jar"))
    env.expect.that_collection(inputs).contains_predicate(
        matching.str_endswith("android-all-instrumented-16-robolectric-13921718-i7.jar"),
    )

def _test_android_render_with_desktop_toolchain_is_local(name):
    # A toolchain without the Android inputs falls back exactly as no toolchain does.
    _android_test(name, _android_render_is_local_impl, [_DESKTOP_RENDER])

def _test_android_render_without_toolchain_is_local(name):
    _android_test(name, _android_render_is_local_impl, [])

def _android_render_is_local_impl(env, target):
    mnemonics = env.expect.that_collection(_mnemonics(target))
    mnemonics.contains("ComposePreviewLocalRender")
    mnemonics.not_contains("ComposePreviewRender")
    argv = env.expect.that_collection(_render_action(env, target).argv)
    argv.contains_at_least(["--android-sdk", "36"])

    # The local render declares no runtime jars, so it names none.
    argv.not_contains("--local-jars")

def _test_android_app_clock_can_be_turned_off(name):
    _android_test(name, _android_app_clock_off_impl, [_ANDROID_RENDER], pin_app_clock = False)

def _android_app_clock_off_impl(env, target):
    env.expect.that_collection(_render_action(env, target).argv).not_contains("--clock-packages")

def _test_android_app_clock_packages_override_the_package(name):
    _android_test(
        name,
        _android_app_clock_packages_impl,
        [_ANDROID_RENDER],
        app_clock_packages = ["com.example.app.", "com.example.lib."],
    )

def _android_app_clock_packages_impl(env, target):
    env.expect.that_collection(_render_action(env, target).argv).contains_at_least(
        ["--clock-packages", "com.example.app.,com.example.lib."],
    )

def _test_android_bundle_carries_linked_resources(name):
    _android_test(name, _android_bundle_carries_linked_resources_impl, [])

def _android_bundle_carries_linked_resources_impl(env, target):
    prepare = [a for a in target.actions if a.mnemonic == "ComposePreviewPrepare"]
    env.expect.that_int(len(prepare)).equals(1)
    inputs = [f.short_path for f in prepare[0].inputs.to_list()]
    for suffix in ("resources.ap_", "r-final.jar", "AndroidManifest.xml", ".artifacts.json"):
        env.expect.that_collection(inputs).contains_predicate(matching.str_endswith(suffix))

def _test_android_wrapper_reports_exported_sources(name):
    # compose-ai-tools#6112: a wrapper that only re-exports its implementation reports that
    # library's sources as its own, as it already did its jars.
    analysis_test(
        name = name,
        impl = _android_wrapper_reports_exported_sources_impl,
        target = ":android_wrapper",
        config_settings = {"//command_line_option:extra_toolchains": [_ANDROID_SDK]},
        extra_target_under_test_aspects = [compose_preview_android_module_aspect],
    )

def _android_wrapper_reports_exported_sources_impl(env, target):
    info = target[ComposePreviewAndroidInfo]
    env.expect.that_collection([f.short_path for f in info.sources]).contains_exactly(["tests/analysis/Module.java"])
    env.expect.that_collection([f.short_path for f in info.project_jars]).contains_predicate(
        matching.str_endswith("libmodule.jar"),
    )

def _test_android_preview_of_a_wrapper_carries_exported_sources(name):
    # compose_preview(module = wrapper) without `srcs`: the metadata's sources, and the prepare
    # action's inputs, are the exported library's.
    _preview(name = name + "_subject", module = ":android_wrapper", cli = ":cli", outputs = ["png"])
    analysis_test(
        name = name,
        impl = _android_preview_of_a_wrapper_carries_exported_sources_impl,
        target = name + "_subject",
        config_settings = {"//command_line_option:extra_toolchains": [_ANDROID_SDK]},
    )

def _android_preview_of_a_wrapper_carries_exported_sources_impl(env, target):
    prepare = [a for a in target.actions if a.mnemonic == "ComposePreviewPrepare"]
    env.expect.that_int(len(prepare)).equals(1)
    inputs = [f.short_path for f in prepare[0].inputs.to_list()]
    env.expect.that_collection(inputs).contains("tests/analysis/Module.java")

def _test_desktop_render_ignores_android_inputs(name):
    # A toolchain that also declares Android inputs leaves a Desktop render exactly as before.
    _preview(name = name + "_subject", cli = ":cli", outputs = ["png"])
    analysis_test(
        name = name,
        impl = _desktop_render_ignores_android_inputs_impl,
        target = name + "_subject",
        config_settings = {"//command_line_option:extra_toolchains": [_ANDROID_RENDER]},
    )

def _desktop_render_ignores_android_inputs_impl(env, target):
    action = _render_action(env, target)
    env.expect.that_str(action.mnemonic).equals("ComposePreviewRender")
    argv = env.expect.that_collection(action.argv)
    argv.not_contains("--android-jar")
    argv.not_contains("--clock-packages")
    inputs = [f.short_path for f in action.inputs.to_list()]
    env.expect.that_collection(inputs).not_contains_predicate(matching.str_endswith("android/0000-daemon.jar"))

def _launcher(env, target):
    writes = [a for a in target.actions if a.mnemonic == "FileWrite" and a.outputs.to_list()[0].basename.endswith(".sh")]
    env.expect.that_int(len(writes)).equals(1)
    return writes[0]

def _test_android_launcher_passes_render_flags(name):
    _android_test(name, _android_launcher_passes_render_flags_impl, [_ANDROID_RENDER], app_compose = "daemon")

def _android_launcher_passes_render_flags_impl(env, target):
    # `bazel run` renders with the flags the build action uses, android.jar from the runfiles.
    content = env.expect.that_str(_launcher(env, target).content)
    content.contains("--android-jar \"$(resolve '")
    content.contains("android-36/android.jar')\"")
    content.contains("--android-sdk '36'")
    content.contains("--clock-packages 'com.example.ui.,androidx.wear.compose.'")
    content.contains("--app-compose 'daemon'")
    runfiles = [f.short_path for f in target[DefaultInfo].default_runfiles.files.to_list()]
    env.expect.that_collection(runfiles).contains_predicate(matching.str_endswith("android-36/android.jar"))
    env.expect.that_collection(_render_action(env, target).argv).contains_at_least(["--app-compose", "daemon"])

def _test_android_app_compose_defaults_to_the_cli(name):
    _android_test(name, _android_app_compose_defaults_to_the_cli_impl, [_ANDROID_RENDER])

def _android_app_compose_defaults_to_the_cli_impl(env, target):
    env.expect.that_collection(_render_action(env, target).argv).not_contains("--app-compose")
    env.expect.that_bool("--app-compose" in _launcher(env, target).content).equals(False)

def _test_app_compose_is_android_only(name):
    _preview(name = name + "_subject", cli = ":cli", outputs = ["png"], app_compose = "app")
    analysis_test(
        name = name,
        impl = _app_compose_is_android_only_impl,
        target = name + "_subject",
        expect_failure = True,
    )

def _app_compose_is_android_only_impl(env, target):
    _expect_failure(env, target, "app_compose applies to Android modules only")

def _test_desktop_launcher_passes_no_render_flags(name):
    _preview(name = name + "_subject", cli = ":cli", outputs = ["png"])
    analysis_test(name = name, impl = _desktop_launcher_passes_no_render_flags_impl, target = name + "_subject")

def _desktop_launcher_passes_no_render_flags_impl(env, target):
    content = _launcher(env, target).content
    env.expect.that_str(content).contains("render_flags=()")
    env.expect.that_bool("--android-jar" in content).equals(False)

def compose_preview_test_suite(name):
    test_suite(
        name = name,
        tests = [
            _test_rejects_unknown_outputs,
            _test_rejects_duplicate_outputs,
            _test_rejects_empty_outputs,
            _test_png_requires_cli,
            _test_toolchain_render_is_cacheable,
            _test_render_without_toolchain_is_local,
            _test_cli_may_be_an_alias,
            _test_android_render_with_android_toolchain_is_cacheable,
            _test_android_render_with_desktop_toolchain_is_local,
            _test_android_render_without_toolchain_is_local,
            _test_android_app_clock_can_be_turned_off,
            _test_android_app_clock_packages_override_the_package,
            _test_android_bundle_carries_linked_resources,
            _test_android_wrapper_reports_exported_sources,
            _test_android_preview_of_a_wrapper_carries_exported_sources,
            _test_desktop_render_ignores_android_inputs,
            _test_android_launcher_passes_render_flags,
            _test_android_app_compose_defaults_to_the_cli,
            _test_app_compose_is_android_only,
            _test_desktop_launcher_passes_no_render_flags,
        ],
    )
