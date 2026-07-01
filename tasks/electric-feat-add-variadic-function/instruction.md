## Task

At parse time, the sync services are rejecting shapes whose WHERE clauses use `coalesce(...)`, `greatest(...)`, or `least(...)`. We need to add support for these in the eval engine. Should treat these the way Postgres does: accepting any number of arguments of compatible types, ignoring nulls (or returning first non-null for coalesce), and short-circuiting when an earlier argument settles the result.

## User stories / requirements

- The WHERE-clause expression engine supports `coalesce(...)`, which accepts any number of arguments and returns the first non-null argument. When every argument is null, it should return null. `coalesce(...)` should short-circuit and return the first non-null argument.
- The expression engine supports `greatest(...)` and `least(...)` with an arbitrary number of arguments. Both `greatest(...)` and `least(...)` skip nulls and return the largest or smallest non-null argument respectively.
- If someone writes a caller passing an array via `func(VARIADIC ARRAY[...])` Postgres syntax, they should get a clear error that mentions VARIADIC.

## General instructions

- The code repo is at /repo/electric.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
