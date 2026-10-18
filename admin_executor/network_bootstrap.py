"""Install only a separate, root-managed Tailscale instance after review.

Preview is the default. This does not log in, advertise a service, grant sudo
permissions, install Placet Admin, or alter the existing tailscaled service.
"""
import argparse
import hashlib
import os
import stat
import subprocess
from pathlib import Path


UNIT_NAME = "placet-admin-tailscale.service"
SOCKET = "/run/placet-admin-tailscale/tailscaled.sock"
UNIT = """[Unit]
Description=Dedicated root-managed Tailscale transport for Placet Admin
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=root
Group=root
ExecStart=/usr/sbin/tailscaled --tun=userspace-networking --port=0 --socket=/run/placet-admin-tailscale/tailscaled.sock --statedir=/var/lib/placet-admin-tailscale --syspolicy-file=
StateDirectory=placet-admin-tailscale
StateDirectoryMode=0700
RuntimeDirectory=placet-admin-tailscale
RuntimeDirectoryMode=0700
UMask=0077
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
KillMode=control-group
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
CapabilityBoundingSet=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK

[Install]
WantedBy=multi-user.target
"""


def install_unit(root=Path("/"), *, owner=0):
    """Fresh unit install; alternate root/owner are for offline tests only."""
    directory = root / "etc/systemd/system"
    for path in (directory, *directory.parents):
        metadata = path.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != owner
                or metadata.st_mode & 0o022):
            raise ValueError(f"Unsafe systemd directory: {path}")
        if path == root:
            break
    target = directory / UNIT_NAME
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w") as output:
        output.write(UNIT)
        output.flush()
        os.fchmod(output.fileno(), 0o644)
        os.fsync(output.fileno())
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--unit-sha256")
    args = parser.parse_args(argv)
    digest = hashlib.sha256(UNIT.encode()).hexdigest()
    if not args.apply:
        print(UNIT)
        print("Unit SHA256:", digest)
        print("Preview only: no system changes.")
        return
    if os.geteuid() != 0:
        parser.error("Installation requires a root-authorized terminal")
    if args.unit_sha256 != digest:
        parser.error("Review the unit and provide its exact SHA256")
    binary = Path("/usr/sbin/tailscaled")
    # No project scripts, dependencies or services execute as root here except
    # this reviewed bootstrap and the system's installed Tailscale binary.
    for path in (binary, *binary.parents):
        metadata = path.lstat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022 or stat.S_ISLNK(metadata.st_mode):
            parser.error("Tailscale binary and ancestors must be root-protected regular paths")
    if not binary.is_file() or not os.access(binary, os.X_OK):
        parser.error("System tailscaled binary is unavailable")
    install_unit()
    subprocess.run(["/usr/bin/systemctl", "daemon-reload"], check=True)
    subprocess.run(["/usr/bin/systemctl", "enable", "--now", UNIT_NAME], check=True)
    print("Dedicated Tailscale instance started. Authentication is still required.")
    print("Use only this socket for subsequent administrative setup:", SOCKET)


if __name__ == "__main__":
    main()
