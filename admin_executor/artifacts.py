"""Acquire an untrusted, uncompressed build tar into a private release directory.

Never uses tar extraction or executes artifact content. The caller must provide
a protected releases directory and a trusted digest of a frozen build output.
This is an acquisition primitive, not a deploy or an authorization boundary.
"""
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True)
class Limits:
    archive_bytes: int = 512 * 1024 * 1024
    expanded_bytes: int = 1024 * 1024 * 1024
    file_bytes: int = 256 * 1024 * 1024
    members: int = 50000
    path_bytes: int = 1024


def _parts(name: str, limits: Limits) -> tuple[str, ...]:
    if not name or len(name.encode("utf-8")) > limits.path_bytes:
        raise ValueError("Invalid artifact path length")
    if "\\" in name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("Invalid artifact path characters")
    parts = tuple(name.rstrip("/").split("/"))
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("Artifact paths must be relative and normalized")
    if parts[0] == "release-manifest.json":
        raise ValueError("Reserved artifact path")
    return parts


def acquire(archive: BinaryIO, releases: Path, release_id: str,
            expected_sha256: str, *, limits: Limits = Limits()) -> Path:
    """Validate and publish a new release without replacing an existing one.

    `releases` must already exist, be owned by the administrative service, and
    be unwritable by builders/agents. Concurrent acquisition must be serialized
    by that service. Source changes after copying cannot mutate the snapshot;
    a digest mismatch aborts. This function does not stop build processes, attest
    their isolation, or make a requester-supplied digest trustworthy.
    """
    if not isinstance(release_id, str) or not re.fullmatch(r"[0-9a-f]{40}", release_id):
        raise ValueError("A full commit SHA is required for the release ID")
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("Invalid artifact digest")
    if releases.is_symlink() or not releases.is_dir():
        raise ValueError("A protected releases directory is required")
    target = releases / release_id
    if os.path.lexists(target):
        raise FileExistsError(target)
    stage = Path(tempfile.mkdtemp(prefix=".acquire-", dir=releases))
    payload = stage / "payload"
    payload.mkdir(mode=0o700)
    try:
        # Freeze bytes in a private spool before parsing. No decompression or
        # tar-provided ownership, permissions, links or metadata are applied.
        with tempfile.TemporaryFile(dir=stage) as frozen:
            digest, size = hashlib.sha256(), 0
            while chunk := archive.read(1024 * 1024):
                size += len(chunk)
                if size > limits.archive_bytes:
                    raise ValueError("Artifact archive exceeds size limit")
                digest.update(chunk)
                frozen.write(chunk)
            if digest.hexdigest() != expected_sha256:
                raise ValueError("Artifact digest mismatch")
            frozen.seek(0)
            seen, manifest, expanded = set(), [], 0
            with tarfile.open(fileobj=frozen, mode="r|") as tar:
                for index, member in enumerate(tar, 1):
                    if index > limits.members:
                        raise ValueError("Artifact has too many entries")
                    parts = _parts(member.name, limits)
                    name = "/".join(parts)
                    if name in seen:
                        raise ValueError("Duplicate artifact path")
                    seen.add(name)
                    if not (member.isdir() or member.isreg()) or member.issparse():
                        raise ValueError("Artifact links and special files are forbidden")
                    if member.mode & 0o7000:
                        raise ValueError("Artifact special permission bits are forbidden")
                    destination = payload.joinpath(*parts)
                    if member.isdir():
                        if member.size:
                            raise ValueError("Artifact directory contains data")
                        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
                        continue
                    expanded += member.size
                    if member.size < 0 or member.size > limits.file_bytes or expanded > limits.expanded_bytes:
                        raise ValueError("Artifact content exceeds size limit")
                    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    file_hash = hashlib.sha256()
                    source = tar.extractfile(member)
                    with destination.open("xb") as output:
                        remaining = member.size
                        while remaining:
                            data = source.read(min(remaining, 1024 * 1024))
                            if not data:
                                raise ValueError("Truncated artifact content")
                            remaining -= len(data)
                            file_hash.update(data)
                            output.write(data)
                        output.flush()
                        os.fsync(output.fileno())
                    mode = 0o755 if member.mode & 0o111 else 0o644
                    destination.chmod(mode)
                    manifest.append({"path": name, "size": member.size,
                                     "sha256": file_hash.hexdigest(), "mode": mode})
        if not manifest:
            raise ValueError("Artifact contains no files")
        with (payload / "release-manifest.json").open("x") as output:
            json.dump({"commit": release_id, "archive_sha256": expected_sha256,
                       "files": sorted(manifest, key=lambda entry: entry["path"])}, output, sort_keys=True)
            output.flush()
            os.fsync(output.fileno())
        (payload / "release-manifest.json").chmod(0o644)
        for directory, _, _ in os.walk(payload, topdown=False):
            Path(directory).chmod(0o755)
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        # Protected parent + serialized caller prevent concurrent replacement.
        # Recheck also refuses a target created since the initial check.
        if os.path.lexists(target):
            raise FileExistsError(target)
        payload.rename(target)
        fd = os.open(releases, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return target
    finally:
        shutil.rmtree(stage)
