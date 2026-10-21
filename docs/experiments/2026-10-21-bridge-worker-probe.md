# Probe 4: fixed system worker under an NNP supervisor

**Operator-only, not executed by an agent.** This validates the alternative to
an in-process UID drop. It restarts only the disposable user unit
`placet-probe.service`. It never invokes a production helper or target.

Review [the standalone script](../../admin_executor/bridge_probe.py), including
its fixed UID/GID 1000, expected NSS groups, root runner properties, and inline
worker. Preview is read-only:

```bash
python3 -I admin_executor/bridge_probe.py
sha256sum admin_executor/bridge_probe.py
```

Compare the script with the reviewed commit. Record its full digest as
`<reviewed_sha256>` below; do not substitute a digest from an unreviewed file.
The operator, from a trusted terminal, copies it into a protected location.
No checkout Python is executed as root:

```bash
/usr/bin/sudo /usr/bin/install -d -o root -g root -m 0755 /run/placet-reviewed-probes
/usr/bin/sudo /usr/bin/install -o root -g root -m 0644 admin_executor/bridge_probe.py /run/placet-reviewed-probes/bridge_probe.py
/usr/bin/sha256sum /run/placet-reviewed-probes/bridge_probe.py
```

The second digest must match the reviewed one. As the desktop user, first
refuse any existing `placet-probe.service` file or loaded unit. Create only this
new disposable unit under `~/.config/systemd/user/placet-probe.service`:

```ini
[Service]
Type=simple
ExecStart=/usr/bin/sleep 300
```

Reload the user manager and start only the new disposable target:

```bash
systemctl --user daemon-reload
systemctl --user start placet-probe.service
systemctl --user show placet-probe.service --property=InvocationID,ActiveState,SubState
/usr/bin/sudo /usr/bin/python3 -I /run/placet-reviewed-probes/bridge_probe.py --apply --script-sha256 <reviewed_sha256>
```

The script refuses pre-existing lab paths/units and root-equivalent groups.
The worker asserts all four UID/GID values, the exact captured NSS group set,
NNP=1, and zero effective/permitted/inheritable/bounding/ambient capabilities
before any user-manager operation. This verifies privilege invariants, not a
claim that the desktop UID cannot influence its own manager. Root's runner
uses the supervisor sandbox and starts the fixed system worker, never drops
its own UID, and accepts only a bounded report with the same new worker
InvocationID. The helper verifies a new active/running disposable target.

Success prints worker_invocation plus the closed restart_confirmed report.
Missing/mismatched report, invariant failure, timeout or unchanged target is
inconclusive/failed laboratory evidence: do not enable the production action.
The script retains no privileged result file and removes its runtime unit and
helper. If interrupted, inspect and stop only the two named laboratory system
units before removing `/run/systemd/system/placet-admin-bridge-probe-worker.service`
and `/run/placet-admin-bridge-probe/helper.py`; reload the system manager.
Never substitute another target or remove pre-existing paths.

After reviewing output, the operator stops/removes only the disposable user
unit and the reviewed probe copy, then reloads the user manager. No production
service is stopped, started, restarted or installed by the probe. A successful
probe does not authorize the maintenance upgrade or any real restart job.
