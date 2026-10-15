# Changelog

## [0.1.0-rc.8](https://github.com/yschimke/compose-preview-bazel/compare/v0.1.0-rc.7...v0.1.0-rc.8) (2026-10-15)


### Features

* artifacts name the target that produced each runtime jar ([#16](https://github.com/yschimke/compose-preview-bazel/issues/16)) ([82ec678](https://github.com/yschimke/compose-preview-bazel/commit/82ec6784827d85617cda54d39fe199ac83eb6f55))

## [0.1.0-rc.7](https://github.com/yschimke/compose-preview-bazel/compare/v0.1.0-rc.6...v0.1.0-rc.7) (2026-10-15)


### Features

* a render toolchain makes compose_preview renders cacheable and remote-executable ([#12](https://github.com/yschimke/compose-preview-bazel/issues/12)) ([ff1d5b7](https://github.com/yschimke/compose-preview-bazel/commit/ff1d5b7376cf1ba3979f9c66ed2b448c9cb1f862))


### Bug Fixes

* stage render jars by the lockfile's pins, not their path ([#15](https://github.com/yschimke/compose-preview-bazel/issues/15)) ([851202d](https://github.com/yschimke/compose-preview-bazel/commit/851202db8e8d0b0edb89466149ce0246905ef43a))
* stage rules_jvm_external 6+ jars for the render toolchain ([#14](https://github.com/yschimke/compose-preview-bazel/issues/14)) ([25c1653](https://github.com/yschimke/compose-preview-bazel/commit/25c1653b4b9b6d99898a5984df6493e2ffb8ed48))

## [0.1.0-rc.6](https://github.com/yschimke/compose-preview-bazel/compare/v0.1.0-rc.5...v0.1.0-rc.6) (2026-10-15)


### Bug Fixes

* link Android resources once for the selected target, portable outside stock Bazel ([#9](https://github.com/yschimke/compose-preview-bazel/issues/9)) ([d24e483](https://github.com/yschimke/compose-preview-bazel/commit/d24e4836ea5e9d852738ecb46faf4b7de1c6437c))

## [0.1.0-rc.5](https://github.com/yschimke/compose-preview-bazel/compare/v0.1.0-rc.4...v0.1.0-rc.5) (2026-10-15)


### Features

* add an Android aspect emitting artifacts schema 2 ([#2](https://github.com/yschimke/compose-preview-bazel/issues/2)) ([844f5e4](https://github.com/yschimke/compose-preview-bazel/commit/844f5e4a1b3d7f6231f40500491e21723636afc1))
* add cacheable Compose source bundles and optional rendering ([#1](https://github.com/yschimke/compose-preview-bazel/issues/1)) ([078c0b0](https://github.com/yschimke/compose-preview-bazel/commit/078c0b0724b64a3e00aaf557fc5ae919c2c2abb5))
* export each Android target's resource link inputs and share link.sh ([#8](https://github.com/yschimke/compose-preview-bazel/issues/8)) ([86f80ec](https://github.com/yschimke/compose-preview-bazel/commit/86f80ecf0129123763d3c80d44865d651f2d3248))
* pack transitive Android assets into the linked resource APK ([#4](https://github.com/yschimke/compose-preview-bazel/issues/4)) ([12af1db](https://github.com/yschimke/compose-preview-bazel/commit/12af1db9ba3dfdad3b3eead95db7922f3fd88990))


### Bug Fixes

* order Android resource overlays dependencies-first ([#3](https://github.com/yschimke/compose-preview-bazel/issues/3)) ([e85e351](https://github.com/yschimke/compose-preview-bazel/commit/e85e351e2d8ffad8b8f009458d25714f24a4bdc3))
