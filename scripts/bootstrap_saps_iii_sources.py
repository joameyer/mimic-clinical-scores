#!/usr/bin/env python3
"""Download (optionally) and verify the exact public SAPS III source documents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.request
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    manifest = json.loads((root / "config" / "saps_iii_sources.json").read_text())
    destination = root / "vendor" / "saps-iii-sources"
    destination.mkdir(parents=True, exist_ok=True)
    verified = 0
    for index, source in enumerate(manifest["sources"], start=1):
        expected = source.get("sha256")
        if not expected:
            continue
        target = destination / f"source-{index}.pdf"
        if args.download:
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            urllib.request.urlretrieve(source["url"], temporary)
            if digest(temporary) != expected:
                temporary.unlink(missing_ok=True)
                raise SystemExit(f"Downloaded hash mismatch for {source['title']}")
            os.replace(temporary, target)
        if not target.is_file():
            raise SystemExit(f"Missing {target}; use --download")
        if digest(target) != expected:
            raise SystemExit(f"Hash mismatch for {target}")
        verified += 1
    print(f"Verified {verified} pinned SAPS III source documents")


if __name__ == "__main__":
    main()
