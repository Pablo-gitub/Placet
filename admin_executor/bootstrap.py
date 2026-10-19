"""Review-only administrative bootstrap, with explicit pinned installation.

Run directly with the system Python; it imports no project code. Default mode
only prints a plan and bundle digest. Applying installs a frozen copy of the
listed modules and a DISABLED catalogue. It grants no execution privileges and
does not install/start services, run pip, or change the current Placet deploy.
"""
import argparse
import hashlib
import json
import os
import stat
from pathlib import Path


MODULES = ("__init__.py", "jobs.py", "artifacts.py", "releases.py", "bootstrap.py",
           "build.py", "build_worker.py", "backup.py", "authorization.py", "requirements.txt",
           "network_bootstrap.py", "web.py", "terminal.py", "serve.py", "web_service.py",
           "web_bootstrap.py", "web_dependencies.py",
           "mailbox_helper.py", "execution.py", "restart.py", "bridge_restart.py", "supervisor.py",
           "broker.py", "control.py", "activation_bootstrap.py", "activation_checks.py", "activation_units.py",
           "web_static/index.html", "web_static/style.css",
           "web_static/app.js", "web_static/en.json", "web_static/it.json")
CODE = Path("usr/local/lib/placet-admin")
CONFIG = Path("etc/placet-admin")
STATE = Path("var/lib/placet-admin")
MAX_MODULE_BYTES = 1024 * 1024


def snapshot(source: Path) -> tuple[dict[str, bytes], str]:
    """Freeze allowlisted regular files before computing the reviewed digest."""
    files = {}
    digest = hashlib.sha256()
    for name in MODULES:
        fd = os.open(source / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_MODULE_BYTES:
                raise ValueError("Bootstrap source must contain bounded regular Python files")
            data = stream.read(MAX_MODULE_BYTES + 1)
            if len(data) > MAX_MODULE_BYTES:
                raise ValueError("Bootstrap source changed beyond its size limit")
        files[name] = data
        digest.update(name.encode() + b"\0" + len(data).to_bytes(8, "big") + data)
    return files, digest.hexdigest()


def _protected_ancestors(path: Path, owner: int, boundary: Path):
    for current in (path, *path.parents):
        if os.path.lexists(current):
            info = current.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
                raise ValueError(f"Unsafe administrative directory: {current}")
        if current == boundary:
            break


def install(files: dict[str, bytes], *, root: Path = Path("/"), owner: int = 0):
    """Fresh installation only. Alternate root/owner exist for offline tests.

    The CLI never accepts an alternate root or owner. A failed install must be
    reviewed manually; rerunning cannot silently overwrite any installed code.
    """
    destinations = (root / CODE, root / CONFIG, root / STATE)
    if set(files) != set(MODULES):
        raise ValueError("Unexpected bootstrap bundle")
    # Validate every destination before writing anything. The production
    # ancestors are root-owned, so an agent cannot swap them during installation.
    for destination in destinations:
        _protected_ancestors(destination.parent, owner, root)
        if not destination.parent.is_dir():
            raise ValueError(f"Administrative parent directory is missing: {destination.parent}")
        if os.path.lexists(destination):
            raise FileExistsError(f"Administrative path already exists: {destination}")
    for destination in destinations:
        destination.mkdir(mode=0o700)
    (root / CODE).chmod(0o755)
    package = root / CODE / "admin_executor"
    package.mkdir(mode=0o755)
    package.chmod(0o755)
    for name, data in files.items():
        (package / name).parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        (package / name).parent.chmod(0o755)
        fd = os.open(package / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
    catalogue = {"revision": 1, "actions": [
        {"id": action, "enabled": False}
        for action in ("deploy", "restart-backend", "restart-bridge")
    ]}
    with (root / CONFIG / "catalogue.json").open("x") as stream:
        json.dump(catalogue, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    (root / CONFIG / "catalogue.json").chmod(0o600)
    for name in ("releases", "jobs", "backups"):
        (root / STATE / name).mkdir(mode=0o700)
    for path in (package / "web_static", package, *(root / STATE / name for name in ("releases", "jobs", "backups")),
                 *destinations, *(destination.parent for destination in destinations)):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Install the reviewed bundle as root")
    parser.add_argument("--bundle-sha256", help="Digest printed by the reviewed preview")
    args = parser.parse_args(argv)
    files, digest = snapshot(Path(__file__).resolve().parent)
    if not args.apply:
        print(json.dumps({"mode": "preview", "bundle_sha256": digest,
                          "paths": ["/" + str(path) for path in (CODE, CONFIG, STATE)],
                          "actions_enabled": False, "services_started": False}, indent=2))
        return
    if os.geteuid() != 0:
        parser.error("Applying the administrative bootstrap requires root")
    if args.bundle_sha256 != digest:
        parser.error("Bundle digest missing or changed: review the preview again")
    install(files)
    print("Administrative foundation installed. All actions remain disabled; no services started.")


if __name__ == "__main__":
    main()
