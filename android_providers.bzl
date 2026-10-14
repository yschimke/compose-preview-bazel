"""Public rules_android providers; internal deployments may substitute this shim.

Only these two symbols tie android.bzl to rules_android: the resources provider an
android_library returns, and the Android SDK toolchain type that supplies aapt2
and android.jar.
"""

load("@rules_android//providers:providers.bzl", _StarlarkAndroidResourcesInfo = "StarlarkAndroidResourcesInfo")

StarlarkAndroidResourcesInfo = _StarlarkAndroidResourcesInfo

ANDROID_SDK_TOOLCHAIN_TYPE = Label("@rules_android//toolchains/android_sdk:toolchain_type")
