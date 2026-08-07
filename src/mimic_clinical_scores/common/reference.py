"""Unfiltered reference loader for official demo-sized and synthetic test data only."""

from __future__ import annotations

from pathlib import Path

import duckdb

from mimic_clinical_scores.common.concepts import execute_untracked
from mimic_clinical_scores.common.staging import RAW_SCHEMAS
from mimic_clinical_scores.scores.saps_ii.specification import SAPSII_SPEC


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _scan(path: Path, schema: tuple[tuple[str, str], ...]) -> str:
    columns = ", ".join(
        f"{_literal(name)}: {_literal(sql_type)}" for name, sql_type in schema
    )
    return (
        f"read_csv({_literal(str(path.resolve()))}, header=true, compression='gzip', "
        f"columns={{{columns}}}, nullstr='', quote='\"', escape='\"', strict_mode=true)"
    )


def build_unfiltered_reference(
    connection: duckdb.DuckDBPyConnection,
    *,
    mimic_root: Path,
    vendor_root: Path,
) -> None:
    """Load all rows; callers must restrict this to demo-sized/synthetic fixtures."""

    connection.execute("CREATE SCHEMA IF NOT EXISTS mimiciv_hosp")
    connection.execute("CREATE SCHEMA IF NOT EXISTS mimiciv_icu")
    connection.execute("CREATE SCHEMA IF NOT EXISTS mimiciv_derived")
    mapping = {
        "hosp/admissions.csv.gz": "mimiciv_hosp.admissions",
        "hosp/diagnoses_icd.csv.gz": "mimiciv_hosp.diagnoses_icd",
        "hosp/labevents.csv.gz": "mimiciv_hosp.labevents",
        "hosp/patients.csv.gz": "mimiciv_hosp.patients",
        "hosp/services.csv.gz": "mimiciv_hosp.services",
        "icu/chartevents.csv.gz": "mimiciv_icu.chartevents",
        "icu/icustays.csv.gz": "mimiciv_icu.icustays",
        "icu/outputevents.csv.gz": "mimiciv_icu.outputevents",
    }
    for relative, table in mapping.items():
        connection.execute(
            f"CREATE TABLE {table} AS SELECT * FROM {_scan(mimic_root / relative, RAW_SCHEMAS[relative])}"
        )
    execute_untracked(
        connection,
        concepts=(*SAPSII_SPEC.concepts, SAPSII_SPEC.score_concept),
        vendor_root=vendor_root,
    )
