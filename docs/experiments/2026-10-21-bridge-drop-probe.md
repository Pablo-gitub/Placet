# Root-to-user bridge helper probe

**Procedure only; not executed by an agent.** The operator reported that all
three cases of the [first probe](2026-10-20-bridge-sandbox-probe.md) restarted the
disposable unit: chroot detection logged an error and assumed not-in-chroot.
The chroot hypothesis was not reproduced. This experiment tests the missing
stage: a root process in the supervisor namespace launching a child with
`user=1000, group=1000, extra_groups=[]`.

## Scope and verified properties

No production restart, no protected-package change and no maintenance installer.
Only the newly created placet-probe.service may be started/stopped/restarted.
The agent read the effective supervisor properties without sudo on October 21,
2026: root/root; WorkingDirectory=/usr/local/lib/placet-admin; UMask=0077;
ProtectSystem=strict; ProtectHome=read-only; PrivateTmp/PrivateDevices=yes;
NoNewPrivileges=yes; ProtectKernelTunables/ProtectKernelModules/
ProtectControlGroups=yes; RestrictSUIDSGID/RestrictNamespaces=yes;
ProtectProc=invisible; ProcSubset=pid; KillMode=control-group; TimeoutStop=10s;
MemoryMax=268435456; TasksMax=32; CapabilityBoundingSet=cap_setgid cap_setuid;
RestrictAddressFamilies=AF_INET AF_UNIX; ReadWritePaths=/var/lib/placet-admin;
InaccessiblePaths exactly as in the block; no drop-ins.

Before execution, compare again with the human-readable output of
`systemctl show placet-admin-supervisor.service` for those properties. Stop if
anything differs; do not add capabilities or loosen the sandbox experimentally.
The runner uses the protected interpreter in isolated mode; it imports only the
standard library as root. The child uses /usr/bin/python3 -I, matching production.
Its `-c` body replaces the production helper only so the target is hardcoded to
the disposable unit. No production main function is called. The import control
loads bridge_restart.py with a non-main name and imports its restart dependency,
then exits; it never invokes restart_service.

## Human-only procedure

