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
    canonical_json_hash,
    software_versions,
    utc_now,
)
from mimic_clinical_scores.common.staging import staging_statistics
from mimic_clinical_scores.common.units import require_unit_validation, unit_validation_statistics
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
    if getattr(specification, "output_granularity", "stay") == "stay_hour":
        overall["score_rows"] = overall.pop("cohort_rows")
        overall["cohort_stays"] = total_cohort
        overall["scored_stays"] = int(
            connection.execute(
                f"SELECT COUNT(DISTINCT stay_id) FROM {specification.score_table}"
            ).fetchone()[0]
        )
        if getattr(specification, "requires_outtime", False):
            overall["excluded_stays_without_usable_outtime"] = total_cohort - overall["scored_stays"]
    else:
        overall["cohort_rows"] = total_cohort
    stratified = {
        "shorter_than_24h": one("short IS TRUE"),
        "at_least_24h": one("short IS FALSE"),
        "unknown_length": one("short IS NULL"),
    }
    if getattr(specification, "output_granularity", "stay") == "stay_hour":
        for metrics in stratified.values():
            metrics["score_rows"] = metrics.pop("cohort_rows")
    overall.update(
        {
            "unique_stay_ids": unique_cohort,
            "matched_icu_stays": matched,
            "stratified": stratified,
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
        "unknown_length": "i.outtime IS NULL",
    }
    denominator_field = (
        "row_count"
        if getattr(specification, "output_granularity", "stay") == "stay_hour"
        else "cohort_size"
    )
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
                    denominator_field: cohort_size,
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
    identity_row = connection.execute(
        "SELECT identity_hash, payload_json FROM pipeline_meta.run_identity WHERE singleton"
    ).fetchone()
    if identity_row is None or identity_row[0] != identity_hash:
        raise ExportError("Cannot export from a database with a different run identity")
    database_identity = json.loads(identity_row[1])
    if database_identity.get("score_name") != specification.name:
        raise ExportError("Selected score differs from the immutable database identity")
    if database_identity.get("mimic_version") != mimic_version:
        raise ExportError("MIMIC version differs from the immutable database identity")
    scores_path = output_directory / "scores.parquet"
    missingness_path = output_directory / "score_missingness.parquet"
    component_path = output_directory / "component_missingness.csv"
    coverage_path = output_directory / "coverage.json"
    staging_path = output_directory / "staging_statistics.json"
    unit_validation_path = output_directory / "unit_validation.json"
    manifest_path = output_directory / "run_manifest.json"

    _atomic_copy_parquet(connection, specification.scores_projection_sql(), scores_path)
    _atomic_copy_parquet(connection, specification.missingness_projection_sql(), missingness_path)
    _atomic_write_csv(
        component_path,
        [
            "component", "short_stay_stratum",
            (
                "row_count"
                if getattr(specification, "output_granularity", "stay") == "stay_hour"
                else "cohort_size"
            ),
            "observed_count",
            "missing_count", "missing_percentage",
        ],
        component_missingness_rows(connection, specification),
    )
    coverage = calculate_coverage(connection, specification)
    require_unit_validation(connection, identity_hash=identity_hash)
    stats = staging_statistics(connection)
    unit_stats = unit_validation_statistics(connection)
    atomic_write_json(coverage_path, coverage)
    atomic_write_json(staging_path, stats)
    atomic_write_json(unit_validation_path, unit_stats)

    started = connection.execute(
        "SELECT CAST(created_at AS VARCHAR) FROM pipeline_meta.run_identity WHERE singleton"
    ).fetchone()[0]
    outputs = {
        "scores": str(scores_path.resolve()),
        "score_missingness": str(missingness_path.resolve()),
        "component_missingness": str(component_path.resolve()),
        "coverage": str(coverage_path.resolve()),
        "staging_statistics": str(staging_path.resolve()),
        "unit_validation": str(unit_validation_path.resolve()),
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
        "output_granularity": getattr(specification, "output_granularity", "stay"),
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
        "unit_validation": unit_stats,
        "coverage": coverage,
        "output_paths": outputs,
        "start_timestamp_utc": started,
        "completion_timestamp_utc": utc_now(),
    }
    atomic_write_json(manifest_path, manifest)
    return manifest


def _query_schema(
    connection: duckdb.DuckDBPyConnection, query: str
) -> tuple[tuple[str, str], ...]:
    cursor = connection.execute(f"SELECT * FROM ({query}) AS projection LIMIT 0")
    return tuple((str(column[0]), str(column[1])) for column in cursor.description)


def _quoted_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _validate_parquet_projection(
    connection: duckdb.DuckDBPyConnection,
    *,
    path: Path,
    query: str,
    label: str,
    key_columns: tuple[str, ...],
) -> None:
    """Require an exported Parquet file to equal its declared projection exactly."""

    expected_schema = _query_schema(connection, query)
    actual_query = f"SELECT * FROM read_parquet({_literal(str(path.resolve()))})"
    observed_schema = _query_schema(connection, actual_query)
    if observed_schema != expected_schema:
        raise ExportError(
            f"{label} schema differs from the declared projection: "
            f"observed={observed_schema}, expected={expected_schema}"
        )
    expected_columns = tuple(name for name, _ in expected_schema)
    missing_keys = [column for column in key_columns if column not in expected_columns]
    if missing_keys:
        raise ExportError(f"{label} projection lacks primary-key columns: {missing_keys}")

    join = " AND ".join(
        f"expected.{_quoted_identifier(column)} = actual.{_quoted_identifier(column)}"
        for column in key_columns
    )
    differs = " OR ".join(
        f"expected.{_quoted_identifier(column)} IS DISTINCT FROM "
        f"actual.{_quoted_identifier(column)}"
        for column in expected_columns
    )
    first_key = _quoted_identifier(key_columns[0])
    mismatch = int(
        connection.execute(
            f"""
            WITH expected AS ({query}),
                 actual AS (SELECT * FROM read_parquet({_literal(str(path.resolve()))}))
            SELECT COUNT(*)
            FROM expected FULL OUTER JOIN actual ON {join}
            WHERE expected.{first_key} IS NULL
               OR actual.{first_key} IS NULL
               OR {differs}
            """
        ).fetchone()[0]
    )
    if mismatch:
        raise ExportError(f"{label} differs from its declared projection in {mismatch} rows")


def _validate_hourly_timestamps(
    connection: duckdb.DuckDBPyConnection,
    *,
    scores_path: Path,
    specification: ScoreSpecification,
) -> None:
    path = _literal(str(scores_path.resolve()))
    if specification.name == "sofa_hourly_14d":
        invalid = int(
            connection.execute(
                f"""
                SELECT COUNT(*) FROM read_parquet({path})
                WHERE hour_start IS DISTINCT FROM intime + hour_index * INTERVAL '1' HOUR
                   OR hour_end IS DISTINCT FROM CASE
                        WHEN outtime IS NULL
                          THEN intime + (hour_index + 1) * INTERVAL '1' HOUR
                        ELSE LEAST(
                          intime + (hour_index + 1) * INTERVAL '1' HOUR, outtime
                        )
                      END
                   OR hour_end <= hour_start
                   OR trailing_window_end IS DISTINCT FROM hour_end
                   OR trailing_window_start IS DISTINCT FROM hour_end - INTERVAL '24' HOUR
                """
            ).fetchone()[0]
        )
        expected_rows = int(
            connection.execute(
                """
                SELECT SUM(
                  CASE WHEN outtime IS NULL THEN 336
                       WHEN outtime > intime THEN LEAST(
                         336,
                         TRY_CAST(CEIL(
                           DATE_DIFF('microseconds', intime, outtime) / 3600000000.0
                         ) AS INTEGER)
                       )
                       ELSE 0 END
                )
                FROM mimiciv_icu.icustays
                """
            ).fetchone()[0]
        )
    elif specification.name == "sofa_hourly_reverse_7d":
        invalid = int(
            connection.execute(
                f"""
                SELECT COUNT(*) FROM read_parquet({path})
                WHERE hour_end IS DISTINCT FROM
                        outtime - hours_before_discharge * INTERVAL '1' HOUR
                   OR hour_start IS DISTINCT FROM GREATEST(
                        intime, outtime - (hours_before_discharge + 1) * INTERVAL '1' HOUR
                      )
                   OR hour_end <= hour_start
                   OR trailing_window_end IS DISTINCT FROM hour_end
                   OR trailing_window_start IS DISTINCT FROM hour_end - INTERVAL '24' HOUR
                """
            ).fetchone()[0]
        )
        expected_rows = int(
            connection.execute(
                """
                SELECT COALESCE(SUM(LEAST(
                  168,
                  TRY_CAST(CEIL(
                    DATE_DIFF('microseconds', intime, outtime) / 3600000000.0
                  ) AS INTEGER)
                )), 0)
                FROM mimiciv_icu.icustays
                WHERE outtime IS NOT NULL AND outtime > intime
                """
            ).fetchone()[0]
        )
    else:
        return
    if invalid:
        raise ExportError(f"Invalid hourly timestamps or trailing windows in {invalid} rows")
    observed_rows = int(pq.read_metadata(scores_path).num_rows)
    if observed_rows != expected_rows:
        raise ExportError(
            f"Hourly row count differs from duration-derived expectation: "
            f"observed={observed_rows}, expected={expected_rows}"
        )


def validate_exports(
    connection: duckdb.DuckDBPyConnection,
    *,
    output_directory: Path,
    identity_hash: str,
    specification: ScoreSpecification | None = None,
) -> dict[str, Any]:
    required = (
        "scores.parquet", "score_missingness.parquet", "component_missingness.csv",
        "coverage.json", "staging_statistics.json", "unit_validation.json",
        "run_manifest.json",
    )
    missing = [name for name in required if not (output_directory / name).is_file()]
    if missing:
        raise ExportError("Missing outputs: " + ", ".join(missing))
    manifest = json.loads((output_directory / "run_manifest.json").read_text(encoding="utf-8"))
    if manifest.get("run_identity_hash") != identity_hash:
        raise ExportError("run_manifest identity does not match the database")
    identity_row = connection.execute(
        "SELECT identity_hash, payload_json FROM pipeline_meta.run_identity WHERE singleton"
    ).fetchone()
    if identity_row is None or identity_row[0] != identity_hash:
        raise ExportError("Database run identity does not match the requested validation identity")
    identity_payload = json.loads(identity_row[1])
    if specification is None:
        from mimic_clinical_scores.scores.registry import get_score

        specification = get_score(str(manifest.get("score_name", "saps_ii")))
    require_unit_validation(connection, identity_hash=identity_hash)
    expected_unit_validation = unit_validation_statistics(connection)

    scores_path = output_directory / "scores.parquet"
    missingness_path = output_directory / "score_missingness.parquet"
    cohort_rows = int(connection.execute("SELECT COUNT(*) FROM pipeline_meta.cohort").fetchone()[0])
    expected_rows = int(connection.execute(f"SELECT COUNT(*) FROM {specification.score_table}").fetchone()[0])
    hourly = getattr(specification, "output_granularity", "stay") == "stay_hour"
    if pq.read_metadata(scores_path).num_rows != expected_rows:
        expected_description = "the hourly score table" if hourly else "one row per cohort stay"
        raise ExportError(f"scores.parquet does not match {expected_description}")
    if pq.read_metadata(missingness_path).num_rows != expected_rows:
        raise ExportError("score_missingness.parquet row count differs from scores.parquet")

    exported_hour_column = None
    if hourly:
        exported_score_columns = tuple(pq.read_schema(scores_path).names)
        exported_hour_column = (
            "hour_index"
            if "hour_index" in exported_score_columns
            else "hours_before_discharge"
        )
    parquet_key = ("stay_id",) + ((exported_hour_column,) if exported_hour_column else ())
    _validate_parquet_projection(
        connection,
        path=scores_path,
        query=specification.scores_projection_sql(),
        label="scores.parquet",
        key_columns=parquet_key,
    )
    _validate_parquet_projection(
        connection,
        path=missingness_path,
        query=specification.missingness_projection_sql(),
        label="score_missingness.parquet",
        key_columns=parquet_key,
    )

    if hourly:
        hour_column = str(exported_hour_column)
        maximum_hour = int(getattr(specification, "maximum_hour_index", 335))
        eligible_stays = int(
            connection.execute(
                "SELECT COUNT(*) FROM mimiciv_icu.icustays WHERE "
                + (
                    "outtime IS NOT NULL AND outtime > intime"
                    if getattr(specification, "requires_outtime", False)
                    else "outtime IS NULL OR outtime > intime"
                )
            ).fetchone()[0]
        )
        score_counts = connection.execute(
            f"""
            SELECT COUNT(*), COUNT(DISTINCT stay_id),
                   COUNT(*) FILTER (WHERE stay_id IS NULL OR {hour_column} IS NULL),
                   COUNT(*) - COUNT(DISTINCT (stay_id, {hour_column}))
            FROM read_parquet({_literal(str(scores_path.resolve()))})
            """
        ).fetchone()
        if score_counts != (expected_rows, eligible_stays, 0, 0):
            raise ExportError(f"Invalid hourly score identifiers: {score_counts}")
        invalid_grid = connection.execute(
            f"""
            SELECT COUNT(*) FROM (
              SELECT stay_id, MIN({hour_column}) AS min_hr, MAX({hour_column}) AS max_hr,
                     COUNT(*) AS rows
              FROM read_parquet({_literal(str(scores_path.resolve()))})
              GROUP BY stay_id
              HAVING min_hr <> 0 OR max_hr > {maximum_hour} OR rows <> max_hr + 1
            ) invalid
            """
        ).fetchone()[0]
        if invalid_grid:
            raise ExportError(f"Invalid or non-contiguous hourly grids for {invalid_grid} stays")
        _validate_hourly_timestamps(
            connection, scores_path=scores_path, specification=specification
        )
    else:
        score_counts = connection.execute(
            f"""
            SELECT COUNT(*), COUNT(DISTINCT stay_id), COUNT(*) FILTER (WHERE stay_id IS NULL)
            FROM read_parquet({_literal(str(scores_path.resolve()))})
            """
        ).fetchone()
        if score_counts != (cohort_rows, cohort_rows, 0):
            raise ExportError(f"Invalid score identifiers: {score_counts}")
    expected_stay_query = (
        "SELECT stay_id FROM mimiciv_icu.icustays WHERE outtime IS NOT NULL AND outtime > intime"
        if hourly and getattr(specification, "requires_outtime", False)
        else (
            "SELECT stay_id FROM mimiciv_icu.icustays "
            "WHERE outtime IS NULL OR outtime > intime"
            if hourly
            else "SELECT stay_id FROM pipeline_meta.cohort"
        )
    )
    mismatch = connection.execute(
        f"""
        SELECT COUNT(*) FROM (
          ({expected_stay_query}
           EXCEPT SELECT stay_id FROM read_parquet({_literal(str(scores_path.resolve()))}))
          UNION ALL
          (SELECT stay_id FROM read_parquet({_literal(str(scores_path.resolve()))})
           EXCEPT {expected_stay_query})
        ) differences
        """
    ).fetchone()[0]
    if mismatch:
        raise ExportError(f"Score stay IDs differ from the cohort ({mismatch} differences)")

    expected_component_rows = component_missingness_rows(connection, specification)
    with (output_directory / "component_missingness.csv").open(
        "r", encoding="utf-8", newline=""
    ) as stream:
        observed_component_rows = list(csv.DictReader(stream))
    serialized_expected = [
        {key: "" if value is None else str(value) for key, value in row.items()}
        for row in expected_component_rows
    ]
    if observed_component_rows != serialized_expected:
        raise ExportError("component_missingness.csv differs from current score-table counts")

    coverage = json.loads((output_directory / "coverage.json").read_text(encoding="utf-8"))
    expected_coverage = calculate_coverage(connection, specification)
    if coverage != expected_coverage:
        raise ExportError("coverage.json differs from current score-table coverage")
    staged = json.loads(
        (output_directory / "staging_statistics.json").read_text(encoding="utf-8")
    )
    expected_staged = staging_statistics(connection)
    if staged != expected_staged:
        raise ExportError("staging_statistics.json differs from the database audit state")
    if manifest.get("coverage") != coverage or manifest.get("staging_statistics") != staged:
        raise ExportError("run_manifest embedded summaries differ from standalone outputs")
    exported_unit_validation = json.loads(
        (output_directory / "unit_validation.json").read_text(encoding="utf-8")
    )
    if exported_unit_validation != expected_unit_validation:
        raise ExportError("unit_validation.json differs from the database audit state")
    if manifest.get("unit_validation") != expected_unit_validation:
        raise ExportError("run_manifest unit assurance differs from the database audit state")
    expected_output_paths = {
        "scores": str(scores_path.resolve()),
        "score_missingness": str(missingness_path.resolve()),
        "component_missingness": str((output_directory / "component_missingness.csv").resolve()),
        "coverage": str((output_directory / "coverage.json").resolve()),
        "staging_statistics": str((output_directory / "staging_statistics.json").resolve()),
        "unit_validation": str((output_directory / "unit_validation.json").resolve()),
        "run_manifest": str((output_directory / "run_manifest.json").resolve()),
    }
    if manifest.get("output_paths") != expected_output_paths:
        raise ExportError("run_manifest output paths differ from the validated artifacts")
    if manifest.get("score_name") != specification.name:
        raise ExportError("run_manifest score name differs from the selected specification")
    if manifest.get("output_granularity") != getattr(
        specification, "output_granularity", "stay"
    ):
        raise ExportError("run_manifest output granularity differs from the specification")
    identity_checks = {
        "score_name": manifest.get("score_name"),
        "mimic_version": manifest.get("mimic_version"),
        "mimic_code_release": manifest.get("official_mimic_code_release"),
        "mimic_code_commit": manifest.get("official_mimic_code_commit"),
        "source_manifest_sha256": manifest.get("upstream_source_manifest_sha256"),
        "dependency_order": manifest.get("concept_dependency_order"),
        "sql_hashes": manifest.get("sql_hashes"),
        "vendor_hashes": manifest.get("vendor_hashes"),
        "item_manifest_version": manifest.get("item_id_manifest_version"),
        "item_manifest_sha256": manifest.get("item_id_manifest_sha256"),
        "raw_source_fingerprints": {
            name: metadata.get("source_fingerprint")
            for name, metadata in manifest.get("raw_source_metadata", {}).items()
        },
        "code_hash": canonical_json_hash(manifest.get("source_code_hashes", {})),
    }
    for key, observed in identity_checks.items():
        if identity_payload.get(key) != observed:
            raise ExportError(f"run_manifest {key} differs from the immutable database identity")
    insecure = [
        name for name in required
        if (output_directory / name).stat().st_mode & 0o077
    ]
    if insecure:
        raise ExportError("Output files are not protected with owner-only permissions: " + ", ".join(insecure))
    result = {"valid": True, "cohort_rows": cohort_rows, "checked_outputs": list(required)}
    if hourly:
        result["score_rows"] = expected_rows
        if getattr(specification, "requires_outtime", False):
            result["scored_stays"] = eligible_stays
            result["excluded_stays"] = cohort_rows - eligible_stays
        maximum_observed = connection.execute(
            f"SELECT MAX({hour_column}) FROM read_parquet({_literal(str(scores_path.resolve()))})"
        ).fetchone()[0]
        result["maximum_hour_index"] = (
            int(maximum_observed) if maximum_observed is not None else None
        )
    return result
