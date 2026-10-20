# Bridge restart: chroot detection probe

**Status: procedure only; not executed by an agent.** Review before the human
operator runs it in a trusted Bash terminal. This is a laboratory experiment,
not an administrative upgrade, a diagnosis already established, or a retry of a
Placet job. No backend, bridge, Tailscale or administrative service is restarted.

## Hypothesis and controls

After dropping to UID/GID 1000, `ProtectProc=invisible` together with
`ProcSubset=pid` might make systemctl classify the sandbox as a chroot and skip
an online operation with exit code zero. A successful `show` is not proof that
`restart` reached the user manager. The installed binary's message string alone
is not evidence that this path was taken.

Run three cases against **only** a new `placet-probe.service` user unit:

| Case | ProtectProc | Extra systemctl environment | ProcSubset |
| --- | --- | --- | --- |
| baseline | invisible | none | pid |
| ignore | invisible | SYSTEMD_IGNORE_CHROOT=1 | pid |
| visible | default (no process hiding) | none | pid |

All other sandbox settings remain identical. `ProtectProc=default` is explicit
so an inherited default cannot silently re-enable hiding. Debug output and both
show snapshots are captured per case. The probe has `RemainAfterExit=yes` so its
InvocationID remains observable after `/bin/true` exits; it is never enabled.

## Human-only procedure

Prerequisites: the trusted desktop operator is UID/GID 1000, its user manager and
bus already exist, `/usr/bin/systemd-run` supports the listed properties, and the
administrative state directory already exists. If any differs, stop and review;
do not change identities, start a manager or weaken settings to make it pass.
The transient units start directly as UID 1000; this reproduces the post-drop
sandbox, **not** the supervisor's root-to-user subprocess creation. NSS may add
supplementary groups; the logs record them. A negative result cannot exclude a
failure in that earlier drop/exec stage.

Paste the entire block into Bash only after review. It refuses name collisions,
uses no existing service as a target, and removes its own user unit on exit.
The three root-created transient probe runners are collected automatically;
`RuntimeMaxSec=30` bounds execution; startup and stop have separate 10-second
timeouts. Logs remain in the printed private
runtime directory until the operator deletes them or logs out.

```bash
(
  set -euo pipefail
  test "$(/usr/bin/id -u)" = 1000
  test "$(/usr/bin/id -g)" = 1000
  test "${XDG_RUNTIME_DIR:-}" = /run/user/1000
  test -S /run/user/1000/bus
  /usr/bin/systemctl --version
  /usr/bin/id

  load=$(/usr/bin/systemctl --user show placet-probe.service \
    --property=LoadState --value 2>/dev/null || true)
  test "$load" = not-found
  for name in baseline ignore visible; do
    load=$(/usr/bin/systemctl show "placet-probe-$name.service" \
      --property=LoadState --value 2>/dev/null || true)
    test "$load" = not-found
  done

  unit="$HOME/.config/systemd/user/placet-probe.service"
  test ! -e "$unit" && test ! -L "$unit"
  logs=$(/usr/bin/mktemp -d /run/user/1000/placet-probe-logs.XXXXXX)
  created=0
  cleanup() {
    if test "$created" = 1; then
      /usr/bin/systemctl --user stop placet-probe.service || true
      /usr/bin/rm -- "$unit"
      /usr/bin/systemctl --user daemon-reload
      /usr/bin/systemctl --user reset-failed placet-probe.service 2>/dev/null || true
    fi
    printf 'Probe logs retained at: %s\n' "$logs"
  }
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  /usr/bin/mkdir -p -- "$HOME/.config/systemd/user"
  (set -o noclobber; /usr/bin/cat > "$unit" <<'UNIT'
[Unit]
Description=Disposable Placet sandbox probe (no application work)
[Service]
Type=oneshot
ExecStart=/bin/true
RemainAfterExit=yes
UNIT
  )
  created=1
  /usr/bin/systemctl --user daemon-reload
  test "$(/usr/bin/systemctl --user show placet-probe.service \
    --property=FragmentPath --value)" = "$unit"
  test -z "$(/usr/bin/systemctl --user show placet-probe.service \
    --property=DropInPaths --value)"
  /usr/bin/systemctl --user start placet-probe.service
  /usr/bin/systemctl --user show placet-probe.service \
    --property=ActiveState,SubState,InvocationID,Job

  properties=(
    --property=WorkingDirectory=/usr/local/lib/placet-admin
    --property=UMask=0077
    --property=ProtectSystem=strict
    --property=ProtectHome=read-only
    --property=PrivateTmp=yes
    --property=PrivateDevices=yes
    --property=NoNewPrivileges=yes
    --property=ProtectKernelTunables=yes
    --property=ProtectKernelModules=yes
    --property=ProtectControlGroups=yes
    --property=RestrictSUIDSGID=yes
    --property=RestrictNamespaces=yes
    --property=ProcSubset=pid
    --property=KillMode=control-group
    --property=TimeoutStartSec=10
    --property=TimeoutStopSec=10
    --property=RuntimeMaxSec=30
    --property=Restart=no
    --property=MemoryMax=256M
    --property=TasksMax=32
    --property=ReadWritePaths=/var/lib/placet-admin
    '--property=InaccessiblePaths=-/home -/root -/var/lib/placet -/var/lib/placet-admin-web -/var/lib/placet-admin-tailscale'
    '--property=CapabilityBoundingSet=CAP_SETUID CAP_SETGID'
    '--property=RestrictAddressFamilies=AF_UNIX AF_INET'
  )
  run_probe() {
    local label="$1" protection
    local -a override=()
    case "$label" in
      baseline) protection=invisible ;;
      ignore) protection=invisible; override=(SYSTEMD_IGNORE_CHROOT=1) ;;
      visible) protection=default ;;
      *) return 2 ;;
    esac
    /usr/bin/sudo /usr/bin/systemd-run --unit="placet-probe-$label.service" \
      --uid=1000 --gid=1000 --service-type=exec --wait --pipe --collect \
      "${properties[@]}" "--property=ProtectProc=$protection" \
      /usr/bin/env -i PATH=/usr/bin:/bin LC_ALL=C \
      XDG_RUNTIME_DIR=/run/user/1000 \
      DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
      SYSTEMD_LOG_LEVEL=debug "${override[@]}" /bin/sh -c '
        /usr/bin/id
        /usr/bin/grep -E "^(Uid|Gid|Groups|CapEff|NoNewPrivs):" /proc/self/status
        /usr/bin/readlink /proc/1/root || :
        printf "BEFORE\n"
        /usr/bin/systemctl --user show placet-probe.service \
          --property=ActiveState,SubState,InvocationID,Job || exit 20
        /usr/bin/systemctl --user restart placet-probe.service
        result=$?
        printf "restart_exit=%s\nAFTER\n" "$result"
        /usr/bin/systemctl --user show placet-probe.service \
          --property=ActiveState,SubState,InvocationID,Job || exit 21
        exit "$result"
      '
  }

  for name in baseline ignore visible; do
    if run_probe "$name" 2>&1 | /usr/bin/tee "$logs/$name.log"; then
      printf '%s runner_exit=0\n' "$name" | /usr/bin/tee -a "$logs/$name.log"
    else
      result=$?
      printf '%s runner_exit=%s\n' "$name" "$result" | /usr/bin/tee -a "$logs/$name.log"
    fi
    /usr/bin/systemctl --user show placet-probe.service \
      --property=ActiveState,SubState,InvocationID,Job \
      | /usr/bin/tee "$logs/$name-outside.log"
  done
)
```

