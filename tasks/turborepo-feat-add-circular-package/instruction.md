## Task

Make `turbo boundaries` flag every circular package dependency chain
in the workspace as a structured boundary issue, with the same
surfacing characteristics as the other boundary issues the command
already reports (e.g. type-only-import or package-not-found errors).
The same diagnostic must appear when the workspace is queried through
`turbo query`'s `boundaries` field. A workspace with no cycles must
still pass cleanly. The diagnostic should clearly state that a
circular dependency was detected and identify the packages involved
in the cycle.

## User stories / requirements

- Running `turbo boundaries` against a workspace with a 3-package circular dependency (`@repo/pkg-a` → `@repo/pkg-b` → `@repo/pkg-c` → `@repo/pkg-a`) reports the cycle: the command exits non-zero and its combined output contains the canonical `Circular package dependency detected` phrase together with every package name in the cycle.
- The same circular dependency surfaces through the GraphQL boundaries query: `turbo query 'query { boundaries { items { message import } } }'` returns a JSON document with at least one item whose `message` carries the canonical `Circular package dependency detected` phrase plus every package name in the cycle.
- A workspace with no circular package dependencies is unaffected by the new rule: `turbo boundaries` against the pre-existing `basic_monorepo` fixture (apps/my-app → packages/util, plus the unrelated `another` package) does not emit a `Circular package dependency detected` diagnostic.
- Multiple independent circular dependency chains in the same workspace each produce their own diagnostic. Given two disjoint 2-cycles (`@repo/pkg-a ↔ @repo/pkg-b` and `@repo/pkg-x ↔ @repo/pkg-y`), the GraphQL boundaries query returns at least two distinct cycle diagnostics — one whose message names the (a, b) pair and another whose message names the (x, y) pair.

## General instructions

- The code repo is at /repo/turborepo.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
