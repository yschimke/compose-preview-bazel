# Working in this repository

Use `agent/` branches, conventional commits and Yuri Schimke <yuri@schimke.ee>
for Git attribution. Do not add AI coauthor trailers.

Rules and their small packaging/test helpers live here. CLI discovery/rendering
lives in compose-ai-tools; real application examples live in compose-ai-contrib.
Do not copy renderer implementation into these rules.

Keep CLI and rule versions independently pinned. Render actions currently run
locally and uncached; do not advertise remote/hermetic caching until all renderer,
Java/native/font/resource inputs are declared and tested.

Format Starlark with buildifier. Run the external-consumer integration test for
rule changes; unit-test packaging changes. Before pushing, fetch main and check
whether the current PR has merged; start a fresh branch if it has. Open or update
a PR for implementation changes. Merge only when the user authorizes it and the
required checks pass.
