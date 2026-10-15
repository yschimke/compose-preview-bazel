# Working in this repository

Use `agent/` branches, conventional commits and Yuri Schimke <yuri@schimke.ee>
for Git attribution. Do not add AI coauthor trailers.

Rules and their small packaging/test helpers live here. CLI discovery/rendering
lives in compose-ai-tools; real application examples live in compose-ai-contrib.
Do not copy renderer implementation into these rules.

Keep CLI and rule versions independently pinned. Without a registered render
toolchain, render actions run locally and uncached; only a render toolchain
(render_toolchain.bzl), which declares every renderer, Java, native, font and
resource input, makes them cacheable. Keep that the only route to caching.

Format Starlark with buildifier. Run the external-consumer integration test for
rule changes; unit-test packaging changes. Before pushing, fetch main and check
whether the current PR has merged; start a fresh branch if it has. Open or update
a PR for implementation changes. Merge only when the user authorizes it and the
required checks pass.
