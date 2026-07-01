## Task

Have a bunch of OAuth/OIDC interoperability issues
that all look related.

- Compliant relying parties say that when they send us a malformed request, we don't
hand back the error shape that's in the OAuth specs. They get our
generic validation error instead of the RFC error envelope, so they
can't tell why the request failed.
- When the request to the authorization endpoint is malformed,
the relying party never sees the error delivered back to its redirect
URI the way the spec says it should (just gets a raw error
response).
- Native/desktop integrators report that passing a
token_type_hint value we don't recognize gets their otherwise-valid
revoke or introspect call rejected outright.

Track these down. Fix so our OAuth provider's error behavior is in line with the
OAuth/OIDC RFCs.

## General instructions

- The code repo is at /repo/better-auth.
- You are inside of a Docker container. You may not be able to perform all operations you would normally be able to do on a local machine. Dependencies have not been pre-installed, and you may need to install them yourself.
- You are expected to act autonomously as a software engineer to complete tasks you are given.
- Do not stop until you feel you have completed the task and your code changes can be merged.
- You may need to use software engineering skills like analyzing the codebase, researching technologies, running services, analyzing logs, etc. to complete the task. Not all tasks will be solvable by reading source code alone.
