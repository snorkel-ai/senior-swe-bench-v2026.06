## Task

We're extending the approval-policies system. Changing how broadly a flag is rolled out to users goes through without any review (without toggling it on or off). Ops teams want to gate those rollout changes the same way they gate toggles today.

Add a new kind of approval action that covers flag rollout changes, with enough flexibility for admins to write targeted rules. Needs to be reliable and unambiguous in all scenarios.

The policy settings UI should let admins compose these rules. Existing enable/disable gating must keep working.

## User stories / requirements

- A policy that gates rollout-percentage changes when the new value crosses a numeric threshold (e.g. "gate when rollout > 50%") holds the PATCH for approval; PATCHes that do not cross the threshold update the flag immediately.
- A policy that gates rollout-percentage changes whose magnitude exceeds a delta threshold (e.g. "gate when the rollout changes by more than 10 percentage points") holds large PATCHes for approval; smaller PATCHes pass through.
- Rollout-percentage changes are detected wherever a flag's rollout can actually live, not just the common location.
- When two or more enabled policies for the same action both match the same change, the API rejects the request with a 4xx response and does NOT create a change request. A single matching policy still gates normally (4xx + change request created).
- The settings UI shows a new approval action type when creating or editing a policy.
- When the policy edit modal opens for an existing rollout-change policy that has a condition configured, a condition-builder UI section is visible — containing form elements (selects, inputs, buttons) for configuring the gate rules.

## General instructions

- The code repo is at /repo/posthog.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
