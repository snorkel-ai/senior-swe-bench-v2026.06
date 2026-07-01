## Task

Let's make the admin conn pool available the moment the manager has its connection options
so that nothing has to open a short-lived connection (esp the lock-breaker that recovers abandoned advisory locks).
The pool that serves bulk shape-snapshot reads should keep its
current timing after lock acquisition.

## User stories / requirements

- While the connection manager is waiting for the advisory lock, the admin connection pool should already be up and serving queries. A caller that asks the connection manager for the admin pool's name during the lock-waiting phase gets back a registered, queryable pool.
- When the advisory lock is held by an abandoned session whose replication slot is inactive, the connection manager's lock-breaker recovery detects the inactive slot, terminates the holding backend, and the replication client then acquires the lock and proceeds. The breaker behavior must be preserved end-to-end (from the caller's view, the lock-acquisition event eventually fires).
- With an active replication slot in place (i.e. slot is not abandoned) the lock-breaker recovery shouldn't terminate the backend holding the lock. So when the lock-breaker recovery runs while a real replication client holds the lock and its slot is active, the holding backend is not terminated and the replication client process is still alive.

## General instructions

- The code repo is at /repo/electric.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
