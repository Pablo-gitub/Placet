"""Render a fixed, offline system-service build sandbox; never start it here.

The administrator provisions the read-only toolchain root and dependency caches
separately. Source must be an acquired snapshot of the authorized commit, not
the desktop working tree. Unit installation/start remains disabled until the
trusted job supervisor and authorization service are connected.
"""
import re


def build_unit(job_id: str) -> str:
    if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValueError("Build job ID must be 32 lowercase hexadecimal characters")
    job = f"/var/lib/placet-admin/builds/{job_id}"
    return f"""[Unit]
Description=Placet isolated offline build {job_id}

[Service]
Type=oneshot
User=placet-build
Group=placet-build
RootDirectory=/var/lib/placet-admin/toolchain
MountAPIVFS=yes
BindReadOnlyPaths={job}/source:/source
BindReadOnlyPaths=/var/lib/placet-admin/dependencies:/dependencies
BindReadOnlyPaths=/usr/local/lib/placet-admin/admin_executor/build_worker.py:/build_worker.py
BindPaths={job}/output:/output
TemporaryFileSystem=/work:rw,nosuid,nodev,size=2G,mode=1777 /tmp:rw,nosuid,nodev,size=256M,mode=1777
WorkingDirectory=/work
ExecStart=/usr/bin/env -i PATH=/usr/bin:/bin HOME=/work/home TMPDIR=/tmp LANG=C.UTF-8 /usr/bin/python3 -I /build_worker.py
UMask=0077
PrivateNetwork=yes
PrivateIPC=yes
PrivateDevices=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=/work /tmp /output
ProtectProc=invisible
ProcSubset=pid
NoNewPrivileges=yes
CapabilityBoundingSet=
AmbientCapabilities=
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
RestrictNamespaces=yes
LockPersonality=yes
RestrictAddressFamilies=AF_UNIX
MemoryMax=2G
MemorySwapMax=0
TasksMax=128
LimitFSIZE=1073741824
CPUQuota=200%
TimeoutStartSec=900
TimeoutStopSec=10
KillMode=control-group
SendSIGKILL=yes
Restart=no
StandardInput=null
StandardOutput=journal
StandardError=journal
"""
