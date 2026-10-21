# Reserved commands: implementation plan

Status: proposal persistence, artifact acquisition, offline release-pointer
journalling, build policy/worker, SQLite backup and WebAuthn authorization core
implemented. A foundation-only bootstrap is available for review. Host isolation
is not attested; build provisioning/supervision, production service cutover,
privileged dispatch and product integration are not implemented yet. A dedicated
enrollment HTTP/UI service and its explicit installer are implemented but require
live installation and iPhone verification; see
[administrative web setup](admin-web-setup.md).

The independent Tailscale transport now has a separately reviewed, explicit
installer. See [administrative network setup](admin-network-setup.md) for the
human steps, live verification and recovery. It enables no administrative action.

### Current implementation boundary

`admin_executor/jobs.py` provides strict candidate action parameters and a
separate SQLite proposal registry. Request IDs deduplicate concurrent retries;
reusing an ID with different parameters is refused. Proposals always remain
`awaiting_confirmation`. The digest identifies canonical input; it is not a
signature or an authorization. The candidate catalogue is not yet a protected,
administrator-enabled production catalogue.

`admin_executor/artifacts.py` acquires an uncompressed tar into a private staging
directory under a caller-configured release root. It freezes and checks the
archive digest before parsing, rejects links, special files, sparse files,
special permission bits, duplicate/non-normalized paths and resource-limit
violations. It copies bytes without applying archive ownership or metadata,
records file hashes and sanitized permissions in a reserved manifest, and
publishes a new release directory by rename only after validation. Existing
releases are refused. Failed validation removes the staging directory.

The release root and all its ancestors must be protected from the builder and
desktop user; acquisitions must be serialized by the future administrative
service. A digest supplied by a requester proves no authorization. The builder
must be stopped and its output digest computed through the protected workflow
before acquisition. This primitive does not provide that workflow or prove
build isolation. Venv symlinks are refused too: the eventual builder must
produce a portable runtime with regular files and verify its final release path.
Copying a default development venv is not an accepted deployment strategy.

The manifest is integrity metadata, not a signature. Published code is not
executed by acquisition. Filesystem sync errors after the publication rename
require reconciliation with the manifest; callers must not blindly rerun or
activate on an exception. Directory publication does not change `/opt/placet`,
the current release pointer, running services or the database.

`admin_executor/releases.py` records a prepared activation in a separate SQLite
journal before atomically selecting an acquired release through a managed
`current` symlink. It serializes operations through a file lock, checks the
expected current version, binds intent to the acquired manifest digest and
refuses another activation while one remains unresolved. On restart, a pointer
already changed to the intended version is reconciled without repeating the
selection. Selection is not success: the trusted supervisor must separately
perform migrations/service coordination and report its health check. Failure
enters `needs_review`; there is no automatic database or file rollback. There
is not yet an operator recovery command for that state.

This manager assumes immutable, protected acquired releases; it compares the
manifest digest, not every file's contents on each selection. It cannot attest
production ownership, actual service health or authorization. Its private root
and pointer are not connected to `/opt/placet` or production services. Its
`record_health` method is a future internal supervisor interface, not an API
accepting a backend/agent assertion that deployment succeeded.

`admin_executor/bootstrap.py` runs directly with system Python in isolated mode;
its default invocation is a read-only preview:

```bash
python3 -I admin_executor/bootstrap.py
```

It freezes an allowlist of regular module files (including itself), prints their
combined digest and the intended fixed destinations. Explicit `--apply` requires
root and the matching `--bundle-sha256` value. The bootstrap source itself must
be reviewed and held unchanged before invoking it as root: a digest of modules
does not authenticate an untrusted bootstrap program. No application imports,
dependency installs or repository scripts run during installation.

The fresh-install-only operation creates `/usr/local/lib/placet-admin`,
`/etc/placet-admin` and `/var/lib/placet-admin`, refusing existing destinations,
symlink ancestors and ancestors writable by non-root. State subdirectories are
private; the catalogue lists three disabled candidate actions. No sudoers/polkit
rules, user accounts, commands launcher, network listeners or services are
installed. An interrupted install requires manual review; it cannot silently
overwrite a partial previous installation. Upgrading administrative code remains
a separate, explicitly reviewed operation. This foundation bootstrap is not yet
the operational command-service installation and need not be applied now.

