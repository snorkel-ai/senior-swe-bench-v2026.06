## Task

Our background task system has grown organically: every heavy operation runs
as a Celery job, but the bookkeeping is thin and inconsistent. Ad-hoc per
task, non-standard results, no uniform job status record.

Add automatic job tracking. Real status lifecycle, queue/run timing,
structured inputs/results, and the triggering source. Also add a monitoring
API: list, an aggregated recent-window summary, what's in-flight, an admin
hand-trigger, and acknowledgement, all behind sensible permissions.

Don't break existing clients. And the third-party Celery result stuff must be
ripped out so our own records are the single source of truth. Just do the BE
for now.

## User stories / requirements

- Every tracked job gets recorded automatically as it goes through the queue. Gets created pending on publish (capture its kind, trigger source, and structured inputs; default to manual), advanced through started, and finalized on success, failure (owned by the failure path, not the success path), or revocation. Untracked jobs are ignored.
- v10 clients get the richer, paginated representation. Each task exposes the new structured fields (type, trigger, status, structured inputs/results, queue/run timing) and a related-doc-ids list derived from the structured result.
- v9 clients get the unchanged legacy list using the old field names and values: legacy task names, the type derived from the trigger (scheduled → scheduled_task; system/email/folder → auto_task; else manual_task), status, the reconstructed result string, the related document, and the duplicate-docs list. A detected duplicate counts as a failure.
- The monitoring summary aggregates per task type (counts by state, average timing) over a day-window query parameter (clamp to a maximum, floor at 1, reject invalid).
- Global summary statistics require the system-monitoring permission.
- The active-tasks endpoint returns only in-flight (pending or started) tasks.
- An admin can manually trigger an allowlisted task and gets back the dispatched task id.
- Acknowledging tasks (requiring the change permission) marks them and returns the count affected.
- A non-staff user can list their own and unowned tasks.
- In v9, a duplicate's duplicate-documents entry appears only when the caller can view that document. Otherwise it's empty.

## General instructions

- The code repo is at /repo/paperless-ngx.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
