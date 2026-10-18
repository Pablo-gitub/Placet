"""Fresh installation of the disabled, dedicated administrative web service.

Preview by default. Review this bootstrap and the pinned module bundle before
root execution. It does not configure Tailscale, enable commands or run a deploy.
"""
import argparse
import ast
import hashlib
import json
import os
import pwd
import shutil
import stat
import subprocess
import types
from pathlib import Path
from urllib.parse import urlsplit


PREFIX = Path("/usr/local/lib/placet-admin")
VENV = Path("/usr/local/lib/placet-admin-venv")
UNIT = Path("/etc/systemd/system/placet-admin-web.service")
BUILD_UNIT = "placet-admin-web-deps"
NETWORK_UNIT = Path("/etc/systemd/system/placet-admin-tailscale.service")
NETWORK_DROPIN = Path("/etc/systemd/system/placet-admin-tailscale.service.d/10-web-socket.conf")
CONFIG = Path("/etc/placet-admin")
STATE = Path("/var/lib/placet-admin")
WEB_STATE = Path("/var/lib/placet-admin-web")
BUILD_ROOT = Path("/var/lib/placet-admin-web-deps")


def _load(name, path, *, content=None):
    # Do not trust a timestamp-valid .pyc from the writable checkout during
    # initial bootstrap review. Compile the reviewed source bytes directly.
    module = types.ModuleType(name)
    module.__file__ = str(path)
    exec(compile(path.read_bytes() if content is None else content, str(path), "exec"), module.__dict__)
    return module


