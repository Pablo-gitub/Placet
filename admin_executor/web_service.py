"""Fixed service template for a separately provisioned, unprivileged web UID."""


UNIT = """[Unit]
Description=Placet Admin protected enrollment page
After=network.target

[Service]
Type=simple
User=placet-admin-web
Group=placet-admin-web
WorkingDirectory=/usr/local/lib/placet-admin
ExecStart=/usr/bin/env -i PATH=/usr/bin:/bin PYTHONPATH=/usr/local/lib/placet-admin /usr/local/lib/placet-admin-venv/bin/python -s -m admin_executor.serve
StateDirectory=placet-admin-web
StateDirectoryMode=0700
RuntimeDirectory=placet-admin-web
RuntimeDirectoryMode=0710
BindReadOnlyPaths=/etc/placet-admin/origin:/run/placet-admin-web/origin
UMask=0007
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
PrivateNetwork=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
InaccessiblePaths=/var/lib/placet-admin /var/lib/placet-admin-tailscale /run/placet-admin-tailscale
CapabilityBoundingSet=
RestrictSUIDSGID=yes
RestrictAddressFamilies=AF_UNIX
TasksMax=32
MemoryMax=256M
Restart=on-failure
RestartSec=5
StandardInput=null

[Install]
WantedBy=multi-user.target
"""
