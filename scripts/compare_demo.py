#!/usr/bin/env python3
"""Run exact reference-versus-optimized equality on an official demo tree."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import tempfile
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from mimic_clinical_scores.common.cohort import inspect_cohort
from mimic_clinical_scores.common.concepts import build_concepts
from mimic_clinical_scores.common.duckdb import DuckDBSettings, connect, ensure_run_identity
from mimic_clinical_scores.common.preflight import identity_payload, run_preflight
from mimic_clinical_scores.common.reference import build_unfiltered_reference
from mimic_clinical_scores.common.staging import build_staging
from mimic_clinical_scores.common.validation import assert_reference_equivalent
from mimic_clinical_scores.scores.saps_ii.specification import SAPSII_SPEC


def demo_stay_ids(demo_root: Path) -> list[int]:
    if not any("demo" in part.lower() for part in demo_root.resolve().parts):
        raise ValueError("Refusing unfiltered reference scan: path must identify a demo directory")
    with gzip.open(demo_root / "icu" / "icustays.csv.gz", "rt", newline="") as stream:
        return [int(row["stay_id"]) for row in csv.DictReader(stream)]


def run(demo_root: Path, project_root: Path, work_directory: Path) -> dict[str, object]:
    work_directory.mkdir(parents=True, exist_ok=False)
    work_directory.chmod(0o700)
    stay_ids = demo_stay_ids(demo_root)
    cohort_file = work_directory / "demo_cohort.parquet"
    pq.write_table(pa.table({"stay_id": pa.array(stay_ids, type=pa.int64())}), cohort_file)
    cohort_file.chmod(0o600)
    preflight = run_preflight(
        project_root=project_root,
        mimic_root=demo_root,
        cohort_file=cohort_file,
        mode="full",
    )

    settings = DuckDBSettings(
        database=work_directory / "optimized.duckdb",
        threads=2,
        memory_limit="4GB",
        spill_directory=work_directory / "spill",
    )
    optimized = connect(settings)
    reference = duckdb.connect(str(work_directory / "reference.duckdb"))
    try:
        identity = ensure_run_identity(optimized, identity_payload(preflight, mimic_version="demo"))
        build_staging(
            optimized,
            mimic_root=demo_root,
            cohort=inspect_cohort(cohort_file, mode="full"),
            identity_hash=identity,
            raw_metadata=preflight["raw_sources"],
            profile_directory=work_directory / "profiles",
        )
        build_concepts(
            optimized,
            concepts=(*SAPSII_SPEC.concepts, SAPSII_SPEC.score_concept),
            vendor_root=SAPSII_SPEC.vendor_root(project_root),
            identity_hash=identity,
        )
        build_unfiltered_reference(
            reference,
            mimic_root=demo_root,
            vendor_root=SAPSII_SPEC.vendor_root(project_root),
        )
        assert_reference_equivalent(reference, optimized, stay_ids)
    finally:
        reference.close()
        optimized.close()
    return {"equivalent": True, "stay_count": len(stay_ids), "work_directory": str(work_directory)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--work-directory", type=Path)
    args = parser.parse_args()
    if args.work_directory is None:
        parent = args.project_root / "work"
        parent.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="demo-equivalence-", dir=parent))
        directory.rmdir()
    else:
        directory = args.work_directory
    print(json.dumps(run(args.demo_root.resolve(), args.project_root.resolve(), directory.resolve()), indent=2))


if __name__ == "__main__":
    main()