def reviewed_snapshot(source):
    """Read the bundle manifest as data, before executing any checkout module."""
    bootstrap_source = (source / "bootstrap.py").read_bytes()
    if len(bootstrap_source) > 1024 * 1024:
        raise ValueError("Bootstrap source exceeds size limit")
    tree = ast.parse(bootstrap_source)
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "MODULES" for target in node.targets)]
    if len(assignments) != 1:
        raise ValueError("Invalid bootstrap manifest")
    names = ast.literal_eval(assignments[0].value)
    if not isinstance(names, tuple) or len(names) > 100 or len(set(names)) != len(names):
        raise ValueError("Invalid bootstrap module list")
    files, digest = {}, hashlib.sha256()
    for name in names:
        if (not isinstance(name, str) or name.startswith("/") or "\\" in name
                or any(part in ("", ".", "..") for part in name.split("/"))):
            raise ValueError("Invalid bootstrap module path")
        if name == "bootstrap.py":
            data = bootstrap_source
        else:
            fd = os.open(source / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as incoming:
                if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
                    raise ValueError("Bootstrap modules must be regular files")
                data = incoming.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError("Bootstrap module exceeds size limit")
        files[name] = data
        digest.update(name.encode() + b"\0" + len(data).to_bytes(8, "big") + data)
    return files, digest.hexdigest()


def validate_origin(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.path or parsed.query
            or parsed.fragment or parsed.port is not None or value != "https://" + str(parsed.hostname)
            or not (parsed.hostname or "").startswith("placet-admin.")
            or not (parsed.hostname or "").endswith(".ts.net")):
        raise ValueError("Use the exact HTTPS hostname of the placet-admin Tailscale Service")
    return value


def copy_runtime(source, target):
    """Acquire the stopped private build output without applying its metadata."""
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    if source.is_symlink() or not source.is_dir():
        raise ValueError("Invalid dependency build output")
    # The private DynamicUser cgroup must already be stopped. State lives under
    # /var/lib/private; other unprivileged processes cannot reach that directory.
    for directory, folders, files in os.walk(source, followlinks=False):
        for name in folders + files:
            info = (Path(directory) / name).lstat()
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)) or info.st_mode & 0o7000:
                raise ValueError("Dependency output contains links or special metadata")
    stage = target.with_name(target.name + ".staging")
    if stage.exists() or stage.is_symlink():
        raise FileExistsError(stage)
    try:
        stage.mkdir(mode=0o755)
        stage.chmod(0o755)
        total = 0
        for directory, folders, files in os.walk(source, followlinks=False):
            relative = Path(directory).relative_to(source)
            for name in folders:
                destination = stage / relative / name
                destination.mkdir(mode=0o755)
                destination.chmod(0o755)
            for name in files:
                original = Path(directory) / name
                fd = os.open(original, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as incoming:
                    info = os.fstat(incoming.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError("Dependency output changed type")
                    total += info.st_size
                    if total > 512 * 1024 * 1024:
                        raise ValueError("Dependency runtime exceeds size limit")
                    destination = stage / relative / name
                    with destination.open("xb") as output:
                        remaining = info.st_size
                        while remaining:
                            data = incoming.read(min(remaining, 1024 * 1024))
                            if not data:
                                raise ValueError("Truncated dependency file")
                            output.write(data)
                            remaining -= len(data)
                        output.flush()
                        os.fchmod(output.fileno(), 0o755 if info.st_mode & 0o111 else 0o644)
                        os.fsync(output.fileno())
        stage.rename(target)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def dependency_command():
    properties = {
        "Type": "oneshot", "DynamicUser": "yes", "StateDirectory": "placet-admin-web-deps",
        "StateDirectoryMode": "0700", "UMask": "0077", "ProtectHome": "yes", "ProtectSystem": "strict",
        "PrivateTmp": "yes", "PrivateDevices": "yes", "NoNewPrivileges": "yes",
        "CapabilityBoundingSet": "", "ProtectProc": "invisible", "ProcSubset": "pid",
        "ProtectKernelTunables": "yes", "ProtectKernelModules": "yes", "ProtectControlGroups": "yes",
        "RestrictSUIDSGID": "yes", "RestrictNamespaces": "yes", "KillMode": "control-group",
        "TimeoutStartSec": "600", "TimeoutStopSec": "10", "MemoryMax": "1G", "TasksMax": "64",
        "LimitFSIZE": "536870912", "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6",
        "StandardInput": "null", "StandardOutput": "journal", "StandardError": "journal",
        "InaccessiblePaths": "-/etc/placet -/etc/placet-admin -/var/lib/placet -/var/lib/placet-admin -/var/lib/placet-admin-tailscale -/run/placet -/run/placet-admin-tailscale -/run/user -/run/dbus -/run/systemd/private",
    }
    args = ["/usr/bin/systemd-run", "--unit=" + BUILD_UNIT, "--wait"]
    args += ["--property=" + key + "=" + value for key, value in properties.items()]
    return args + ["/usr/bin/env", "-i", "PATH=/usr/bin:/bin", "HOME=/var/lib/placet-admin-web-deps",
                   "/usr/bin/python3", "-I", str(PREFIX / "admin_executor/web_dependencies.py")]


def replace_disabled_package(files, expected_digest, *, origin, root_owner=0):
    """Recover only the pre-service failure phase, preserving prior code/data."""
    package = PREFIX / "admin_executor"
    old_files, old_digest = reviewed_snapshot(package)
    if old_digest != expected_digest:
        raise ValueError("Installed administrative bundle differs; refuse recovery")
    for name in old_files:
        path = package / name
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != root_owner or metadata.st_mode & 0o022:
            raise ValueError("Installed bundle is not root-protected")
    for path in (CONFIG / "origin", CONFIG / "catalogue.json"):
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != root_owner or metadata.st_mode & 0o022:
            raise ValueError("Administrative configuration is not root-protected")
    if (CONFIG / "origin").read_text().strip() != origin:
        raise ValueError("Recovery cannot change the administrative origin")
    catalogue = json.loads((CONFIG / "catalogue.json").read_text())
    if catalogue != {"revision": 1, "actions": [{"id": action, "enabled": False}
                        for action in ("deploy", "restart-backend", "restart-bridge")]}:
        raise ValueError("Recovery is limited to the disabled initial catalogue")
    user = pwd.getpwnam("placet-admin-web")
    if (user.pw_uid == 0 or user.pw_gid == 0 or user.pw_dir != "/var/lib/placet-admin-web"
            or user.pw_shell != "/usr/sbin/nologin"
            or set(os.getgrouplist(user.pw_name, user.pw_gid)) != {user.pw_gid}):
        raise ValueError("Unexpected administrative web user; inspect before recovery")
    for path in (VENV, VENV.with_name(VENV.name + ".staging"), UNIT, NETWORK_DROPIN,
                 WEB_STATE, STATE / "authorization.sqlite", BUILD_ROOT / "venv-ready"):
        if os.path.lexists(path):
            raise ValueError(f"Recovery refuses a later installation phase: {path}")
    stage = PREFIX / "admin_executor.recovery-staging"
    backup = PREFIX / ("admin_executor.before-" + expected_digest)
    if os.path.lexists(stage) or os.path.lexists(backup):
        raise ValueError("A previous recovery needs inspection")
    stage.mkdir(mode=0o755)
    stage.chmod(0o755)
    try:
        for name, data in files.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            target.parent.chmod(0o755)
            with target.open("xb") as output:
                output.write(data)
                output.flush()
                os.fchmod(output.fileno(), 0o644)
                os.fsync(output.fileno())
        package.rename(backup)
        stage.rename(package)
        print("Previous protected package retained at:", backup)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--bundle-sha256")
    parser.add_argument("--resume-failed-bootstrap", help="Exact installed bundle digest; pre-service recovery only")
    args = parser.parse_args(argv)
    origin = validate_origin(args.origin)
    source = Path(__file__).resolve().parent
    files, digest = reviewed_snapshot(source)
    if not args.apply:
        print("Preview only. Origin:", origin)
        print("Bundle SHA256:", digest)
        print("Installs protected code, disabled catalogue, dedicated web UID/runtime/service.")
        print("Dependencies build under a private unprivileged systemd unit; no pip runs as root.")
        print("Does not alter Tailscale login/Serve settings, enable commands or deploy Placet.")
        print("Restarts only the dedicated admin transport with access to the protected web socket group.")
        if args.resume_failed_bootstrap:
            print("Recovery preserves the old package and failed build; no users or state are deleted.")
        return
    if os.geteuid() != 0 or args.bundle_sha256 != digest:
        parser.error("Root and the exact reviewed bundle digest are required")
    bootstrap = _load("placet_admin_reviewed_bootstrap", source / "bootstrap.py", content=files["bootstrap.py"])
    if args.resume_failed_bootstrap:
        if (len(args.resume_failed_bootstrap) != 64
                or any(c not in "0123456789abcdef" for c in args.resume_failed_bootstrap)):
            parser.error("Recovery requires the full installed bundle digest")
        for path in (PREFIX, PREFIX / "admin_executor", Path("/etc/placet-admin"), Path("/var/lib/placet-admin")):
            bootstrap._protected_ancestors(path, 0, Path("/"))
    else:
        for path in (VENV, UNIT, PREFIX, Path("/etc/placet-admin"), Path("/var/lib/placet-admin"),
                     Path("/var/lib/placet-admin-web"), Path("/var/lib/placet-admin-web-deps"),
                     Path("/var/lib/private/placet-admin-web-deps"), NETWORK_DROPIN):
            if os.path.lexists(path):
                parser.error(f"Fresh installation only; inspect existing path: {path}")
    for parent in (VENV.parent, UNIT.parent):
        bootstrap._protected_ancestors(parent, 0, Path("/"))
    network = _load("placet_admin_reviewed_transport", source / "network_bootstrap.py", content=files["network_bootstrap.py"])
    if NETWORK_UNIT.is_symlink() or NETWORK_UNIT.read_text() != network.UNIT:
        parser.error("Dedicated network unit differs from the reviewed bootstrap; inspect it first")
    if NETWORK_DROPIN.parent.exists():
        bootstrap._protected_ancestors(NETWORK_DROPIN.parent, 0, Path("/"))
    build_state = subprocess.run(["/usr/bin/systemctl", "show", BUILD_UNIT + ".service",
                                  "--property=ActiveState", "--value"],
                                 capture_output=True, text=True, check=True).stdout.strip()
    if build_state not in ("inactive", "failed"):
        parser.error("A dependency build is still active; refuse concurrent bootstrap")
    if build_state == "failed":
        subprocess.run(["/usr/bin/systemctl", "reset-failed", BUILD_UNIT + ".service"], check=True)
    if args.resume_failed_bootstrap:
        replace_disabled_package(files, args.resume_failed_bootstrap, origin=origin)
    else:
        try:
            pwd.getpwnam("placet-admin-web")
        except KeyError:
            pass
        else:
            parser.error("Web service user already exists; inspect it before provisioning")
        bootstrap.install(files)
        configuration = Path("/etc/placet-admin/origin")
        configuration.write_text(origin + "\n")
        configuration.chmod(0o644)
        subprocess.run(["/usr/sbin/useradd", "--system", "--user-group", "--home-dir", "/var/lib/placet-admin-web",
                        "--no-create-home", "--shell", "/usr/sbin/nologin", "placet-admin-web"], check=True)
    result = subprocess.run(dependency_command())
    # Successful transient units may already be unloaded. A failed build never
    # reaches acquisition; stop it as well so a timeout cannot leave writers.
    stopped = subprocess.run(["/usr/bin/systemctl", "stop", BUILD_UNIT + ".service"], capture_output=True, text=True)
    status = subprocess.run(["/usr/bin/systemctl", "show", BUILD_UNIT + ".service", "--property=ActiveState,LoadState"],
                            capture_output=True, text=True, check=True).stdout
    properties = dict(line.split("=", 1) for line in status.splitlines() if "=" in line)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, result.args)
    if properties.get("ActiveState") != "inactive" or (stopped.returncode and properties.get("LoadState") != "not-found"):
        raise ValueError("Dependency cgroup is not inactive; refuse runtime acquisition")
    copy_runtime(Path("/var/lib/placet-admin-web-deps/venv-ready"), VENV)
    # This probes only reviewed administrative dependencies, not repository
    # application code. Run it as the new unprivileged web user at its final path.
    subprocess.run(["/usr/sbin/runuser", "-u", "placet-admin-web", "--", "/usr/bin/env", "-i",
                    "PATH=/usr/bin:/bin", "PYTHONPATH=" + str(PREFIX), str(VENV / "bin/python"), "-s", "-c",
                    "import fastapi, uvicorn, webauthn; from admin_executor.web import create_app"], check=True)
    template = _load("placet_admin_reviewed_web_unit", PREFIX / "admin_executor/web_service.py")
    fd = os.open(UNIT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "w") as output:
        output.write(template.UNIT)
        output.flush()
        os.fchmod(output.fileno(), 0o644)
        os.fsync(output.fileno())
    NETWORK_DROPIN.parent.mkdir(mode=0o755, exist_ok=True)
    with NETWORK_DROPIN.open("x") as output:
        output.write("[Service]\nSupplementaryGroups=placet-admin-web\n")
        output.flush()
        os.fchmod(output.fileno(), 0o644)
        os.fsync(output.fileno())
    subprocess.run(["/usr/bin/systemctl", "daemon-reload"], check=True)
    subprocess.run(["/usr/bin/systemctl", "enable", "--now", "placet-admin-web.service"], check=True)
    subprocess.run(["/usr/bin/systemctl", "restart", "placet-admin-tailscale.service"], check=True)
    print("Administrative web service started; commands remain disabled.")
    print("Verify the service and private socket before advertising through dedicated Tailscale.")


if __name__ == "__main__":
    main()
