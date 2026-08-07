#!/usr/bin/env python3
"""Verify or refresh the exact vendored MIT-LCP source subset."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    shutil.copyfile(source, temporary)
    os.replace(temporary, destination)


def validate(root: Path, manifest: dict[str, object]) -> None:
    errors: list[str] = []
    hashes: dict[str, str] = manifest["sha256"]  # type: ignore[assignment]
    for relative, expected in hashes.items():
        path = root / relative
        if not path.is_file():
            errors.append(f"missing {relative}")
        elif sha256(path) != expected:
            errors.append(f"hash mismatch for {relative}")
    if errors:
        raise SystemExit("; ".join(errors))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--refresh", action="store_true", help="Download and replace only the pinned source files"
    )
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    manifest = json.loads(
        (project_root / "config" / "official_sources.json").read_text(encoding="utf-8")
    )
    vendor_root = project_root / "vendor" / "mimic-code"
    if not args.refresh:
        validate(vendor_root, manifest)
        print(f"Verified {len(manifest['sha256'])} pinned upstream files")
        return

    with tempfile.TemporaryDirectory(prefix="mimic-code-bootstrap-") as temporary:
        checkout = Path(temporary) / "checkout"
        subprocess.run(
            [
                "git", "clone", "--depth", "1", "--branch", manifest["release"],
                manifest["repository"], str(checkout),
            ],
            check=True,
        )
        commit = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        if commit != manifest["commit"]:
            raise SystemExit(f"Pinned tag resolved to unexpected commit {commit}")
        validate(checkout, manifest)
        for relative in manifest["sha256"]:
            atomic_copy(checkout / relative, vendor_root / relative)
    validate(vendor_root, manifest)
    print(f"Refreshed and verified {len(manifest['sha256'])} pinned upstream files")


if __name__ == "__main__":
    main()

