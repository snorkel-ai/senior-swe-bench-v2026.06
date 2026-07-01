## Task

The entity detail pages in the admin UI don't surface recent policy authorizations that involve the entity — an operator investigating access has to go to the database to find out which actor's client was recently granted access to which resource under which policy. Add a view of recent policy authorizations relevant to the entity being viewed on each of the Clients, Policies, and Resources detail pages, without leaving the page and without disturbing each page's existing default content. Keep the three panels consistent, and scope each listing to its own entity and to the admin's own account.

## User stories / requirements

- A Client detail page surfaces the recent policy authorizations that involve that client, each shown with the resource and the group that authorization went through.
- A Policy detail page surfaces the recent authorizations granted through that policy, each showing the responsible actor.
- A Resource detail page surfaces the recent authorizations that granted access to that resource, each showing the responsible actor.
- Each detail page lists only the authorizations keyed to its own entity. An authorization recorded for one client does not show up on a different client's recent-authorizations view, even when both clients belong to the same account.
- Authorizations granted through an "Everyone" group policy have no explicit group membership. Such an authorization still renders on the detail page without crashing, and its responsible actor is still shown.

## General instructions

- The code repo is at /repo/firezone.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
