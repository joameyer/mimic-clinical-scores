"""One-pass, normalized, cohort/item/time-filtered raw MIMIC staging."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa

from mimic_clinical_scores.common.cohort import CohortInfo
from mimic_clinical_scores.common.duckdb import (
    PipelineStateError,
    execute_table_artifact,
    table_exists,
)
from mimic_clinical_scores.common.provenance import canonical_json_hash
from mimic_clinical_scores.common.specification import ScoreSpecification
from mimic_clinical_scores.scores.saps_ii.specification import SAPSII_SPEC
from mimic_clinical_scores.scores.saps_ii.staging_rules import (
    CHARTEVENT_FULL_CONTEXT_ITEM_IDS,
    CHARTEVENT_ITEM_IDS,
    LABEVENT_ITEM_IDS,
    OUTPUTEVENT_ITEM_IDS,
    RULES,
)


class StagingError(RuntimeError):
    """Raised if raw data cannot be staged exactly or audited."""


RAW_SCHEMAS: dict[str, tuple[tuple[str, str], ...]] = {
    "hosp/admissions.csv.gz": (
        ("subject_id", "INTEGER"), ("hadm_id", "INTEGER"), ("admittime", "TIMESTAMP"),
        ("dischtime", "TIMESTAMP"), ("deathtime", "TIMESTAMP"),
        ("admission_type", "VARCHAR"), ("admit_provider_id", "VARCHAR"),
        ("admission_location", "VARCHAR"), ("discharge_location", "VARCHAR"),
        ("insurance", "VARCHAR"), ("language", "VARCHAR"),
        ("marital_status", "VARCHAR"), ("race", "VARCHAR"),
        ("edregtime", "TIMESTAMP"), ("edouttime", "TIMESTAMP"),
        ("hospital_expire_flag", "SMALLINT"),
    ),
    "hosp/diagnoses_icd.csv.gz": (
        ("subject_id", "INTEGER"), ("hadm_id", "INTEGER"), ("seq_num", "INTEGER"),
        ("icd_code", "VARCHAR"), ("icd_version", "SMALLINT"),
    ),
    "hosp/labevents.csv.gz": (
        ("labevent_id", "INTEGER"), ("subject_id", "INTEGER"), ("hadm_id", "INTEGER"),
        ("specimen_id", "INTEGER"), ("itemid", "INTEGER"),
        ("order_provider_id", "VARCHAR"), ("charttime", "TIMESTAMP"),
        ("storetime", "TIMESTAMP"), ("value", "VARCHAR"), ("valuenum", "DOUBLE"),
        ("valueuom", "VARCHAR"), ("ref_range_lower", "DOUBLE"),
        ("ref_range_upper", "DOUBLE"), ("flag", "VARCHAR"),
        ("priority", "VARCHAR"), ("comments", "VARCHAR"),
    ),
    "hosp/patients.csv.gz": (
        ("subject_id", "INTEGER"), ("gender", "VARCHAR"), ("anchor_age", "SMALLINT"),
        ("anchor_year", "SMALLINT"), ("anchor_year_group", "VARCHAR"), ("dod", "DATE"),
    ),
    "hosp/services.csv.gz": (
        ("subject_id", "INTEGER"), ("hadm_id", "INTEGER"),
        ("transfertime", "TIMESTAMP"), ("prev_service", "VARCHAR"),
        ("curr_service", "VARCHAR"),
    ),
    "hosp/transfers.csv.gz": (
        ("subject_id", "INTEGER"), ("hadm_id", "INTEGER"), ("transfer_id", "INTEGER"),
        ("eventtype", "VARCHAR"), ("careunit", "VARCHAR"),
        ("intime", "TIMESTAMP"), ("outtime", "TIMESTAMP"),
    ),
    "hosp/procedures_icd.csv.gz": (
        ("subject_id", "INTEGER"), ("hadm_id", "INTEGER"), ("seq_num", "INTEGER"),
        ("chartdate", "DATE"), ("icd_code", "VARCHAR"), ("icd_version", "SMALLINT"),
    ),
    "icu/chartevents.csv.gz": (
        ("subject_id", "INTEGER"), ("hadm_id", "INTEGER"), ("stay_id", "INTEGER"),
        ("caregiver_id", "INTEGER"), ("charttime", "TIMESTAMP"),
        ("storetime", "TIMESTAMP"), ("itemid", "INTEGER"), ("value", "VARCHAR"),
        ("valuenum", "DOUBLE"), ("valueuom", "VARCHAR"), ("warning", "SMALLINT"),
    ),
    "icu/icustays.csv.gz": (
        ("subject_id", "INTEGER"), ("hadm_id", "INTEGER"), ("stay_id", "INTEGER"),
        ("first_careunit", "VARCHAR"), ("last_careunit", "VARCHAR"),
        ("intime", "TIMESTAMP"), ("outtime", "TIMESTAMP"), ("los", "DOUBLE"),
    ),
    "icu/inputevents.csv.gz": (
        ("subject_id", "INTEGER"), ("hadm_id", "INTEGER"), ("stay_id", "INTEGER"),
        ("caregiver_id", "INTEGER"), ("starttime", "TIMESTAMP"), ("endtime", "TIMESTAMP"),
        ("storetime", "TIMESTAMP"), ("itemid", "INTEGER"), ("amount", "DOUBLE"),
        ("amountuom", "VARCHAR"), ("rate", "DOUBLE"), ("rateuom", "VARCHAR"),
        ("orderid", "BIGINT"), ("linkorderid", "BIGINT"),
        ("ordercategoryname", "VARCHAR"), ("secondaryordercategoryname", "VARCHAR"),
        ("ordercomponenttypedescription", "VARCHAR"), ("ordercategorydescription", "VARCHAR"),
        ("patientweight", "DOUBLE"), ("totalamount", "DOUBLE"), ("totalamountuom", "VARCHAR"),
        ("isopenbag", "SMALLINT"), ("continueinnextdept", "SMALLINT"),
        ("statusdescription", "VARCHAR"), ("originalamount", "DOUBLE"),
        ("originalrate", "DOUBLE"),
    ),
    "icu/outputevents.csv.gz": (
        ("subject_id", "INTEGER"), ("hadm_id", "INTEGER"), ("stay_id", "INTEGER"),
        ("caregiver_id", "INTEGER"), ("charttime", "TIMESTAMP"),
        ("storetime", "TIMESTAMP"), ("itemid", "INTEGER"), ("value", "DOUBLE"),
        ("valueuom", "VARCHAR"),
    ),
}

STAGING_ORDER = (
    ("icu/icustays.csv.gz", "mimiciv_icu.icustays"),
    ("hosp/admissions.csv.gz", "mimiciv_hosp.admissions"),
    ("hosp/patients.csv.gz", "mimiciv_hosp.patients"),
    ("hosp/services.csv.gz", "mimiciv_hosp.services"),
    ("hosp/diagnoses_icd.csv.gz", "mimiciv_hosp.diagnoses_icd"),
    ("icu/chartevents.csv.gz", "mimiciv_icu.chartevents"),
    ("hosp/labevents.csv.gz", "mimiciv_hosp.labevents"),
    ("icu/outputevents.csv.gz", "mimiciv_icu.outputevents"),
)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _id_list(values: frozenset[int]) -> str:
    if not values:
        raise StagingError("Empty item-ID declaration")
    return ", ".join(str(value) for value in sorted(values))


def _csv_scan(path: Path, relative: str) -> str:
    columns = ", ".join(
        f"{_literal(name)}: {_literal(sql_type)}" for name, sql_type in RAW_SCHEMAS[relative]
    )
    return (
        f"read_csv({_literal(str(path.resolve()))}, header = true, compression = 'gzip', "
        f"columns = {{{columns}}}, nullstr = '', quote = '\"', escape = '\"', "
        "strict_mode = true)"
    )


def _select_columns(relative: str, alias: str = "raw") -> str:
    return ", ".join(f"{alias}.{name}" for name, _ in RAW_SCHEMAS[relative])


def _filter_sql(
    relative: str,
    qualified_table: str,
    source: Path,
    specification: ScoreSpecification = SAPSII_SPEC,
) -> str:
    scan = _csv_scan(source, relative)
    columns = _select_columns(relative)
    if qualified_table == "mimiciv_icu.icustays":
        predicate = "EXISTS (SELECT 1 FROM pipeline_meta.cohort c WHERE c.stay_id = raw.stay_id)"
    elif qualified_table in {
        "mimiciv_hosp.admissions", "mimiciv_hosp.services", "mimiciv_hosp.diagnoses_icd",
        "mimiciv_hosp.transfers", "mimiciv_hosp.procedures_icd",
    }:
        predicate = (
            "EXISTS (SELECT 1 FROM pipeline_meta.cohort_context c "
            "WHERE c.hadm_id = raw.hadm_id)"
        )
    elif qualified_table == "mimiciv_hosp.patients":
        predicate = (
            "EXISTS (SELECT 1 FROM pipeline_meta.cohort_context c "
            "WHERE c.subject_id = raw.subject_id)"
        )
    elif specification.name == "saps_iii_adapted" and qualified_table == "mimiciv_icu.chartevents":
        predicate = f"""
            raw.itemid IN ({_id_list(specification.item_ids(qualified_table))})
            AND EXISTS (
                SELECT 1 FROM pipeline_meta.cohort_context c
                WHERE c.stay_id = raw.stay_id
                  AND raw.charttime >= c.intime - CASE WHEN raw.itemid=223835 THEN INTERVAL '3' HOUR ELSE INTERVAL '1' HOUR END
                  AND raw.charttime <= c.intime + INTERVAL '1' HOUR
            )
        """
    elif specification.name == "saps_iii_adapted" and qualified_table == "mimiciv_hosp.labevents":
        predicate = f"""
            raw.itemid IN ({_id_list(specification.item_ids(qualified_table))})
            AND EXISTS (
                SELECT 1 FROM pipeline_meta.cohort_context c
                WHERE c.hadm_id = raw.hadm_id
                  AND raw.charttime >= c.intime - INTERVAL '1' HOUR
                  AND raw.charttime <= c.intime + INTERVAL '1' HOUR
            )
        """
    elif specification.name == "saps_iii_adapted" and qualified_table == "mimiciv_icu.inputevents":
        predicate = f"""
            raw.itemid IN ({_id_list(specification.item_ids(qualified_table))})
            AND EXISTS (
                SELECT 1 FROM pipeline_meta.cohort_context c
                WHERE c.stay_id = raw.stay_id
                  AND raw.starttime < c.intime
                  AND raw.endtime > c.intime - INTERVAL '24' HOUR
            )
        """
    elif specification.name == "sofa_first_day_adapted" and qualified_table == "mimiciv_icu.chartevents":
        all_ids = specification.item_ids(qualified_table)
        full_context_ids = specification.full_context_item_ids(qualified_table)
        predicate = f"""
            raw.itemid IN ({_id_list(all_ids)})
            AND EXISTS (
                SELECT 1 FROM pipeline_meta.cohort_context c
                WHERE c.stay_id = raw.stay_id
                  AND (
                    raw.itemid IN ({_id_list(full_context_ids)})
                    OR (
                      raw.itemid NOT IN ({_id_list(full_context_ids)})
                      AND raw.charttime >= c.intime - INTERVAL '6' HOUR
                      AND raw.charttime <= c.intime + INTERVAL '24' HOUR
                    )
                  )
            )
        """
    elif specification.name == "sofa_first_day_adapted" and qualified_table == "mimiciv_hosp.labevents":
        predicate = f"""
            raw.itemid IN ({_id_list(specification.item_ids(qualified_table))})
            AND EXISTS (
                SELECT 1 FROM pipeline_meta.cohort_context c
                WHERE c.subject_id = raw.subject_id
                  AND raw.charttime >= c.intime - INTERVAL '6' HOUR
                  AND raw.charttime <= c.intime + INTERVAL '24' HOUR
            )
        """
    elif specification.name == "sofa_first_day_adapted" and qualified_table == "mimiciv_icu.inputevents":
        predicate = f"""
            raw.itemid IN ({_id_list(specification.item_ids(qualified_table))})
            AND EXISTS (
                SELECT 1 FROM pipeline_meta.cohort_context c
                WHERE c.stay_id = raw.stay_id
                  AND raw.starttime >= c.intime - INTERVAL '6' HOUR
                  AND raw.starttime <= c.intime + INTERVAL '24' HOUR
            )
        """
    elif specification.name == "sofa_first_day_adapted" and qualified_table == "mimiciv_icu.outputevents":
        predicate = f"""
            raw.itemid IN ({_id_list(specification.item_ids(qualified_table))})
            AND EXISTS (
                SELECT 1 FROM pipeline_meta.cohort_context c
                WHERE c.stay_id = raw.stay_id
                  AND raw.charttime >= c.intime
                  AND raw.charttime <= c.intime + INTERVAL '24' HOUR
            )
        """
    elif qualified_table == "mimiciv_icu.chartevents":
        predicate = f"""
            raw.itemid IN ({_id_list(CHARTEVENT_ITEM_IDS)})
            AND EXISTS (
                SELECT 1 FROM pipeline_meta.cohort_context c
                WHERE c.stay_id = raw.stay_id
                  AND (
                    raw.itemid IN ({_id_list(CHARTEVENT_FULL_CONTEXT_ITEM_IDS)})
                    OR (
                      raw.itemid = 220277
                      AND raw.charttime >= c.intime - INTERVAL '2' HOUR
                      AND raw.charttime <= c.intime + INTERVAL '24' HOUR
                    )
                    OR (
                      raw.itemid NOT IN ({_id_list(CHARTEVENT_FULL_CONTEXT_ITEM_IDS)})
                      AND raw.itemid <> 220277
                      AND raw.charttime > c.intime
                      AND raw.charttime <= c.intime + INTERVAL '24' HOUR
                    )
                  )
            )
        """
    elif qualified_table == "mimiciv_hosp.labevents":
        predicate = f"""
            raw.itemid IN ({_id_list(LABEVENT_ITEM_IDS)})
            AND EXISTS (
                SELECT 1 FROM pipeline_meta.cohort_context c
                WHERE c.hadm_id = raw.hadm_id
                  AND raw.charttime > c.intime
                  AND raw.charttime <= c.intime + INTERVAL '24' HOUR
            )
        """
    elif qualified_table == "mimiciv_icu.outputevents":
        predicate = f"""
            raw.itemid IN ({_id_list(OUTPUTEVENT_ITEM_IDS)})
            AND EXISTS (
                SELECT 1 FROM pipeline_meta.cohort_context c
                WHERE c.stay_id = raw.stay_id
                  AND raw.charttime > c.intime
                  AND raw.charttime <= c.intime + INTERVAL '24' HOUR
            )
        """
    else:
        raise StagingError(f"No staging filter for {qualified_table}")
    return f"CREATE TABLE {qualified_table} AS SELECT {columns} FROM {scan} AS raw WHERE {predicate}"


def _profile_source_rows(profile_path: Path) -> int:
    if not profile_path.is_file():
        raise StagingError(f"DuckDB did not create scan profile {profile_path}")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    scanned: list[int] = []

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            name = str(node.get("operator_name", "")).upper()
            if "READ_CSV" in name:
                extra = node.get("extra_info") or {}
                if "Filters" in extra:
                    raise StagingError(
                        "DuckDB pushed a filter into READ_CSV, so the full source row count "
                        "cannot be audited from this one-pass profile"
                    )
                if node.get("operator_cardinality") is not None:
                    # DuckDB 1.3 reports an estimate in operator_rows_scanned for
                    # READ_CSV. With no scan-level Filters, operator_cardinality is
                    # the exact number emitted by the sole source scan.
                    scanned.append(int(node["operator_cardinality"]))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(profile)
    if not scanned:
        fallback = profile.get("cumulative_rows_scanned")
        if fallback is not None:
            scanned.append(int(fallback))
    if not scanned:
        raise StagingError(f"Could not audit source row count from profile {profile_path}")
    return max(scanned)


def build_cohort_tables(
    connection: duckdb.DuckDBPyConnection,
    cohort: CohortInfo,
    identity_hash: str,
) -> None:
    arrow = pa.table({"stay_id": pa.array(cohort.stay_ids, type=pa.int64())})
    connection.register("cohort_arrow", arrow)
    try:
        execute_table_artifact(
            connection,
            artifact_name="cohort_allowlist",
            artifact_type="cohort",
            qualified_table="pipeline_meta.cohort",
            identity_hash=identity_hash,
            artifact_hash=cohort.ordered_id_hash,
            sql=(
                "CREATE TABLE pipeline_meta.cohort AS "
                "SELECT CAST(stay_id AS INTEGER) AS stay_id FROM cohort_arrow ORDER BY stay_id"
            ),
        )
    finally:
        connection.unregister("cohort_arrow")


def _validate_icustays(connection: duckdb.DuckDBPyConnection, expected: int) -> None:
    row = connection.execute(
        """
        SELECT COUNT(*) AS rows, COUNT(DISTINCT stay_id) AS stays,
               COUNT(*) FILTER (WHERE subject_id IS NULL OR hadm_id IS NULL
                                OR intime IS NULL) AS invalid_required_context,
               COUNT(*) FILTER (WHERE outtime IS NULL) AS unknown_outtime
        FROM mimiciv_icu.icustays
        """
    ).fetchone()
    if row[:3] != (expected, expected, 0):
        raise StagingError(
            "Every cohort stay must map once to non-null raw subject_id, hadm_id, "
            f"and intime; expected {(expected, expected, 0)}, observed {row[:3]} "
            f"(null outtime allowed and observed for {row[3]} stays)"
        )


def build_context_table(
    connection: duckdb.DuckDBPyConnection,
    cohort: CohortInfo,
    identity_hash: str,
) -> None:
    execute_table_artifact(
        connection,
        artifact_name="cohort_context",
        artifact_type="context",
        qualified_table="pipeline_meta.cohort_context",
        identity_hash=identity_hash,
        artifact_hash=canonical_json_hash(
            {"cohort": cohort.ordered_id_hash, "source": "mimiciv_icu.icustays"}
        ),
        sql="""
            CREATE TABLE pipeline_meta.cohort_context AS
            SELECT subject_id, hadm_id, stay_id, intime, outtime,
                   DATE_DIFF('microseconds', intime, outtime) / 3600000000.0 AS icu_los_hours
            FROM mimiciv_icu.icustays ORDER BY stay_id
        """,
        after_create=lambda con: _validate_icustays(con, cohort.unique_stay_ids),
    )


def build_staging(
    connection: duckdb.DuckDBPyConnection,
    *,
    mimic_root: Path,
    cohort: CohortInfo,
    identity_hash: str,
    raw_metadata: dict[str, dict[str, Any]],
    profile_directory: Path,
    specification: ScoreSpecification = SAPSII_SPEC,
) -> list[dict[str, Any]]:
    """Scan each required source once and atomically stage its retained rows."""

    build_cohort_tables(connection, cohort, identity_hash)
    profile_directory.mkdir(parents=True, exist_ok=True)
    profile_directory.chmod(0o700)
    results: list[dict[str, Any]] = []

    order = tuple(
        (relative, table)
        for relative, table in (
            ("icu/icustays.csv.gz", "mimiciv_icu.icustays"),
            ("hosp/admissions.csv.gz", "mimiciv_hosp.admissions"),
            ("hosp/patients.csv.gz", "mimiciv_hosp.patients"),
            ("hosp/services.csv.gz", "mimiciv_hosp.services"),
            ("hosp/transfers.csv.gz", "mimiciv_hosp.transfers"),
            ("hosp/diagnoses_icd.csv.gz", "mimiciv_hosp.diagnoses_icd"),
            ("hosp/procedures_icd.csv.gz", "mimiciv_hosp.procedures_icd"),
            ("icu/chartevents.csv.gz", "mimiciv_icu.chartevents"),
            ("hosp/labevents.csv.gz", "mimiciv_hosp.labevents"),
            ("icu/inputevents.csv.gz", "mimiciv_icu.inputevents"),
            ("icu/outputevents.csv.gz", "mimiciv_icu.outputevents"),
        )
        if relative in specification.required_raw_tables
    )
    rules = specification.staging_rules()
    for relative, qualified_table in order:
        source = mimic_root / relative
        metadata = raw_metadata[relative]
        rule = rules[qualified_table]
        artifact_name = f"staging:{qualified_table}"
        artifact_hash = canonical_json_hash(
            {
                "source_fingerprint": metadata["source_fingerprint"],
                "filter": rule.filter_summary,
                "item_manifest": getattr(specification, "item_manifest_version", "saps-ii-v1"),
                "item_ids": sorted(
                    specification.item_ids(qualified_table)
                    if qualified_table in {
                        "mimiciv_icu.chartevents",
                        "mimiciv_hosp.labevents",
                        "mimiciv_icu.outputevents",
                        "mimiciv_icu.inputevents",
                    }
                    else []
                ),
                "schema": RAW_SCHEMAS[relative],
            }
        )
        profile_path = profile_directory / f"{qualified_table.replace('.', '_')}.json"
        details: dict[str, Any] = {
            "source_path": metadata["path"],
            "compressed_source_size_bytes": metadata["compressed_size_bytes"],
            "source_fingerprint": metadata["source_fingerprint"],
            "filters": rule.filter_summary,
            "staged_table_size_bytes": None,
        }

        sql = (
            "PRAGMA enable_profiling = 'json'; "
            f"PRAGMA profiling_output = {_literal(str(profile_path.resolve()))}; "
            + _filter_sql(relative, qualified_table, source, specification)
        )

        def stop_profile(con: duckdb.DuckDBPyConnection) -> None:
            con.execute("PRAGMA disable_profiling")
            details["source_row_count"] = _profile_source_rows(profile_path)

        def record_statistics(
            con: duckdb.DuckDBPyConnection, retained: int, elapsed: float
        ) -> None:
            current_stat = source.stat()
            if (
                current_stat.st_size != metadata["compressed_size_bytes"]
                or current_stat.st_mtime_ns != metadata["mtime_ns"]
            ):
                raise StagingError(
                    f"Raw source metadata changed during staging scan: {source}"
                )
            source_rows = int(details["source_row_count"])
            details.update(
                {
                    "retained_row_count": retained,
                    "retention_fraction": retained / source_rows if source_rows else None,
                    "processing_time_seconds": elapsed,
                }
            )
            con.execute(
                """
                INSERT OR REPLACE INTO pipeline_meta.staging_statistics VALUES (?, ?)
                """,
                [qualified_table, json.dumps(details, sort_keys=True, default=str)],
            )

        row_count, elapsed, resumed = execute_table_artifact(
            connection,
            artifact_name=artifact_name,
            artifact_type="staging",
            qualified_table=qualified_table,
            identity_hash=identity_hash,
            artifact_hash=artifact_hash,
            sql=sql,
            details=details,
            after_create=stop_profile,
            after_count=record_statistics,
        )
        if resumed:
            stored = connection.execute(
                "SELECT statistics_json FROM pipeline_meta.staging_statistics WHERE table_name = ?",
                [qualified_table],
            ).fetchone()
            if stored is None:
                raise PipelineStateError(f"Missing staging statistics for {qualified_table}")
            details = json.loads(stored[0])
        results.append({"table": qualified_table, "rows": row_count, "elapsed": elapsed, "resumed": resumed})

        if qualified_table == "mimiciv_icu.icustays":
            build_context_table(connection, cohort, identity_hash)
        elif not table_exists(connection, "pipeline_meta.cohort_context"):
            raise PipelineStateError("Cohort context is missing after ICU-stay staging")

    return results


def staging_statistics(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT table_name, statistics_json FROM pipeline_meta.staging_statistics ORDER BY table_name"
    ).fetchall()
    return {table: json.loads(payload) for table, payload in rows}
