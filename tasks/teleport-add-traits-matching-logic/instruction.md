## Task

Let's add trait-aware filtering feature to Teleport's user listing.

The list users API needs support a new
traits-based matcher. When the filter carries a non-empty set of required
traits, only users with matching traits are listed.
Should apply independently to each trait in the filter.

Let's also extend free-text user search so that
users can be found by their trait data, which should be in addition to the existing fields it
already checks. Make sure this doesn't break the existing search behavior.

The protobuf data model for the new filter field is already generated, build
the matching behaviour on top of it.

## User stories / requirements

- When a user lists users with a required-traits filter, they get back exactly the users whose traits match it. When a key lists several values, only users whose values include EVERY requested value match.
- When a user lists users with a free-text keyword search, they get back results from matches in trait data (keys and values) in addition to the fields the search already covered. A keyword that matches no user returns nobody.

## General instructions

- The code repo is at /repo/teleport.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