Review and paste the complete block into a trusted Bash terminal for UID/GID
1000 with its user manager already running. It refuses existing probe names.
Import control runs first, then the probe-only restart helper. Each root runner
is transient and collected; automatic retries are disabled. RuntimeMaxSec=50
bounds execution, with separate startup/stop limits. Private runtime logs retain
stdout/stderr for review; these are lab logs, never Placet DB entries. They contain
only fixed-command diagnostics; do not add tokens, assertions or environment dumps.

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
  for name in import restart; do
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
    --property=RuntimeMaxSec=50
    --property=Restart=no
    --property=MemoryMax=256M
    --property=TasksMax=32
    --property=ReadWritePaths=/var/lib/placet-admin
    '--property=InaccessiblePaths=-/home -/root -/var/lib/placet -/var/lib/placet-admin-web -/var/lib/placet-admin-tailscale'
    '--property=CapabilityBoundingSet=CAP_SETUID CAP_SETGID'
    '--property=RestrictAddressFamilies=AF_UNIX AF_INET'
  )
  root_program=$(/usr/bin/cat <<'ROOTPY'
import errno
import json
import subprocess
import sys

mode = sys.argv[1]
if mode not in ("import", "restart"):
    raise SystemExit(2)
helper = r"""
import json
import os
import runpy
import subprocess
import sys
print(json.dumps({"phase": "helper_entry", "uid": os.geteuid(),
                  "gid": os.getegid(), "groups": os.getgroups()}), flush=True)
# A non-main run loads the protected script but cannot call main()/restart.
runpy.run_path("/usr/local/lib/placet-admin/admin_executor/bridge_restart.py",
               run_name="placet_probe_import")
sys.path.insert(0, "/usr/local/lib/placet-admin/admin_executor")
from restart import restart_service
print(json.dumps({"phase": "helper_import_ok"}), flush=True)
if sys.argv[1] == "import":
    raise SystemExit(0)
env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C",
       "XDG_RUNTIME_DIR": "/run/user/1000",
       "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}
restart_code = 0
for phase in ("before", "restart", "after"):
    argv = ["/usr/bin/systemctl", "--user"]
    if phase == "restart":
        argv += ["restart", "placet-probe.service"]
    else:
        argv += ["show", "placet-probe.service",
                 "--property=ActiveState,SubState,InvocationID,Job"]
    print(json.dumps({"phase": phase}), flush=True)
    try:
        response = subprocess.run(argv, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  env=env | {"SYSTEMD_LOG_LEVEL": "debug"},
                                  timeout=15 if phase == "restart" else 10)
    except subprocess.TimeoutExpired:
        print(json.dumps({"phase": phase, "timeout": True}), flush=True)
        raise SystemExit(22)
    print(json.dumps({"phase": phase, "exit_code": response.returncode,
                      "stdout": response.stdout[:8192].decode("utf-8", "replace"),
                      "stderr": response.stderr[:8192].decode("utf-8", "replace")}), flush=True)
    if phase == "before" and response.returncode:
        raise SystemExit(20)
    if phase == "restart":
        restart_code = response.returncode
raise SystemExit(restart_code)
"""
env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C",
       "XDG_RUNTIME_DIR": "/run/user/1000",
       "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}
print(json.dumps({"phase": "root_before_drop", "mode": mode}), flush=True)
try:
    result = subprocess.run(["/usr/bin/python3", "-I", "-c", helper, mode],
                            user=1000, group=1000, extra_groups=[], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=40)
except OSError as error:
    print(json.dumps({"phase": "spawn_failed", "errno": error.errno}), flush=True)
    raise SystemExit(23)
except subprocess.TimeoutExpired:
    print(json.dumps({"phase": "helper_timeout"}), flush=True)
    raise SystemExit(24)
except subprocess.SubprocessError:
    print(json.dumps({"phase": "preexec_failed"}), flush=True)
    raise SystemExit(25)
print(json.dumps({"phase": "helper_returned", "exit_code": result.returncode,
                  "stdout": result.stdout[:32768].decode("utf-8", "replace"),
                  "stderr": result.stderr[:8192].decode("utf-8", "replace")}), flush=True)
raise SystemExit(0 if result.returncode == 0 else 26)
ROOTPY
  )
  run_probe() {
    case "$1" in import|restart) ;; *) return 2 ;; esac
    /usr/bin/sudo /usr/bin/systemd-run --unit="placet-probe-$1.service" \
      --uid=0 --gid=0 --service-type=exec --wait --pipe --collect \
      "${properties[@]}" --property=ProtectProc=invisible \
      /usr/bin/env -i PATH=/usr/bin:/bin PYTHONPATH=/usr/local/lib/placet-admin \
      /usr/local/lib/placet-admin-venv/bin/python -I -c "$root_program" "$1"
  }

  for name in import restart; do
    /usr/bin/systemctl --user show placet-probe.service \
      --property=ActiveState,SubState,InvocationID,Job \
      | /usr/bin/tee "$logs/$name-outside-before.log"
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

## Interpretation and cleanup

- Import control: outside InvocationID must remain unchanged. root_before_drop
  followed by spawn_failed/preexec_failed identifies the parent launch/drop
  boundary; errno is numeric, not an exception dump. A nonzero child with no
  helper_entry points to interpreter startup. helper_entry without
  helper_import_ok points to script/dependency import. Captured child stderr
  distinguishes the exception. An import-control ID change invalidates the run.
- Restart case: helper_import_ok, before, restart and after identify the exact
  stage reached. A new nonempty InvocationID with active/exited confirms only
  the disposable oneshot restart. Compare inside and outside snapshots.
- Parent timeout, missing output, failed show, changed manager or outside
  interference is inconclusive. Never infer that a restart was sent from a
  parent timeout or nonzero exit alone. No retry or reconciliation occurs here.
- Both cases passing narrows the original incident further; this does not prove
  the production helper's main or its later response protocol works. Preserve
  the installed bundle identity, timing and logs before drawing conclusions.

The EXIT trap stops/removes only the newly created user probe and reloads its
manager. If interrupted, wait for placet-probe-import.service and
placet-probe-restart.service system runners to become inactive before cleanup;
then stop only placet-probe.service, remove only its file under the user's config,
and daemon-reload/reset-failed for that name. Do not remove anything if the run
aborted because a name already existed. Keep the printed private log directory
for review. Never substitute a production unit in this procedure.

Differences from production are explicit: child stdout/stderr are captured,
the protected script is loaded under a non-main name, and the probe-only helper
runs fixed commands for the disposable unit. Debug messages apply only to those
systemctl calls. The root runner has identical privilege/sandbox properties but
uses piped diagnostics, no retry and bounded laboratory lifetime. This probe
adds no SYSTEMD_IGNORE_CHROOT override, phase-code production fix or launcher.
