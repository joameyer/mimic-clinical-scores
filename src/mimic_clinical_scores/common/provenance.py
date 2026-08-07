"""Hashing, immutable upstream validation, and raw-source metadata."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


TABLE_REFERENCE_RE = re.compile(
    r"\bmimiciv_(?:derived|hosp|icu)\.[A-Za-z_][A-Za-z0-9_]*\b", re.IGNORECASE
)
ITEM_EQUALS_RE = re.compile(r"\bitemid\s*=\s*(\d+)\b", re.IGNORECASE)
ITEM_IN_RE = re.compile(r"\bitemid\s+IN\s*\(([^)]*)\)", re.IGNORECASE | re.DOTALL)


class ProvenanceError(RuntimeError):
    """Raised when immutable inputs do not match their declaration."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def hash_ordered_ids(stay_ids: Iterable[int]) -> str:
    payload = "".join(f"{stay_id}\n" for stay_id in stay_ids).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def atomic_write_json(path: Path, value: Any, *, protected: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, default=str)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    if protected:
        temporary.chmod(0o600)
    os.replace(temporary, path)


def load_official_manifest(project_root: Path) -> dict[str, Any]:
    path = project_root / "config" / "official_sources.json"
    if not path.is_file():
        raise ProvenanceError(f"Official source manifest is missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_official_sources(project_root: Path) -> dict[str, str]:
    manifest = load_official_manifest(project_root)
    vendor_root = project_root / "vendor" / "mimic-code"
    actual: dict[str, str] = {}
    errors: list[str] = []
    for relative, expected in manifest["sha256"].items():
        path = vendor_root / relative
        if not path.is_file():
            errors.append(f"missing {relative}")
            continue
        observed = sha256_file(path)
        actual[relative] = observed
        if observed != expected:
            errors.append(f"hash mismatch for {relative}: {observed} != {expected}")
    if errors:
        raise ProvenanceError("Invalid vendored MIT-LCP sources: " + "; ".join(errors))
    return actual


def extract_table_references(sql: str) -> set[str]:
    return {match.group(0).lower() for match in TABLE_REFERENCE_RE.finditer(sql)}


def extract_item_ids(sql: str) -> set[int]:
    result = {int(value) for value in ITEM_EQUALS_RE.findall(sql)}
    for body in ITEM_IN_RE.findall(sql):
        result.update(int(value) for value in re.findall(r"\d+", body))
    return result


def audit_dependency_graph(project_root: Path, item_manifest: dict[str, Any]) -> dict[str, Any]:
    """Recursively validate the pinned SAPS II graph and item-ID declaration."""

    official = load_official_manifest(project_root)
    vendor_root = project_root / "vendor" / "mimic-code"
    execution_order: list[str] = official["execution_order"]
    table_to_file: dict[str, str] = {}
    file_refs: dict[str, set[str]] = {}
    own_table_re = re.compile(
        r"CREATE\s+TABLE\s+(mimiciv_derived\.[A-Za-z_][A-Za-z0-9_]*)",
        re.IGNORECASE,
    )
    for relative in execution_order:
        sql = (vendor_root / relative).read_text(encoding="utf-8")
        own = own_table_re.search(sql)
        if not own:
            raise ProvenanceError(f"No output table found in {relative}")
        table_to_file[own.group(1).lower()] = relative
        file_refs[relative] = extract_table_references(sql)

    target = "mimiciv_derived.sapsii"
    needed_files: set[str] = set()
    raw_tables: set[str] = set()

    def visit(table: str) -> None:
        relative = table_to_file.get(table)
        if relative is None:
            if table.startswith(("mimiciv_hosp.", "mimiciv_icu.")):
                raw_tables.add(table)
                return
            raise ProvenanceError(f"Undeclared derived dependency: {table}")
        if relative in needed_files:
            return
        needed_files.add(relative)
        own = next(key for key, value in table_to_file.items() if value == relative)
        for dependency in file_refs[relative] - {own}:
            visit(dependency)

    visit(target)
    ordered_needed = [path for path in execution_order if path in needed_files]
    if ordered_needed != execution_order:
        extra = sorted(set(execution_order) - needed_files)
        missing = sorted(needed_files - set(execution_order))
        raise ProvenanceError(f"Execution order does not equal recursive graph; extra={extra}, missing={missing}")

    entries_by_concept: dict[str, set[int]] = {}
    declared_raw_by_concept: dict[str, set[str]] = {}
    for entry in item_manifest["entries"]:
        concept = str(entry["source_concept"])
        entries_by_concept.setdefault(concept, set()).add(int(entry["item_id"]))
        declared_raw_by_concept.setdefault(concept, set()).add(str(entry["raw_table"]))

    audit_errors: list[str] = []
    prefix = "mimic-iv/concepts_duckdb/"
    for relative in execution_order:
        concept = relative.removeprefix(prefix)
        sql = (vendor_root / relative).read_text(encoding="utf-8")
        observed_ids = extract_item_ids(sql)
        declared_ids = entries_by_concept.get(concept, set())
        if observed_ids != declared_ids:
            audit_errors.append(
                f"{concept} item IDs observed={sorted(observed_ids)} declared={sorted(declared_ids)}"
            )
        observed_raw = {
            table for table in file_refs[relative] if table.startswith(("mimiciv_hosp.", "mimiciv_icu."))
        }
        declared_raw = declared_raw_by_concept.get(concept, set())
        item_raw = {table for table in observed_raw if table.endswith(("chartevents", "labevents", "outputevents"))}
        if item_raw != declared_raw:
            audit_errors.append(
                f"{concept} item-bearing raw tables observed={sorted(item_raw)} declared={sorted(declared_raw)}"
            )
    if audit_errors:
        raise ProvenanceError("Item-ID audit failed: " + "; ".join(audit_errors))

    return {
        "concept_dependency_order": ordered_needed,
        "raw_tables": sorted(raw_tables),
        "sql_hashes": {path: official["sha256"][path] for path in execution_order},
    }


def expected_raw_sha256(mimic_root: Path) -> dict[str, str]:
    checksum_file = mimic_root / "SHA256SUMS.txt"
    if not checksum_file.is_file():
        return {}
    result: dict[str, str] = {}
    for line in checksum_file.read_text(encoding="utf-8").splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            result[parts[1].lstrip("*./")] = parts[0].lower()
    return result


def read_gzip_csv_header(path: Path) -> tuple[str, ...]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
        line = stream.readline()
    if not line:
        raise ProvenanceError(f"CSV is empty: {path}")
    return tuple(next(csv.reader([line])))


def raw_source_metadata(
    mimic_root: Path,
    relative: str,
    *,
    verify_checksum: bool = False,
) -> dict[str, Any]:
    path = mimic_root / relative
    stat = path.stat()
    expected = expected_raw_sha256(mimic_root).get(relative)
    observed = sha256_file(path) if verify_checksum else None
    if verify_checksum and expected and observed != expected:
        raise ProvenanceError(f"Raw checksum mismatch for {path}: {observed} != {expected}")
    fingerprint_payload = {
        "path": str(path.resolve()),
        "relative_path": relative,
        "compressed_size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "header": list(read_gzip_csv_header(path)),
        "manifest_sha256": expected,
    }
    metadata = {
        **fingerprint_payload,
        "verified_sha256": observed,
        "source_fingerprint": canonical_json_hash(fingerprint_payload),
    }
    return metadata


def software_versions() -> dict[str, str]:
    versions = {"python": sys.version.split()[0]}
    try:
        import duckdb

        versions["duckdb"] = duckdb.__version__
    except ImportError:
        versions["duckdb"] = "not-installed"
    try:
        import pyarrow

        versions["pyarrow"] = pyarrow.__version__
    except ImportError:
        versions["pyarrow"] = "not-installed"
    return versions
