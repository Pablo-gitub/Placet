"""Fixed root services. No application code, free argv or shell is executed."""


def units():
    common = '''[Unit]
StartLimitIntervalSec=60
StartLimitBurst=3
After=local-fs.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/usr/local/lib/placet-admin
UMask=0077
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes
PrivateDevices=yes
NoNewPrivileges=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictNamespaces=yes
ProtectProc=invisible
ProcSubset=pid
KillMode=control-group
TimeoutStopSec=10
Restart=on-failure
RestartSec=5
MemoryMax=256M
TasksMax=32
StandardInput=null
StandardOutput=journal
StandardError=journal
ReadWritePaths=/var/lib/placet-admin
InaccessiblePaths=-/home -/root -/var/lib/placet -/var/lib/placet-admin-web -/var/lib/placet-admin-tailscale
'''
    command = 'ExecStart=/usr/bin/env -i PATH=/usr/bin:/bin PYTHONPATH=/usr/local/lib/placet-admin /usr/local/lib/placet-admin-venv/bin/python -s -m admin_executor.'
    return {
        'placet-admin-control.service': common + '''CapabilityBoundingSet=CAP_CHOWN
RestrictAddressFamilies=AF_UNIX
RuntimeDirectory=placet-admin-control
RuntimeDirectoryMode=0711
RuntimeDirectoryPreserve=no
''' + command + 'control\n\n[Install]\nWantedBy=multi-user.target\n',
        'placet-admin-supervisor.service': common + '''CapabilityBoundingSet=CAP_SETUID CAP_SETGID
RestrictAddressFamilies=AF_UNIX AF_INET
''' + command + 'supervisor\n\n[Install]\nWantedBy=multi-user.target\n',
    }
