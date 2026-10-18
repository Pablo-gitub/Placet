"""Build reviewed administrative dependencies under a private DynamicUser unit.

Never run as root. No application requirements or scripts are used here.
"""
import os
import subprocess
import shutil
import tempfile
from pathlib import Path


ROOT = Path("/var/lib/placet-admin-web-deps")
CANDIDATE = "venv-ready"


def main():
    if os.geteuid() == 0:
        raise ValueError("Dependency preparation must be unprivileged")
    destination = ROOT / CANDIDATE
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("A dependency candidate already exists; inspect it before retrying")
    # Do not execute copied interpreters from DynamicUser's id-mapped state
    # directory. Build/execute in PrivateTmp; persistent state is data only.
    # Preserve the previous failed /venv directory for diagnosis.
    with tempfile.TemporaryDirectory(prefix="placet-admin-deps-", dir="/tmp") as temporary:
        runtime = Path(temporary) / "venv"
        python = runtime / "bin/python"
        subprocess.run(["/usr/bin/python3", "-I", "-m", "venv", "--copies", str(runtime)], check=True)
        subprocess.run([str(python), "-I", "-m", "pip", "install", "--no-input", "--only-binary=:all:",
                        "--index-url", "https://pypi.org/simple", "-r",
                        "/usr/local/lib/placet-admin/admin_executor/requirements.txt"], check=True)
        alias = runtime / "lib64"
        if alias.is_symlink() and os.readlink(alias) == "lib":
            alias.unlink()
        shutil.copytree(runtime, destination, symlinks=True)


if __name__ == "__main__":
    main()