`--pipe` deliberately captures stdout/stderr instead of using the supervisor's
journal-only output. The runner does no interactive reads. `Restart=no` and the
runtime bound deliberately prevent an automatic retry; the other sandbox
properties mirror the supervisor. Do not remove flags after an unsupported
property or authentication error: preserve that failure as an inconclusive run.
The group/capability diagnostics are observation only, never root authorization.

## Interpreting the result

- **Hypothesis supported:** baseline `show` works, restart exits zero, the
  before/after InvocationID stays identical, and stderr explicitly reports a
  chroot/offline skip. The ignore control changes InvocationID to a new nonempty
  value and stays active/exited; the visible control also restarts successfully.
  Each comparison is within that case, not against the first case's ID.
- **Refuted for this reproduction:** baseline already changes InvocationID with
  no skip; hidden PID 1 alone did not reproduce the incident. Check the earlier
  helper launch/import/drop stage instead. This does not prove production works.
- **Inconclusive:** any initial/final show fails, a runner fails to start, no
  usable ID is retained, the controls fail too, groups/capabilities differ in a
  relevant way, or another process touches the probe. No restart message alone
  proves success; no unchanged ID alone proves a chroot decision.
- If ignore works but visible does not, ProcSubset or another sandbox feature
  may also affect detection. Do not claim ProtectProc alone caused the failure.

The normal user unit is removed by the trap, without enabling or disabling any
other unit. Check `systemctl --user show placet-probe.service --property=LoadState`
reports not-found after cleanup. If the terminal was killed before cleanup, first
wait for the three named transient runners to be inactive (do not clean up while a runner remains active),
then stop **only** placet-probe.service, remove **only** the unit file above,
run `systemctl --user daemon-reload` and reset-failed for that name. Do not remove
an existing file if this run never created it. The private logs may be kept for
review; no agent should collect unrelated journal or credential data.

## Conditional fix decision (not implemented)

If the controls support the hypothesis, prefer the smallest reviewed change:
set SYSTEMD_IGNORE_CHROOT=1 only for the fixed, unprivileged bridge restart
invocation, retaining unit allowlisting, UID/GID drop, sandbox and post-restart
InvocationID verification. It must not come from a request, become global, or
apply to privileged/backend commands. It bypasses a safety heuristic, so the
fixed host user-bus target and a negative regression case matter. Systemd 259
marks this variable deprecated in favor of SYSTEMD_IN_CHROOT=0; selecting that
supported alternative belongs in the subsequent reviewed fix, not this probe.

Direct D-Bus RestartUnit avoids the CLI heuristic but requires correlation with
the returned job and JobRemoved, timeout handling and final state checks; an RPC
reply alone does not prove the restart completed. Do not add that larger change
before the experiment. If confirmed, either production fix and its diagnostics
need a new reviewed protected-package maintenance upgrade with fresh digests.
No launcher, phase-code implementation or maintenance installer is added here.

Sources: systemd 259's [verb dispatch and offline/chroot handling](https://github.com/systemd/systemd/blob/v259/src/shared/verbs.c),
[chroot detection and environment overrides](https://github.com/systemd/systemd/blob/v259/src/basic/virt.c),
and [RestartUnit dispatch and job tracking](https://github.com/systemd/systemd/blob/v259/src/systemctl/systemctl-start-unit.c).
These describe upstream code, not a verification of the distribution's installed
binary or the live supervisor namespace. No probe result has been recorded yet.
