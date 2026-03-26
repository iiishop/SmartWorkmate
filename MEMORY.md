# MEMORY

- Project was reset on branch `refactor` and rebuilt from zero baseline.
- Current direction is ASL-first acceptance pipeline: ASL -> AST -> semantic checks -> IR -> pytest adapter -> unified verdict.
- DSL v0.1 decisions: statement-level `AND` is non-short-circuit, performance syntax is `p_ms(n[,m])` and `p95_ms(n[,m])`, and every `expect` statement must end with `;`.
- Performance binding rule: one `expect` statement must map performance checks to one function-call context; multiple targets must be split into separate statements.
- Architecture decision: acceptance translator is one subsystem of a larger program and now lives under `smartworkmate/acceptance_spec/` with a flat module layout (`ast/parser/semantic/ir/pytest_codegen/pytest_runner/verdict_schema`).
- Environment decision: use `uv` for Python workflow (`uv run ...`) and keep runtime package wiring compatible with uv-managed virtual environments.
- DSL lexer rule: keywords are case-insensitive (`using/test/given/expect/and/true/false`) while user-defined identifiers remain case-sensitive.
- Reporting decision: AI-facing output defaults to compact `verdict.lvf`; verbose `verdict.json` is optional via explicit flag.
- Pytest integration now supports JUnit XML parsing for statement-level status mapping (`test_statement_<n>` -> verdict statement index).
- Builtin framework introduced with registry + category model (`smartworkmate.acceptance_spec.builtins`); builtins are namespaced with `$` (e.g. `$p_ms`, `$p95_ms`, `$multiset`).
- Alias conflict policy: test aliases cannot collide with builtin names (including `$`-stripped forms like `p_ms`) to prevent ambiguity.
- Added CLI entrypoint `smartworkmate-acceptance` to compile ASL and run pytest, defaulting to compact `verdict.lvf` with optional `--include-json`.
- Generated pytest resolver now supports dotted target paths that traverse class attributes (e.g. `algo.Algo.sort_non_decreasing`).
- Codegen optimization: perf sampling logic now routes through shared `_measure()` helper, `given` variables are injected per-statement minimum, and builtin helpers are emitted only when referenced in generated expressions.
