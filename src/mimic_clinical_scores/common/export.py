"""Atomic protected exports, missingness audits, coverage, and run manifest."""

from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

import duckdb
import pyarrow.parquet as pq

from mimic_clinical_scores.common.duckdb import DuckDBSettings, read_artifact_rows
from mimic_clinical_scores.common.provenance import (
    atomic_write_json,
    software_versions,
    utc_now,
)
from mimic_clinical_scores.common.staging import staging_statistics
from mimic_clinical_scores.scores.saps_ii.specification import SAPSII_SPEC
from mimic_clinical_scores.common.specification import ScoreSpecification


class ExportError(RuntimeError):
    """Raised when outputs would be ambiguous or fail validation."""


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _prepare_output_directory(output_directory: Path, identity_hash: str) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    output_directory.chmod(0o700)
    existing_manifest = output_directory / "run_manifest.json"
    if existing_manifest.is_file():
        existing = json.loads(existing_manifest.read_text(encoding="utf-8"))
        if existing.get("run_identity_hash") != identity_hash:
            raise ExportError(
                f"Output directory belongs to a different run identity: {output_directory}. "
                "Use a new --output-dir or remove it deliberately."
            )


def _atomic_copy_parquet(
    connection: duckdb.DuckDBPyConnection, query: str, destination: Path
) -> None:
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        connection.execute(
            f"COPY ({query}) TO {_literal(str(temporary.resolve()))} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        temporary.chmod(0o600)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _coverage_metric(observed: int, denominator: int) -> dict[str, Any]:
    return {
        "count": observed,
        "percentage": (100.0 * observed / denominator) if denominator else None,
    }


def calculate_coverage(
    connection: duckdb.DuckDBPyConnection,
    specification: ScoreSpecification = SAPSII_SPEC,
) -> dict[str, Any]:
    components_complete = " AND ".join(
        f"{column} IS NOT NULL" for column in specification.component_columns
    )
    score_column = specification.score_columns[0]
    probability_column = (
        specification.probability_columns[0] if specification.probability_columns else None
    )
    base = f"""
        SELECT s.*, DATE_DIFF('microseconds', i.intime, i.outtime) / 3600000000.0 < 24.0 AS short
        FROM {specification.score_table} s
        INNER JOIN mimiciv_icu.icustays i USING (stay_id)
    """

    def one(where: str = "TRUE") -> dict[str, Any]:
        probability_select = (
            f"COUNT(*) FILTER (WHERE {probability_column} IS NOT NULL)"
            if probability_column else "NULL"
        )
        row = connection.execute(
            f"""
            SELECT COUNT(*) AS rows,
                   COUNT(*) FILTER (WHERE {score_column} IS NOT NULL) AS score_rows,
                   {probability_select} AS probability_rows,
                   COUNT(*) FILTER (WHERE {components_complete}) AS complete_rows
            FROM ({base}) coverage WHERE {where}
            """
        ).fetchone()
        total, score, probability, complete = row
        total, score, complete = int(total), int(score), int(complete)
        probability_by_column = {
            column: _coverage_metric(
                int(
                    connection.execute(
                        f"SELECT COUNT(*) FILTER (WHERE {column} IS NOT NULL) "
                        f"FROM ({base}) coverage WHERE {where}"
                    ).fetchone()[0]
                ),
                total,
            )
            for column in specification.probability_columns
        }
        return {
            "cohort_rows": total,
            "score_coverage": _coverage_metric(score, total),
            "probability_coverage": (
                _coverage_metric(int(probability), total)
                if probability is not None
                else {"count": None, "percentage": None, "applicable": False}
            ),
            "probability_coverage_by_column": probability_by_column,
            "complete_component_coverage": _coverage_metric(complete, total),
            "any_component_missing_coverage": _coverage_metric(total - complete, total),
        }

    total_cohort = int(connection.execute("SELECT COUNT(*) FROM pipeline_meta.cohort").fetchone()[0])
    unique_cohort = int(
        connection.execute("SELECT COUNT(DISTINCT stay_id) FROM pipeline_meta.cohort").fetchone()[0]
    )
    matched = int(connection.execute("SELECT COUNT(*) FROM mimiciv_icu.icustays").fetchone()[0])
    overall = one()
    overall.update(
        {
            "cohort_rows": total_cohort,
            "unique_stay_ids": unique_cohort,
            "matched_icu_stays": matched,
            "stratified": {
                "shorter_than_24h": one("short IS TRUE"),
                "at_least_24h": one("short IS FALSE"),
                "unknown_length": one("short IS NULL"),
            },
        }
    )
    return overall


def component_missingness_rows(
    connection: duckdb.DuckDBPyConnection,
    specification: ScoreSpecification = SAPSII_SPEC,
) -> list[dict[str, Any]]:
    strata = {
        "all": "TRUE",
        "shorter_than_24h": "DATE_DIFF('microseconds', i.intime, i.outtime) / 3600000000.0 < 24.0",
        "at_least_24h": "DATE_DIFF('microseconds', i.intime, i.outtime) / 3600000000.0 >= 24.0",
    }
    rows: list[dict[str, Any]] = []
    for component in specification.component_columns:
        for stratum, predicate in strata.items():
            cohort_size, observed = connection.execute(
                f"""
                SELECT COUNT(*), COUNT(s.{component})
                FROM {specification.score_table} s
                INNER JOIN mimiciv_icu.icustays i USING (stay_id)
                WHERE {predicate}
                """
            ).fetchone()
            cohort_size = int(cohort_size)
            observed = int(observed)
            missing = cohort_size - observed
            rows.append(
                {
                    "component": component,
                    "short_stay_stratum": stratum,
                    "cohort_size": cohort_size,
                    "observed_count": observed,
                    "missing_count": missing,
                    "missing_percentage": 100.0 * missing / cohort_size if cohort_size else None,
                }
            )
    return rows


def _slurm_metadata() -> dict[str, str]:
    keep = {
        "SLURM_JOB_ID", "SLURM_JOB_NAME", "SLURM_CPUS_PER_TASK", "SLURM_MEM_PER_NODE",
        "SLURM_JOB_PARTITION", "SLURM_SUBMIT_DIR", "SLURM_ARRAY_JOB_ID", "SLURM_ARRAY_TASK_ID",
    }
    return {key: os.environ[key] for key in sorted(keep) if key in os.environ}


def export_all(
    connection: duckdb.DuckDBPyConnection,
    *,
    output_directory: Path,
    identity_hash: str,
    mode: str,
    mimic_version: str,
    cohort_manifest: dict[str, Any] | None,
    preflight: dict[str, Any],
    runtime: DuckDBSettings,
    command_line: list[str] | None = None,
    specification: ScoreSpecification = SAPSII_SPEC,
) -> dict[str, Any]:
    _prepare_output_directory(output_directory, identity_hash)
    scores_path = output_directory / "scores.parquet"
    missingness_path = output_directory / "score_missingness.parquet"
    component_path = output_directory / "component_missingness.csv"
    coverage_path = output_directory / "coverage.json"
    staging_path = output_directory / "staging_statistics.json"
    manifest_path = output_directory / "run_manifest.json"

    _atomic_copy_parquet(connection, specification.scores_projection_sql(), scores_path)
    _atomic_copy_parquet(connection, specification.missingness_projection_sql(), missingness_path)
    _atomic_write_csv(
        component_path,
        [
            "component", "short_stay_stratum", "cohort_size", "observed_count",
            "missing_count", "missing_percentage",
        ],
        component_missingness_rows(connection, specification),
    )
    coverage = calculate_coverage(connection, specification)
    stats = staging_statistics(connection)
    atomic_write_json(coverage_path, coverage)
    atomic_write_json(staging_path, stats)

    started = connection.execute(
        "SELECT CAST(created_at AS VARCHAR) FROM pipeline_meta.run_identity WHERE singleton"
    ).fetchone()[0]
    outputs = {
        "scores": str(scores_path.resolve()),
        "score_missingness": str(missingness_path.resolve()),
        "component_missingness": str(component_path.resolve()),
        "coverage": str(coverage_path.resolve()),
        "staging_statistics": str(staging_path.resolve()),
        "run_manifest": str(manifest_path.resolve()),
    }
    manifest = {
        "run_identity_hash": identity_hash,
        "run_mode": mode,
        "cohort_source": (cohort_manifest or {}).get(
            "cohort_source_path", preflight["cohort"]["path"]
        ),
        "cohort_source_fingerprint": (cohort_manifest or {}).get(
            "cohort_source_fingerprint", preflight["cohort"]["fingerprint"]
        ),
        "cohort_allowlist_path": preflight["cohort"]["path"],
        "cohort_allowlist_fingerprint": preflight["cohort"]["fingerprint"],
        "cohort_ordered_id_hash": preflight["cohort"]["ordered_id_hash"],
        "sample_seed": (cohort_manifest or {}).get("random_seed"),
        "selected_id_hash": (cohort_manifest or {}).get("ordered_selected_id_sha256"),
        "mimic_version": mimic_version,
        "score_name": specification.name,
        "score_provenance": specification.provenance_label,
        "upstream_source_manifest": preflight["official"].get("adaptation_source_manifest"),
        "upstream_source_manifest_sha256": preflight["official"].get("source_manifest_sha256"),
        "official_mimic_code_release": specification.mimic_code_release,
        "official_mimic_code_commit": specification.mimic_code_commit,
        "concept_dependency_order": preflight["official"]["dependency_order"],
        "sql_hashes": preflight["official"]["sql_hashes"],
        "vendor_hashes": preflight["official"]["vendor_hashes"],
        "raw_source_metadata": preflight["raw_sources"],
        "item_id_manifest_version": preflight["official"]["item_manifest_version"],
        "item_id_manifest_sha256": preflight["official"]["item_manifest_sha256"],
        "source_code_hashes": preflight["code_hashes"],
        "runtime_configuration": {
            "database": str(runtime.database.resolve()),
            "threads": runtime.threads,
            "memory_limit": runtime.memory_limit,
            "spill_directory": str(
                (runtime.spill_directory or runtime.database.parent / "spill").resolve()
            ),
        },
        "software_versions": software_versions(),
        "command_line_arguments": command_line if command_line is not None else sys.argv,
        "slurm_metadata": _slurm_metadata(),
        "artifacts": read_artifact_rows(connection),
        "staging_statistics": stats,
        "coverage": coverage,
        "output_paths": outputs,
        "start_timestamp_utc": started,
        "completion_timestamp_utc": utc_now(),
    }
    atomic_write_json(manifest_path, manifest)
    return manifest


def validate_exports(
    connection: duckdb.DuckDBPyConnection,
    *,
    output_directory: Path,
    identity_hash: str,
) -> dict[str, Any]:
    required = (
        "scores.parquet", "score_missingness.parquet", "component_missingness.csv",
        "coverage.json", "staging_statistics.json", "run_manifest.json",
    )
    missing = [name for name in required if not (output_directory / name).is_file()]
    if missing:
        raise ExportError("Missing outputs: " + ", ".join(missing))
    manifest = json.loads((output_directory / "run_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("run_identity_hash") != identity_hash:
        raise ExportError("run_manifest identity does not match the database")

    scores_path = output_directory / "scores.parquet"
    missingness_path = output_directory / "score_missingness.parquet"
    cohort_rows = int(connection.execute("SELECT COUNT(*) FROM pipeline_meta.cohort").fetchone()[0])
    if pq.read_metadata(scores_path).num_rows != cohort_rows:
        raise ExportError("scores.parquet does not contain one row per cohort stay")
    if pq.read_metadata(missingness_path).num_rows != cohort_rows:
        raise ExportError("score_missingness.parquet row count differs from the cohort")

    score_counts = connection.execute(
        f"""
        SELECT COUNT(*), COUNT(DISTINCT stay_id), COUNT(*) FILTER (WHERE stay_id IS NULL)
        FROM read_parquet({_literal(str(scores_path.resolve()))})
        """
    ).fetchone()
    if score_counts != (cohort_rows, cohort_rows, 0):
        raise ExportError(f"Invalid score identifiers: {score_counts}")
    mismatch = connection.execute(
        f"""
        SELECT COUNT(*) FROM (
          (SELECT stay_id FROM pipeline_meta.cohort
           EXCEPT SELECT stay_id FROM read_parquet({_literal(str(scores_path.resolve()))}))
          UNION ALL
          (SELECT stay_id FROM read_parquet({_literal(str(scores_path.resolve()))})
           EXCEPT SELECT stay_id FROM pipeline_meta.cohort)
        ) differences
        """
    ).fetchone()[0]
    if mismatch:
        raise ExportError(f"Score stay IDs differ from the cohort ({mismatch} differences)")
    return {"valid": True, "cohort_rows": cohort_rows, "checked_outputs": list(required)}