The foundation bootstrap alone exposes no HTTP routes or service execution
methods and makes no changes to the current deploy. Current component status
is recorded below. Production ownership and filesystem protection are not
provided by the libraries alone. Pending: isolated build supervision, source
identity verification, backups and service activation/recovery, followed by the
administrative bootstrap. Only after those checks should authorization and
the PWA be wired. Running `npm run build` in the working tree validates the
frontend but does not establish any of these administrative boundaries.

Relevant isolation references for the next block:
[systemd execution environments](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html),
[control-group termination](https://www.freedesktop.org/software/systemd/man/latest/systemd.kill.html),
and [Bubblewrap's policy responsibilities and limitations](https://github.com/containers/bubblewrap/blob/main/README.md).
Choosing a tool or generating a unit alone is not evidence of isolation: the
host must pass checks for inaccessible credentials/sockets, process cleanup,
resource bounds and rejection of untrusted build scripts as root.

## Build, backup and authorization implementation

- `build.py` renders a fixed systemd service, not a shell supplied by a caller.
  The job ID cannot inject directives. The sandbox uses a dedicated build user,
  a separately provisioned read-only toolchain root, read-only source/dependency
  mounts, a clean environment, private networking and resource limits. Shutdown
  targets the complete cgroup. Only the per-job output mount is host-writable.
  The generated unit has passed the local systemd parser; that does not establish
  effective host isolation or demonstrate a completed build.
- `build_worker.py` refuses root and runs npm offline and wheel-only pip without
  an index inside that environment. Missing cached dependencies fail; there is
  no online fallback. It produces candidate runtime files and refuses links and
  special files. Repository build code can run as the isolated build user.
  Worker output is untrusted and needs independent acquisition after cgroup
  shutdown. Provisioning the toolchain/caches, verified Git snapshot resolution,
  cgroup supervision and validation of the relocated venv remain necessary.
- `backup.py` uses SQLite's backup API and integrity checks, including committed
  WAL contents. It refuses root execution and existing destinations. A separate
  trusted supervisor must quiesce writes at cutover and acquire the backup into
  administrative storage. A file backup does not itself coordinate migrations
  or provide a safe restore operation.
- `authorization.py` verifies real WebAuthn signatures through `webauthn`, in a
  separate administrative Python environment declared in `requirements.txt` in
  that directory. Required user verification, exact HTTPS origin/RP, cross-origin
  rejection, expiration, revoked credentials and one-use challenges are enforced.
  The challenge binds the protected handler's canonical action, parameters,
  artifact digest, installed version and summary. Verification, credential
  counter update, consumption and creation of an authorized job are one SQLite
  transaction. Replays are rejected rather than creating another job.
  Registration and revocation methods require a root-authorized terminal context;
  no network enrollment endpoint exists. Changing origin requires an explicit
  credential migration. Neither a cryptographic signature nor an authorized row
  dispatches a privileged action by itself. The offline restart supervisor is
  described under Operational status. Its confirmation broker is implemented
  but not installed or enabled.

Passkey tests use actual ES256 signatures and registration data; they do not
demonstrate Face ID compatibility on a physical iPhone. Install the separate
administrative requirements for these tests. CI installs them for development
testing; the ordinary application installer does not. No dependency is installed
as root by the bootstrap.

## Administrative hostname on the existing PC

Another physical PC is not required. Tailscale Services can provide a separate
service hostname hosted on the same machine. The existing client supports the
`serve --service` option; actual service registration, host eligibility, access
policy and HTTPS configuration still need verification. See
[Tailscale Services](https://tailscale.com/docs/features/tailscale-services).

The current host grants the desktop user Tailscale operator privileges. Because
agents share that UID, this is an unresolved trust boundary: a different hostname
alone does not protect a confirmation page whose proxy can be reconfigured by
that same user. Administrative proxy/identity management must be made inaccessible
to agent processes (or use a separate root-managed Tailscale instance) before
enabling remote commands. This review has not changed any Tailscale settings.
Do not replace this boundary with another port on the PWA hostname or claim
that a successful signature proves the displayed operation was trustworthy.

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

- The agent/desktop account must have no root-equivalent access. Control of a
  rootful Docker daemon (including membership in `docker` with access to its
  root socket) permits replacing host files and bypasses every boundary below.
  Membership/access through `lxd`, `lxc`, `disk`, an exposed privileged daemon,
  unattended administrative sudo or equivalent host powers must be assessed too.
  Virtualization permissions such as `libvirt` require policy-specific review;
  `kvm` alone is not automatically equivalent to root. Unreviewed administrative
  access is not evidence of isolation.
- Password-protected sudo is a residual risk when agents share the operator's
  UID. They can alter shell startup files, aliases, wrappers or PATH, or present
  a fake password prompt, to intercept a password later entered by the human.
  A passing preflight and password-required sudo do not eliminate this route.
  The guarantee that agents cannot authorize privileged actions depends on the
  operator using an administrative session outside agent control (for example a
  trusted TTY/console with an unmodified login/shell environment, or a separate
  account), or on running agents under a genuinely separate unprivileged UID.
  Merely opening another terminal under the same modifiable shell configuration
  is not isolation. Absolute authorization claims below assume this boundary.
- The activation installer must fail closed if the desktop/agent UID retains
  root-equivalent access, or if that access has not been assessed. Check NSS
  membership and supplementary groups retained by running desktop/agent
  processes: removing a group from account configuration does not remove it from
  existing processes. Also reject desktop membership in the web/backend socket
  groups, whether primary or supplementary; `SO_PEERCRED` is a second barrier,
  not a reason to weaken socket permissions. The separate activation bootstrap implements this mandatory preflight;
  the ordinary installer cannot enable administrative actions.
- The operator chooses remediation: rootless Docker (for example for a personal
  ntfy container), root-managed containers without desktop daemon access, or a
  genuinely separate unprivileged agent account. These commits change no groups,
  container setup, sudo rules or existing user sessions. Root-owned files and
  passkeys cannot protect against an agent that already has root.
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

The restart execution core is implemented but not installed or enabled. It
serializes dispatch under an exclusive lock, claims each authorized job once,
and keeps execution states/events in the protected authorization database.
Recovery marks interrupted work as `outcome_unknown`, without retrying the
restart. Unresolved uncertain outcomes also block later jobs: a timed-out
manager request might still take effect. A root-only reconciliation workflow
must be provided before enabling the supervisor. Disabled, unknown,
noncanonical or changed handler operations fail
before any effect. Public status excludes WebAuthn assertions and credentials.

The fixed backend handler restarts `placet-backend.service`. The bridge handler
drops supplemental groups, GID and UID before running its protected helper,
which addresses only `placet-bridge.service` on that user's manager. Missing
user managers are not started. Backend success requires a new active invocation
that remains stable for five seconds, with unchanged `NRestarts`, followed by
HTTP 200 from `127.0.0.1:<protected-port>/api/health`. The port must come from
root-owned `health.json`, matching the deployed backend; neither hostname nor
path is configurable, and no proposal supplies a port. This is a point-in-time
check, not a future availability guarantee. Bridge results are explicitly
labelled `user_service_report`: the desktop UID can influence the helper and
manager, so this evidence must never authorize another action. Timeouts after a restart request
remain uncertain. The root entry point accepts no arguments and reads only
protected catalogue/desktop identity files; the latter is not provisioned yet.

The confirmation broker and protected page are implemented, not installed.
`confirmation_proposals` stores immutable intents, `challenge_proposals` binds
nonces to those intents, and `proposal_jobs` permits only one job per intent.
Signature verification, counter update, challenge consumption and job binding
commit atomically. An exact retry of a consumed bound assertion returns its
existing job; an unbound challenge replay is still rejected. No retry dispatches
another effect. Previously authorized summaries remain the summaries displayed
for those jobs even if the protected description changes later.

Unconfirmed proposals expire 15 minutes after creation, independently of the
60-second challenge. Expiration is checked before and after signature verification
inside the authorization transaction. Reading or retrying an idempotency key
does not renew it. Expired proposals remain readable and require a new explicit
proposal from the PWA. Already authorized jobs do not expire under this rule.

The web process calls the root broker over a bounded Unix protocol with mutual
UID checks, never opens its credential database, and exposes no dispatch route.
Distinct web/backend socket roles restrict the backend to catalogue, proposals
and status. The page compares its reviewed operation with the challenge before
requesting a passkey, then recovers state through GET-only polling. Errors never
cause an automatic signature or authorization retry. English/Italian interaction
tests and Chromium at 375 px use simulated browser credentials; cryptographic
broker tests use real ES256 assertions. These are not live phone execution tests.

The human-only backend API is implemented: catalogue/public recent references,
idempotent proposal creation and status reads. Agent/bridge tokens cannot use
it. The ordinary backend client imports no administrative/crypto package, checks
the broker's root UID, has no authorization/dispatch methods, and projects only
public fields. Missing, unreachable or malformed catalogues expose no actions.
Lost creation acknowledgements are recovered with the same request key, not a
new proposal. Job state remains in administrative storage across backend restarts.

The PWA Commands screen is implemented under Settings (`#/comandi`), not
deployed. Run prepares an intent; a second explicit tap opens its HTTPS
confirmation link, with its destination host shown before navigation. No async
response, polling event or page restoration opens a window automatically.
Before creation, the PWA persists a random idempotency key. Lost acknowledgements
retain that key across reloads and require an explicit retry; a new intention
after completion/expiry receives a new key. If the reference cannot be persisted,
creation does not start. Only public proposal IDs are cached, not signing data
or confirmation URLs. Status recovers from administrative storage with bounded
backoff during expected backend interruptions; connection loss is not job failure.

Disabled/unconfigured catalogues offer only an explanation and the installation
guide. Invalid historical entries are skipped without hiding valid actions.
The root catalogue also exposes the first unresolved `blocked_job_id`, even if
its proposal has left the ten most recent references. The PWA disables further
preparation and explains terminal reconciliation without attempting it. The UI
documents the future E terminal contract (`reconcile --id ... --outcome
succeeded|failed`); that CLI must be implemented before activation. Bridge
evidence remains labelled as a user-service report.

Root reconciliation, supervised units and the reviewed
active-installation upgrade remain pending. No broker or supervisor unit is
installed by the current bootstrap, and all catalogue entries remain disabled.
Tests use injected restart handlers and never restart real services.

No restart action is operational yet. The already installed enrollment service
and enrolled passkey are unchanged by these repository commits. Activating the
new components requires a separate reviewed administrative upgrade; ordinary
Placet deploys must not replace them. Existing administrative access (e.g. SSH)
remains the recovery path.


## Part E: reviewed activation, pending human installation

The `codex/admin-activation` change adds a preview-default, digest-pinned upgrade
for the existing enrollment-only installation. Only `restart-backend` and
`restart-bridge` can be enabled, after root-equivalent-access and peer-group
checks. It does not change Tailscale, accounts, containers, sudoers or dependency
runtimes. It preserves passkeys, retains the old package and takes a private
SQLite backup. An interrupted upgrade needs human inspection, never an automatic
rerun. The current desktop Docker membership remains an activation blocker until
the operator chooses a remedy; no remediation is performed by this change.

The fixed broker and supervisor are separate root services with bounded restart
rates. The broker has CHOWN solely for socket ownership; the supervisor has
SETUID/SETGID for dropping the bridge helper to the desktop identity. Neither
has DAC override or general shell input. Home directories stay inaccessible;
`/run/user` remains readable for the already-dropped helper's fixed user-bus
connection. User-service evidence is indicative and never authorizes another job.

Root-terminal reconciliation holds the execution lock and records a terminal
outcome without repeating an effect. The PWA and installation guide share the
same CLI contract under a regression test. Unknown outcomes block all new jobs
until a human verifies the actual service state and reconciles them.

On iPhone the confirmation page asks the user to switch back to the installed
Placet app manually; a secondary link opens Placet in a browser. Both command
pages suspend polling while hidden. Real Face ID and both restart actions still
require the acceptance steps in the [installation guide](../installation.md#activating-the-two-fixed-restart-commands)
after review and human activation. Deploy/build execution remains out of scope.

## Restart phase diagnostics (repository only, October 21, 2026)

The first disposable probe did not reproduce a chroot skip; no environment
bypass or sandbox relaxation is justified by that result. The
[root-to-user probe](../experiments/2026-10-21-bridge-drop-probe.md) reproduces the
missing subprocess credential-drop/import stage. Its procedure has not been
executed by an agent and never targets a production service.

Restart handlers now return a closed terminal-state/phase-code pair. The
unprivileged bridge helper emits a small JSON report; the root parent accepts
only the helper's allowed code/state combinations and rejects extra fields or
legacy unstructured output. Parent spawn failures, helper import failure,
nonzero exit, timeout and invalid report are distinct. Stdout is bounded during
collection, stderr is discarded, and exception output is never stored or logged.
Only an allowlisted code enters the existing job result/event fields, with no
schema change or authorization capability added. The PWA and protected page
translate known codes in English/Italian and do not display unknown raw codes.
Historical jobs with no detailed code keep their state and a neutral explanation.

For the bridge, failures before possible dispatch are failed; a nonzero restart
command is `restart_rejected` with **outcome_unknown**, because it may be a job
failure after side effects. Timeout, unchanged invocation, missing response or
post-restart instability remain uncertain. Success requires a new nonempty
InvocationID, active/running, then a stable check two seconds later with the same
ID and unchanged NRestarts. This is still evidence reported by the user service,
not an independent health check. Backend timing/health/state semantics remain
unchanged; its existing unhealthy/unstable failure still reports failed, now
with a diagnostic code. No uncertain job is automatically retried or released.

The code and UI changes are not an installed administrative upgrade. A future
maintenance design must be reviewed separately: pin source/bundle/installed
hashes, freeze new admissions, hold the execution lock after the active handler
finishes (or stop on timeout), and stop only the administrative writer services
before replacing protected code. Preserve live passkeys, authorization/execution
rows, audit, catalogue, identity and transport configuration. Take a consistent
private backup, retain previous code, validate the new report protocol and resume
only after protected configuration checks; never restore stale authorization
state as an automatic rollback or replay a running/uncertain job. Account for
requests signed while maintenance is starting. An unresolved uncertain job stays
blocked until a human reconciliation; an already-authorized queued job must not
be silently lost or reauthorized. Partial failure remains fail-closed for review.
The enrollment-only activation_bootstrap must not be reused. No maintenance
installer or isolated terminal launcher is implemented by this change.

### Fixed bridge worker (review branch; not installed)

`restart-bridge` now uses a fixed system one-shot unit rather than a Python
subprocess credential drop. The supervisor retains NNP and invokes only
`placet-admin-bridge-worker.service`, with no requester-provided argv, target or
environment. The existing supervisor capabilities are not broadened; removing
its now-unused SETUID/SETGID is deferred because this maintenance operation
replaces only the worker unit, not other administrative unit definitions.

The worker runs as the desktop UID/GID, NNP enabled, every capability set empty,
with no home or administrative state access and no writable result file. Its
public, root-owned policy pins the exact supplementary groups; systemd's empty
SupplementaryGroups assignment does **not** remove NSS memberships. Group drift
or root-equivalent groups fail before the user-manager request. The same desktop
UID can interfere with its own manager/processes: the result remains
`user_service_report`, not independent root evidence.

A bounded `sd_notify STATUS` envelope includes the worker InvocationID and a
closed restart report. The root adapter reads only fixed system-manager fields,
requires a new completed worker invocation, correlates it durably with the
claimed job, and never treats a user report as proof of no effect after dispatch.
No worker retries occur. Crash, lost response, nonzero exit or invalid report
preserve `outcome_unknown` and block later jobs. Only a worker invocation already
linked to a finished/reconciled job may be stopped before another authorized
start; an untracked worker requires terminal inspection. The execution lock
covers these steps. A small additive `execution_workers` table retains dispatch
and invocation correlation; no passkey or authorization data moves to the worker.

First run [probe 4](../experiments/2026-10-21-bridge-worker-probe.md) as the
operator after review. It is a laboratory gate, not approval for a real restart.
The separate digest-pinned maintenance installer and installation instructions
are required before this code can run on the host. Ordinary application deploys
never update the protected administrative package.

The review branch includes `worker_maintenance.py`: preview only by default,
four reviewed digests plus explicit groups for root apply from a protected copy.
It freezes writers under the execution lock, rejects pending/uncertain jobs,
backs up enrollment/state and replaces the worker-related bundle plus only its
fixed unit. It never restores old authorization data or tests a real restart.
Interrupted mutation leaves admin components stopped for explicit recovery.
The operator procedure is in the [installation guide](../installation.md).

Worker notification completion is explicit: a separate BARRIER=1 notification
with one SCM_RIGHTS pipe descriptor follows STATUS; EOF acknowledges processing.
The barrier has a three-second deadline and closes both local descriptors on
all paths. A barrier error/timeout exits nonzero and retains uncertain outcome,
without resending the action. Time budgets: worker start 60 seconds, supervisor
start command 75 seconds; the disposable probe adds 100-second root-runner and
120-second outer bounds. `worker_busy` intentionally remains uncertain: this
job has not dispatched, but an untracked/previous worker might still be acting,
so allowing further privileged jobs would violate serialization.
