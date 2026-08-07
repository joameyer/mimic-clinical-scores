"""Protected stay-ID cohort preparation and validation."""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from mimic_clinical_scores.common.provenance import (
    atomic_write_json,
    hash_ordered_ids,
    read_gzip_csv_header,
    sha256_file,
    software_versions,
    utc_now,
)


DEFAULT_SAMPLE_SIZE = 100
DEFAULT_SAMPLE_SEED = 20260807
ICUSTAYS_HEADER = (
    "subject_id",
    "hadm_id",
    "stay_id",
    "first_careunit",
    "last_careunit",
    "intime",
    "outtime",
    "los",
)


class CohortError(ValueError):
    """Raised for invalid or unsafe cohort inputs."""


@dataclass(frozen=True)
class CohortInfo:
    path: Path
    stay_ids: tuple[int, ...]
    source_row_count: int
    fingerprint: str
    ordered_id_hash: str

    @property
    def unique_stay_ids(self) -> int:
        return len(self.stay_ids)


def _stay_id_table(path: Path) -> pa.Table:
    if not path.is_file():
        raise CohortError(f"Cohort Parquet does not exist: {path}")
    schema = pq.read_schema(path)
    if "stay_id" not in schema.names:
        raise CohortError(f"Cohort must contain a stay_id column: {path}")
    field = schema.field("stay_id")
    if not pa.types.is_integer(field.type):
        raise CohortError(f"stay_id must be an integer Parquet column, found {field.type}")
    return pq.read_table(path, columns=["stay_id"])


def validate_stay_ids(values: Sequence[Any]) -> tuple[int, ...]:
    if not values:
        raise CohortError("Cohort is empty")
    stay_ids: list[int] = []
    for position, value in enumerate(values):
        if value is None:
            raise CohortError(f"stay_id is null at row {position}")
        if isinstance(value, bool) or not isinstance(value, int):
            raise CohortError(f"stay_id at row {position} is not an integer: {value!r}")
        stay_ids.append(value)
    if len(set(stay_ids)) != len(stay_ids):
        raise CohortError("Cohort stay_id values must be unique")
    return tuple(stay_ids)


def inspect_cohort(path: Path, *, mode: str | None = None) -> CohortInfo:
    table = _stay_id_table(path)
    stay_ids = validate_stay_ids(table.column("stay_id").to_pylist())
    if mode == "dev100" and len(stay_ids) != DEFAULT_SAMPLE_SIZE:
        raise CohortError(f"Development mode requires exactly 100 stays, found {len(stay_ids)}")
    return CohortInfo(
        path=path.resolve(),
        stay_ids=stay_ids,
        source_row_count=table.num_rows,
        fingerprint=sha256_file(path),
        ordered_id_hash=hash_ordered_ids(stay_ids),
    )


