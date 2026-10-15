"""Analysis tests for compose_preview: attribute validation and render cacheability."""

load("@rules_testing//lib:analysis_test.bzl", "analysis_test", "test_suite")
load("@rules_testing//lib:truth.bzl", "matching")
load("@rules_testing//lib:util.bzl", "util")
load("//:defs.bzl", "compose_preview")

def _preview(name, **kwargs):
    util.helper_target(
        compose_preview,
        name = name,
        module = ":module",
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
        ],
    )
