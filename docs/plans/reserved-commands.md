# Reserved commands: implementation plan

Status: design agreed, feature not implemented yet.

## Goal and scope

Run from the phone actions that require human intervention, for example the
privileged deploy. The "Commands" screen is not a remote terminal and adds no
approvals to what agents already do autonomously: code changes, tests, builds
and commits stay in the current flow.

The catalogue only admits actions explicitly enabled by the administrator from
a terminal. Not every sudo command is automatically admitted. Actions without
sudo that you want to reserve to the user may be included too, provided a
technical boundary prevents their autonomous execution: hiding a button or
writing a rule does not stop an agent running as the same Linux user from
running a command it can already reach.

Every execution requires a fresh authorization through a passkey with user
verification; on the phone this can be Face ID. No sudo password is collected
or transferred. The catalogue authorizes the kind of action, not all its
future executions.

## User flow

1. The PWA's bottom bar opens "Commands", with the enabled actions, their
   description and the latest outcomes.
2. The user picks an action and only the allowed parameters. For the deploy: a
   precise commit, never a moving reference such as `main` at execution time.
3. The PWA opens the administrative service's confirmation page on a separate
   hostname. The service recomputes the action to display; it does not trust a
   description supplied by the PWA.
4. The user sees action, target, parameters and version. For the deploy: full
   commit, author, subject and changes since the installed version, with
   attention to dependencies and service configuration.
5. A passkey authorizes exactly that operation. The service verifies the
   assertion and atomically consumes the authorization together with creating
   the job.
6. An independent job runs the action. The PWA shows waiting, running, success
   or failure; it can reconnect after a deploy without starting the job again.

The chat may later show a structured proposal with "Review and run", which
opens the same flow. Free text, shell blocks and agent messages never become
executable commands by themselves. No Play button bypasses the confirmation
page.

## Trust boundaries

- Agents and the bridge can propose an action and read the allowed outcomes.
  They cannot authorize, enrol credentials, change the protected catalogue or
  start a privileged job.
- The Placet backend keeps references to proposals and presents states, but
  holds no reusable credential to authorize execution.
- The administrative service verifies the passkey directly. An
  `approved=true` field, an agent/bridge token or the `placet` UID alone are
  not enough to start the action.
- Catalogue, executor, confirmation page, public keys and authentication
  configuration live outside `/opt/placet`, are protected by root and are not
  updated by the normal Placet deploy.
- The network-facing process and the root portion must be minimal and
  separated where possible. The executor only receives an already authorized
  job with validated parameters, never an arbitrary shell.
- The Tailscale identity limits access but does not replace cryptographic
  consent. The confirmation uses an RP ID separate from the one available to
  the PWA; a different port alone does not achieve this separation.
- Placet must not be able to replace the origin of the confirmation. DNS, TLS
  and Tailscale Serve management must be included in the review of the desktop
  user's privileges before the boundary is declared secure.

A passkey proves the user authorized that action, not that the chosen code is
correct. Agents can still modify the repository and use the credentials
available to their user. The confirmation page must make the authorized commit
clear; reviewing the code remains a human decision.

## First action: a safe deploy

The current `deploy/install.sh` runs as root a script and dependencies taken
from the repository that agents can write to, including the pip install; it
also copies units from the repository. It must not be exposed directly as a
remote administrative command.

The new executor must:

- resolve and verify a full commit and the repository's identity, then
  materialize an isolated copy without depending on the modifiable working
  tree;
- build the frontend and dependencies as a dedicated user, without sudo,
  credentials, access to the user's home or the services' sockets; treat the
  build process itself as hostile;
- never run as root scripts, Git hooks, venv programs or instructions coming
  from the commit;
- generate the services from protected administrative templates; keep a fixed
  installation target;
- stop the build's whole process group before acquiring the artifacts; refuse
  symlinks, special files, external paths and dangerous metadata; protect the
  acquired snapshot from later changes;
- publish a protected release and activate it atomically, avoiding partial
  copies over the running code;
