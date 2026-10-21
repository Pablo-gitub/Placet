"""An on-demand fixed user worker; never enabled at boot or automatically retried."""
WORKER = 'placet-admin-bridge-worker.service'


def worker_unit(*, uid, gid):
    if any(type(n) is not int or n <= 0 for n in (uid,gid)):
        raise ValueError('Explicit non-root worker identity required')
    return f'''[Unit]
Description=Placet fixed bridge restart worker
StartLimitIntervalSec=60
StartLimitBurst=3

[Service]
Type=oneshot
RemainAfterExit=yes
User={uid}
Group={gid}
SupplementaryGroups=
WorkingDirectory=/usr/local/lib/placet-admin
UMask=0077
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictNamespaces=yes
ProtectProc=invisible
ProcSubset=pid
RestrictAddressFamilies=AF_UNIX
InaccessiblePaths=-/home -/root -/var/lib/placet-admin -/var/lib/placet -/var/lib/placet-admin-web -/var/lib/placet-admin-tailscale -/etc/placet -/etc/placet-admin
NotifyAccess=main
Restart=no
TimeoutStartSec=60
TimeoutStopSec=5
KillMode=control-group
MemoryMax=64M
TasksMax=16
StandardInput=null
StandardOutput=null
StandardError=null
UnsetEnvironment=PYTHONPATH PYTHONHOME LD_PRELOAD LD_LIBRARY_PATH
ExecStart=/usr/local/lib/placet-admin-venv/bin/python -I /usr/local/lib/placet-admin/admin_executor/bridge_restart.py
'''
