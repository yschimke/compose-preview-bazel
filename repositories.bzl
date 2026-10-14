"""Checksum-pinned CLI provisioning at repository fetch time, not render time."""

def _cli_repository_impl(ctx):
    ctx.download_and_extract(
        url = ctx.attr.urls,
        sha256 = ctx.attr.sha256,
        stripPrefix = ctx.attr.strip_prefix,
        output = "distribution",
    )
    ctx.file("BUILD.bazel", """load("@rules_compose_preview//:tool.bzl", "compose_preview_cli")
package(default_visibility = ["//visibility:public"])
compose_preview_cli(
    name = "cli",
    launcher = "distribution/bin/compose-preview",
    files = glob(["distribution/**"]),
)
""")

compose_preview_cli_repository = repository_rule(
    implementation = _cli_repository_impl,
    attrs = {
        "urls": attr.string_list(mandatory = True),
        "sha256": attr.string(mandatory = True),
        "strip_prefix": attr.string(mandatory = True),
    },
    doc = "Download a pinned compose-preview distribution; use_repo_rule exposes @<name>//:cli.",
)

def _producer_repository_impl(ctx):
    ctx.download_and_extract(
        url = ctx.attr.urls,
        sha256 = ctx.attr.sha256,
        stripPrefix = ctx.attr.strip_prefix,
        output = "distribution",
    )
    ctx.file("BUILD.bazel", """load("@rules_compose_preview//:tool.bzl", "compose_preview_producer")
load("@rules_java//java:defs.bzl", "java_import")
package(default_visibility = ["//visibility:public"])
java_import(name = "producer_runtime", jars = glob(["distribution/lib/*.jar"]))
compose_preview_producer(name = "producer", deps = [":producer_runtime"])
""")

compose_preview_producer_repository = repository_rule(
    implementation = _producer_repository_impl,
    attrs = {
        "urls": attr.string_list(mandatory = True),
        "sha256": attr.string(mandatory = True),
        "strip_prefix": attr.string(mandatory = True),
    },
    doc = "Fetch a pinned offline producer distribution; exposes @<name>//:producer.",
)
