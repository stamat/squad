# Principles — the law

Non-negotiable. Every implementation follows them; every review checks them.

- **Test-driven.** Tests come first and are the spec — assume the user reads the
  tests before the code. Test everything testable.
- **Never game tests.** A failing test means the code is wrong — fix the code.
  Weakening, skipping, or deleting a test to make it pass is forbidden. If the
  test itself is wrong, say so in your report and let review decide.
- **Focus before features (YAGNI).** Build only what the task needs. One
  function, one job. Keep functions short — short enough to read whole, without
  scrolling.
- **Self-explanatory names, readable code, composition over cleverness.**
  Classes, interfaces, functions, variables — a good name needs no comment.
  Consistent style throughout.
- **No premature optimisation.** Optimise only when the task demands it, or when
  a simple change on a user-facing path yields real resource (thus cost) savings.
- **Reuse before writing.** Standard library and native code over a new
  dependency; and reuse what already exists in this repo before adding anything
  new — the smallest correct change reuses what's here.
- **Root cause over symptom.** A bug fix goes where all callers route through,
  not a band-aid on the one path the report names.
- **Delete dead code.** No commented-out blocks, no unused exports kept "for
  later" — version control remembers.
- **Handle errors on real paths; fail loud.** Validate input at trust
  boundaries. Don't swallow exceptions or mask failures with silent defaults.
- **Declarative over imperative** where it applies.
- **Immutability by default.**
