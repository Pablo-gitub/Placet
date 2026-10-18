# Dedicated Placet Admin Tailscale transport

Status: installer and systemd syntax tested offline; no live transport installed
or authenticated by these checks. This is transport setup, not command-service
activation. The administrative web application is still not operational.

The `placet-admin` Service definition is created in the Tailscale console with
endpoint `tcp:443`. Do not retag the desktop's existing Tailscale node: its user
identity may be required by current SSH and PWA access.

## 1. Review and start the dedicated instance

From the repository root, preview the fixed unit:

```bash
/usr/bin/python3 -I admin_executor/network_bootstrap.py
```

Review that file before running the bootstrap as root. Its unit digest pins the
unit content, not the trustworthiness of a modified bootstrap program. With the
digest printed by the reviewed preview:

```bash
sudo /usr/bin/python3 -I admin_executor/network_bootstrap.py --apply --unit-sha256 <reviewed-unit-digest>
```

This fresh-install-only operation installs `placet-admin-tailscale.service`,
reloads systemd and starts/enables only that unit. Existing unit files are refused.
It uses the system's `/usr/sbin/tailscaled` in userspace networking mode, with
automatic UDP port allocation, separate root-private runtime/state directories
and no desktop operator. It does not change `tailscaled.service`, host DNS,
routes or the PWA's existing proxy. Userspace mode and the hardening policy must
still pass live coexistence checks; parser validation alone does not prove that.

## 2. Authenticate and assign the new node identity

After checking the dedicated daemon is active, authenticate it interactively
from a human terminal, always specifying its socket:

```bash
sudo tailscale --socket=/run/placet-admin-tailscale/tailscaled.sock up --hostname=placet-admin-host --accept-dns=false --accept-routes=false --netfilter-mode=off --ssh=false --operator= --timeout=60s
```

Open the login URL locally; do not send auth keys or credentials through Placet.
The node appears separately as `placet-admin-host`. Define a dedicated host tag
with appropriate owners in the tailnet policy and apply it to this new node
only. The old desktop node remains unchanged. Tailscale Services requires a
tag-based host identity. Check the actual policy and restrict access to the
human supervisor; adding an allow rule does not narrow broader existing rules.

## 3. Verify the boundary before advertising

- Existing PWA and SSH access still work.
- New daemon state and its socket's parent directories are root-owned, mode0700.
- The desktop/agent user cannot reach the new LocalAPI socket.
- The new node has no desktop operator, SSH server, accepted DNS/routes, subnet
  routes or exit-node role enabled.
- Service host approval and policy management remain human administrative tasks;
  do not grant the old desktop node automatic hosting of this Service.
- The administrative application listens on its intended protected Unix socket
  and serves the protected page; a user-bindable loopback port is not an adequate
  replacement because an agent could impersonate it while the application is down.

Only then configure the dedicated daemon's `serve --service=svc:placet-admin`
target and approve that new host in the Service console. No forwarding command
is provided yet because the administrative HTTP service/socket does not exist.

## Recovery

To stop only the new transport:

```bash
sudo systemctl disable --now placet-admin-tailscale.service
```

Retain its private state while investigating. Do not run `tailscale logout`,
`serve reset` or `up --reset` against the default socket as a recovery shortcut.

References: [Tailscale Services](https://tailscale.com/docs/features/tailscale-services),
[daemon flags](https://tailscale.com/docs/reference/tailscaled).