- run application migrations as the service user, with backups and data
  compatibility: rolling back files alone does not undo an SQLite migration;
- keep the job ID, installed commit, result and logs outside the processes
  being restarted.

The user bridge needs a distinct path for its restart: root power is not
extended generically to arbitrary user commands. Every action has a fixed
handler and limited parameters.

## Catalogue and authorizations

For each action: stable ID, human description, catalogue/handler revision,
typed parameters, required privileges, timeout, concurrency policy and allowed
logs. No `shell=True`, free command, arbitrary directory, free environment
variable or extra argument supplied by the requester.

First candidate catalogue: deploy of a precise commit, backend restart, bridge
restart. Enabling and changing actions remain administrative operations from a
terminal. Do not put Git commits, tests or ordinary builds into the catalogue.

The challenge is random, short-lived (about 60 seconds), single use and bound
server-side to: canonical action, parameters, catalogue/handler version,
artifact or commit, and the summary shown. If the authorized elements or the
relevant installed version change, a new confirmation is needed. Verify
signature, challenge, origin, RP ID and user verification; do not rely on the
authenticator's counter alone against replay.

Enrolling and revoking credentials requires an administrative command from a
terminal. No initial enrolment can be authorized by an agent or by the PWA
session alone. Provide a second credential and recovery through the existing
administrative access.

## States and reliability

Proposed, awaiting confirmation, authorization expired/rejected, job accepted,
running, succeeded, failed, outcome to be checked. Authorizing and creating the
job is a single persistent operation; a double tap, a replay or an HTTP retry
return the same job and do not duplicate the command.

One deploy at a time. Incompatible restarts wait or are refused with an
explanation. A lost connection is not a failure: the phone recovers the job's
state. A crash during a non-idempotent action does not cause a blind automatic
restart. Cancellation, if any, is available only in states where the handler
guarantees it.

Logs contain no passwords, tokens or credential material. Messages from builds
and jobs are untrusted data, shown as text, not as HTML or executable
instructions.

## Implementation order and acceptance criteria

### 1. Executor and isolated deploy, verifiable from a terminal

Build the catalogue, validators, build separation, artifact acquisition and
persistent jobs. Produce a separate, small and reviewable administrative
bootstrap: no automatic privileged installation at this stage.

Checks: altering `install.sh`, requirements and units in the commit must not
run root code; the build has no access to the home, credentials or sockets;
child processes and symlinks allow no changes after acquisition; invalid
hashes and parameters are refused; failures leave no half-done installation.
Check backups/migrations/rollback separately.

### 2. Independent confirmation with a passkey

Build the administrative origin, terminal enrolment, protected page,
challenge, and atomic consumption with job creation. Check on a real phone the
passkey's compatibility, opening from the PWA and returning to the app.
Configure and verify hostname, TLS and the related privileges first.

Checks: agent/bridge tokens and the backend UID are not enough; replay,
expired challenge, changed parameters, missing user verification and a wrong
origin fail; the PWA cannot replace the page or authorize another action.
Restarting Placet and an executor crash do not duplicate the job.

### 3. Commands screen in the PWA

Integrate the available catalogue, opening of the external confirmation,
monitoring and logs. Clearly show unavailability or incomplete configuration;
no executable button before the protected service is ready. The PWA receives
no permanent administrative secret.

Checks: phone/standalone PWA, authentication cancelled or expired, double tap,
network change, reconnection during a deploy, outcome recovered after a backend
restart. Verify that the agents' commits, tests and ordinary tools stay
autonomous.

### 4. Structured proposals in the chat

Add a reference to an enabled action and its candidate parameters. The button
opens the same confirmation; no new authorization shortcut. Test missing
references, an updated catalogue and text that misrepresents the real action.

## Operational status

This document defines the plan; it installs no services, passkeys, sudo
privileges or new operational routes. The protected bootstrap will need an
explicit administrative step after reviewing the concrete files. The existing
administrative access (e.g. SSH) remains the recovery path.
