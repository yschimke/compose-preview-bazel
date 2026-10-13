"""Public Bazel provider; internal deployments may substitute this shim."""

load("@rules_java//java/common:java_info.bzl", _JavaInfo = "JavaInfo")

JavaInfo = _JavaInfo
