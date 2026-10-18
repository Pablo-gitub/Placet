"""Fixed worker, run ONLY inside the systemd offline build root.

Do not run on the desktop or as root. The privileged supervisor never imports
or executes build output. This worker has no production-service access.
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path


SOURCE = Path("/source")
WORK = Path("/work/source")
OUTPUT = Path("/output")
RUNTIME_DIRECTORIES = ("backend", "bridge", "hooks", "mcp_server")
RUNTIME_FILES = ("run_server.py", "run_bridge.py", "requirements.txt")


def regular_tree(root: Path):
    """Reject links and special files before copying/handing off build output."""
    if not root.is_dir() or root.is_symlink():
        raise ValueError("Expected a regular build directory")
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            item = Path(directory) / name
            metadata = item.lstat()
            if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                raise ValueError("Build trees must not contain links or special files")
            if metadata.st_mode & 0o7000:
                raise ValueError("Build trees must not contain special permission bits")


def run(args, *, cwd):
    subprocess.run(args, cwd=cwd, check=True, stdin=subprocess.DEVNULL)


def main():
    if os.geteuid() == 0:
        raise ValueError("The build worker must never run as root")
    # These paths are supplied by the fixed sandbox, not user-controlled CLI
    # flags. No credentials or desktop filesystem are mounted into that root.
    if not Path("/build_worker.py").is_file() or not Path("/dependencies/wheels").is_dir():
        raise ValueError("The protected offline build environment is not ready")
    regular_tree(SOURCE)
    shutil.copytree(SOURCE, WORK)
    Path("/work/home").mkdir()
    shutil.copytree("/dependencies/npm-cache", "/work/npm-cache")
    run(["/usr/bin/node", "-e",
         "const [a,b]=process.versions.node.split('.').map(Number); if(a<22||(a===22&&b<13))process.exit(1)"], cwd=WORK)
    run(["/usr/bin/npm", "ci", "--offline", "--cache", "/work/npm-cache", "--no-audit", "--no-fund"],
        cwd=WORK / "frontend")
    run(["/usr/bin/npm", "run", "build"], cwd=WORK / "frontend")
    release = OUTPUT / "release"
    release.mkdir()
    runtime = release / ".venv"
    run(["/usr/bin/python3", "-I", "-m", "venv", "--copies", "--without-pip", str(runtime)], cwd=WORK)
    run(["/usr/bin/python3", "-I", "-m", "pip", "--python", str(runtime / "bin/python"),
         "install", "--no-index", "--only-binary=:all:", "--find-links", "/dependencies/wheels",
         "-r", str(WORK / "requirements.txt")], cwd=WORK)
    # venv creates this redundant alias even with --copies on some systems.
    alias = runtime / "lib64"
    if alias.is_symlink() and os.readlink(alias) == "lib":
        alias.unlink()
    for directory in RUNTIME_DIRECTORIES:
        regular_tree(WORK / directory)
        shutil.copytree(WORK / directory, release / directory)
    for name in RUNTIME_FILES:
        source = WORK / name
        if source.is_symlink() or not stat.S_ISREG(source.stat().st_mode):
            raise ValueError("Runtime entry points must be regular files")
        shutil.copyfile(source, release / name)
    regular_tree(WORK / "frontend/dist")
    shutil.copytree(WORK / "frontend/dist", release / "frontend/dist")
    regular_tree(release)
    # No success marker in a writable build directory is trusted by the parent.
    # The supervisor must stop the complete cgroup and independently acquire
    # output before validating the resulting runtime at its final path.


if __name__ == "__main__":
    main()