def _atomic_write_parquet(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    pq.write_table(table, temporary, compression="zstd", version="2.6")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def prepare_development_cohort(
    source: Path,
    output: Path,
    manifest_output: Path,
    *,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    seed: int = DEFAULT_SAMPLE_SEED,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Sample protected stay IDs only; no blocked feature is read or propagated."""

    source_table = _stay_id_table(source)
    source_values = source_table.column("stay_id").to_pylist()
    if any(value is None for value in source_values):
        raise CohortError("Source cohort contains null stay_id values")
    source_stay_ids = [int(value) for value in source_values]
    unique_ids = sorted(set(source_stay_ids))
    if sample_size <= 0:
        raise CohortError("sample_size must be positive")
    if len(unique_ids) < sample_size:
        raise CohortError(
            f"Source has only {len(unique_ids)} unique stay IDs; cannot sample {sample_size}"
        )
    selected = random.Random(seed).sample(unique_ids, sample_size)
    selected_hash = hash_ordered_ids(selected)

    if output.exists() and not overwrite:
        existing = inspect_cohort(output)
        if existing.stay_ids != tuple(selected):
            raise CohortError(
                f"Refusing to overwrite different protected cohort {output}; pass --overwrite"
            )
    else:
        metadata = {
            b"purpose": b"Protected stay_id allowlist only",
            b"seed": str(seed).encode("ascii"),
            b"ordered_selected_id_sha256": selected_hash.encode("ascii"),
        }
        table = pa.table({"stay_id": pa.array(selected, type=pa.int64())}).replace_schema_metadata(
            metadata
        )
        _atomic_write_parquet(output, table)

    manifest = {
        "cohort_source_path": str(source.resolve()),
        "cohort_source_fingerprint": sha256_file(source),
        "source_row_count": source_table.num_rows,
        "number_of_unique_stay_ids": len(unique_ids),
        "sample_size": sample_size,
        "random_seed": seed,
        "ordered_selected_id_sha256": selected_hash,
        "creation_timestamp_utc": utc_now(),
        "software_versions": software_versions(),
        "output_path": str(output.resolve()),
    }
    atomic_write_json(manifest_output, manifest)
    return manifest


def prepare_all_icu_cohort(
    mimic_root: Path,
    output: Path,
    manifest_output: Path,
    *,
    mimic_version: str = "3.1",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create an allowlist containing every stay in the raw MIMIC ICU table."""

    source = mimic_root / "icu" / "icustays.csv.gz"
    if not source.is_file():
        raise CohortError(f"Raw ICU stays source does not exist: {source}")
    observed_header = read_gzip_csv_header(source)
    if observed_header != ICUSTAYS_HEADER:
        raise CohortError(
            f"Unexpected icustays header in {source}: "
            f"observed={observed_header!r}, expected={ICUSTAYS_HEADER!r}"
        )

    escaped_source = str(source.resolve()).replace("'", "''")
    scan = f"""
        read_csv(
          '{escaped_source}',
          header = true,
          compression = 'gzip',
          columns = {{
            'subject_id': 'INTEGER', 'hadm_id': 'INTEGER', 'stay_id': 'BIGINT',
            'first_careunit': 'VARCHAR', 'last_careunit': 'VARCHAR',
            'intime': 'TIMESTAMP', 'outtime': 'TIMESTAMP', 'los': 'DOUBLE'
          }},
          nullstr = '', quote = '"', escape = '"', strict_mode = true
        )
    """
    connection = duckdb.connect(":memory:")
    try:
        values = [row[0] for row in connection.execute(
            f"SELECT stay_id FROM {scan} ORDER BY stay_id"
        ).fetchall()]
    finally:
        connection.close()
    stay_ids = validate_stay_ids(values)
    ordered_hash = hash_ordered_ids(stay_ids)
    source_fingerprint = sha256_file(source)

    if output.exists() and not overwrite:
        existing = inspect_cohort(output)
        if existing.stay_ids != stay_ids:
            raise CohortError(
                f"Refusing to overwrite a different protected cohort {output}; "
                "pass --overwrite after verifying the raw source change"
            )
    else:
        metadata = {
            b"purpose": b"All raw MIMIC-IV ICU stay_id values",
            b"raw_icustays_sha256": source_fingerprint.encode("ascii"),
            b"ordered_selected_id_sha256": ordered_hash.encode("ascii"),
        }
        table = pa.table(
            {"stay_id": pa.array(stay_ids, type=pa.int64())}
        ).replace_schema_metadata(metadata)
        _atomic_write_parquet(output, table)

    manifest = {
        "cohort_kind": "all_raw_mimic_icu_stays",
        "cohort_source_path": str(source.resolve()),
        "cohort_source_fingerprint": source_fingerprint,
        "cohort_source_size_bytes": source.stat().st_size,
        "source_row_count": len(values),
        "number_of_unique_stay_ids": len(stay_ids),
        "sample_size": None,
        "random_seed": None,
        "ordered_selected_id_sha256": ordered_hash,
        "mimic_version": mimic_version,
        "creation_timestamp_utc": utc_now(),
        "software_versions": software_versions(),
        "output_path": str(output.resolve()),
    }
    atomic_write_json(manifest_output, manifest)
    return manifest
