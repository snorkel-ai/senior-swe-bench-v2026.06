## Task

We need to give report subscriptions an enabled/disabled lifecycle, i.e. let users pause and resume, and have the delivery pipeline auto-disable a subscription when its target is permanently broken (e.g. the Slack integration was disconnected). We don't want to lose retries for transient failures. We should send an email to the owner when we auto-disable, and show pause/resume in the subscriptions UI (with disabled ones shown as inactive).

## User stories / requirements

- Report subscriptions have an enabled/disabled lifecycle that is independent of the soft-delete flag: enabled by default, can be created already paused, and can be paused or resumed without being deleted. A create/update only kicks off a delivery when the subscription is left enabled. Enabling, re-enabling, or editing an enabled subscription each trigger exactly one delivery, while disabling triggers none.
- Resuming a paused subscription schedules the next delivery in the future and triggers exactly one confirmation delivery.
- If a subscription is in a state where it can't deliver (e.g. a Slack integration that's gone, or an unsupported delivery channel), resuming it is rejected up front, attributed to the enabled field. Same for when the recurrence schedule has already run out.
- A create or edit that would leave a subscription with no future delivery (an exhausted recurrence schedule) is rejected, while an equivalent write whose schedule still has a future occurrence succeeds.
- Triggering a manual test delivery on a paused subscription is rejected, and the user is told to resume it first. An enabled subscription accepts the test delivery.
- The delivery pipeline distinguishes permanent from transient failure. A permanently-broken target (a Slack target with no connected integration, or an unsupported delivery channel) auto-disables the subscription and is handled as a terminal outcome — the pipeline completes rather than erroring out — so it stops retrying forever. A transient failure (no exported assets ready yet) leaves the subscription enabled so the next scheduled cycle retries. Each case records a failure with a categorised reason.
- Once a subscription is auto-disabled, re-running the pipeline (a Temporal redispatch) leaves it disabled and records no additional failed delivery.
- The due-subscriptions scheduler skips disabled subscriptions but keeps enabled ones.
- The subscriptions table renders a subscription as inactive only when its enabled flag is explicitly false. A row with the flag true, missing, or null renders the same as an enabled one, so legacy rows without the flag are not shown as disabled.

## General instructions

- The code repo is at /repo/posthog.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
