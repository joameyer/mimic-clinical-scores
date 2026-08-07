#!/usr/bin/env python3
"""Aggregate, identifier-free audit of adapted SOFA respiratory missingness."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

import duckdb


AUDIT_CTE = """
WITH gas_events AS (
  SELECT
    ie.stay_id,
    bg.charttime,
    bg.po2,
    bg.fio2,
    bg.fio2_chartevents,
    bg.pao2fio2ratio,
    EXISTS (
      SELECT 1
      FROM mimiciv_derived.ventilation vd
      WHERE vd.stay_id = ie.stay_id
        AND vd.ventilation_status = 'InvasiveVent'
        AND bg.charttime >= vd.starttime
        AND bg.charttime <= vd.endtime
    ) AS invasive_ventilation_at_gas
  FROM mimiciv_icu.icustays ie
  JOIN mimiciv_derived.bg bg
    ON bg.subject_id = ie.subject_id
   AND bg.charttime >= ie.intime - INTERVAL '6' HOUR
   AND bg.charttime <= ie.intime + INTERVAL '24' HOUR
), gas_by_stay AS (
  SELECT
    stay_id,
    COUNT(*) AS pao2_rows,
    COUNT(pao2fio2ratio) AS valid_pf_rows,
    COUNT(*) FILTER (WHERE fio2 IS NOT NULL AND pao2fio2ratio IS NOT NULL)
      AS valid_pf_rows_using_lab_fio2,
    COUNT(*) FILTER (
      WHERE fio2 IS NULL AND fio2_chartevents IS NOT NULL
        AND pao2fio2ratio IS NOT NULL
    ) AS valid_pf_rows_using_chart_fio2,
    BOOL_OR(invasive_ventilation_at_gas) AS invasive_ventilation_at_any_gas
  FROM gas_events
  GROUP BY stay_id
), audited AS (
  SELECT
    ie.stay_id,
    CASE
      WHEN ie.outtime IS NULL THEN 'unknown_length'
      WHEN DATE_DIFF('microseconds', ie.intime, ie.outtime) / 3600000000.0 < 24
        THEN 'shorter_than_24h'
      ELSE 'at_least_24h'
    END AS duration_stratum,
    EXISTS (
      SELECT 1
      FROM mimiciv_derived.ventilation vd
      WHERE vd.stay_id = ie.stay_id
        AND vd.ventilation_status = 'InvasiveVent'
        AND vd.starttime <= ie.intime + INTERVAL '24' HOUR
        AND vd.endtime >= ie.intime - INTERVAL '6' HOUR
    ) AS invasive_ventilation_in_window,
    s.respiration_score,
    COALESCE(g.pao2_rows, 0) AS pao2_rows,
    COALESCE(g.valid_pf_rows, 0) AS valid_pf_rows,
    COALESCE(g.valid_pf_rows_using_lab_fio2, 0) AS valid_pf_rows_using_lab_fio2,
    COALESCE(g.valid_pf_rows_using_chart_fio2, 0) AS valid_pf_rows_using_chart_fio2,
    COALESCE(g.invasive_ventilation_at_any_gas, FALSE)
      AS invasive_ventilation_at_any_gas,
    CASE
      WHEN s.respiration_score IS NOT NULL THEN 'component_observed'
      WHEN COALESCE(g.pao2_rows, 0) = 0 THEN 'missing_no_pao2_in_window'
      WHEN COALESCE(g.valid_pf_rows, 0) = 0 THEN 'missing_pao2_without_valid_fio2'
      ELSE 'missing_despite_valid_pf_internal_inconsistency'
    END AS availability_class
  FROM mimiciv_icu.icustays ie
  JOIN mimiciv_derived.sofa_first_day_adapted s USING (stay_id)
  LEFT JOIN gas_by_stay g USING (stay_id)
)
"""


def _metric(count: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "count": count,
        "percentage": round(100.0 * count / denominator, 6) if denominator else None,
    }


def _group_rows(connection: duckdb.DuckDBPyConnection) -> list[tuple[Any, ...]]:
    return connection.execute(
        AUDIT_CTE
        + """
        SELECT 'overall' AS section, 'all' AS group_name,
          COUNT(*) AS cohort_rows,
          COUNT(*) FILTER (WHERE respiration_score IS NOT NULL) AS observed,
          COUNT(*) FILTER (WHERE respiration_score IS NULL) AS missing,
          COUNT(*) FILTER (WHERE availability_class = 'missing_no_pao2_in_window') AS no_pao2,
          COUNT(*) FILTER (WHERE availability_class = 'missing_pao2_without_valid_fio2') AS no_fio2,
          COUNT(*) FILTER (
            WHERE availability_class = 'missing_despite_valid_pf_internal_inconsistency'
          ) AS inconsistent_missing,
          COUNT(*) FILTER (
            WHERE respiration_score IS NOT NULL AND valid_pf_rows = 0
          ) AS inconsistent_observed,
          COUNT(*) FILTER (WHERE valid_pf_rows_using_lab_fio2 > 0) AS stays_with_lab_fio2,
          COUNT(*) FILTER (WHERE valid_pf_rows_using_chart_fio2 > 0) AS stays_with_chart_fio2
        FROM audited
        UNION ALL
        SELECT 'icu_duration', duration_stratum,
          COUNT(*),
          COUNT(*) FILTER (WHERE respiration_score IS NOT NULL),
          COUNT(*) FILTER (WHERE respiration_score IS NULL),
          COUNT(*) FILTER (WHERE availability_class = 'missing_no_pao2_in_window'),
          COUNT(*) FILTER (WHERE availability_class = 'missing_pao2_without_valid_fio2'),
          COUNT(*) FILTER (
            WHERE availability_class = 'missing_despite_valid_pf_internal_inconsistency'
          ),
          COUNT(*) FILTER (WHERE respiration_score IS NOT NULL AND valid_pf_rows = 0),
          COUNT(*) FILTER (WHERE valid_pf_rows_using_lab_fio2 > 0),
          COUNT(*) FILTER (WHERE valid_pf_rows_using_chart_fio2 > 0)
        FROM audited GROUP BY duration_stratum
        UNION ALL
        SELECT 'invasive_ventilation_in_window',
          CASE WHEN invasive_ventilation_in_window THEN 'yes' ELSE 'no' END,
          COUNT(*),
          COUNT(*) FILTER (WHERE respiration_score IS NOT NULL),
          COUNT(*) FILTER (WHERE respiration_score IS NULL),
          COUNT(*) FILTER (WHERE availability_class = 'missing_no_pao2_in_window'),
          COUNT(*) FILTER (WHERE availability_class = 'missing_pao2_without_valid_fio2'),
          COUNT(*) FILTER (
            WHERE availability_class = 'missing_despite_valid_pf_internal_inconsistency'
          ),
          COUNT(*) FILTER (WHERE respiration_score IS NOT NULL AND valid_pf_rows = 0),
          COUNT(*) FILTER (WHERE valid_pf_rows_using_lab_fio2 > 0),
          COUNT(*) FILTER (WHERE valid_pf_rows_using_chart_fio2 > 0)
        FROM audited GROUP BY invasive_ventilation_in_window
        ORDER BY section, group_name
        """
    ).fetchall()


def _fio2_fallback_age(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    rows = connection.execute(
        """
        WITH chart_fallback_gases AS (
          SELECT ie.stay_id, bg.subject_id, bg.charttime AS gas_time
          FROM mimiciv_icu.icustays ie
          JOIN mimiciv_derived.bg bg
            ON bg.subject_id = ie.subject_id
           AND bg.charttime >= ie.intime - INTERVAL '6' HOUR
           AND bg.charttime <= ie.intime + INTERVAL '24' HOUR
          WHERE bg.po2 IS NOT NULL
            AND bg.fio2 IS NULL
            AND bg.fio2_chartevents IS NOT NULL
        ), matched AS (
          SELECT g.stay_id, g.gas_time, MAX(ce.charttime) AS fio2_time
          FROM chart_fallback_gases g
          JOIN mimiciv_icu.chartevents ce
            ON ce.subject_id = g.subject_id
           AND ce.itemid = 223835
           AND ce.charttime >= g.gas_time - INTERVAL '4' HOUR
           AND ce.charttime <= g.gas_time
           AND ce.valuenum > 0 AND ce.valuenum <= 100
          GROUP BY g.stay_id, g.gas_time
        ), ages AS (
          SELECT DATE_DIFF('seconds', fio2_time, gas_time) / 60.0 AS age_minutes
          FROM matched
        )
        SELECT
          CASE
            WHEN age_minutes <= 15 THEN '00_to_15_minutes'
            WHEN age_minutes <= 60 THEN '15_to_60_minutes'
            WHEN age_minutes <= 120 THEN '01_to_02_hours'
            WHEN age_minutes <= 240 THEN '02_to_04_hours'
            ELSE 'outside_expected_window'
          END AS age_group,
          COUNT(*) AS gas_pairs,
          MIN(age_minutes) AS minimum_minutes,
          MAX(age_minutes) AS maximum_minutes
        FROM ages
        GROUP BY age_group
        ORDER BY MIN(age_minutes)
        """
    ).fetchall()
    total = sum(int(row[1]) for row in rows)
    return {
        "definition": (
            "Most recent valid charted FiO2 preceding a PaO2 when same-specimen "
            "laboratory FiO2 is unavailable; permitted range is 0 through 240 minutes."
        ),
        "matched_stay_gas_pairs": total,
        "age_distribution": [
            {
                "age_group": group,
                "gas_pairs": _metric(int(count), total),
                "minimum_minutes": minimum,
                "maximum_minutes": maximum,
            }
            for group, count, minimum, maximum in rows
        ],
    }


def audit(
    database: Path,
    *,
    threads: int = 4,
    memory_limit: str = "18GB",
    spill_directory: Path | None = None,
) -> dict[str, Any]:
    if not database.is_file():
        raise FileNotFoundError(f"SOFA DuckDB database does not exist: {database}")
    connection = duckdb.connect(str(database), read_only=True)
    try:
        if threads < 1:
            raise ValueError("threads must be positive")
        if not re.fullmatch(r"[1-9][0-9]*(?:MB|GB|TB)", memory_limit.upper()):
            raise ValueError("memory_limit must look like 18GB or 1024MB")
        connection.execute(f"SET threads = {threads}")
        connection.execute(f"SET memory_limit = '{memory_limit.upper()}'")
        if spill_directory is not None:
            spill_directory.mkdir(parents=True, exist_ok=True)
            spill_directory.chmod(0o700)
            escaped = str(spill_directory.resolve()).replace("'", "''")
            connection.execute(f"SET temp_directory = '{escaped}'")
        required = {
            "mimiciv_icu.icustays",
            "mimiciv_icu.chartevents",
            "mimiciv_derived.bg",
            "mimiciv_derived.ventilation",
            "mimiciv_derived.sofa_first_day_adapted",
        }
        available = {
            f"{schema}.{table}"
            for schema, table in connection.execute(
                "SELECT table_schema, table_name FROM information_schema.tables"
            ).fetchall()
        }
        missing = sorted(required - available)
        if missing:
            raise RuntimeError("Database lacks required completed tables: " + ", ".join(missing))

        sections: dict[str, dict[str, Any]] = {}
        for row in _group_rows(connection):
            (
                section, group_name, total, observed, missing_count, no_pao2, no_fio2,
                inconsistent_missing, inconsistent_observed, lab_fio2, chart_fio2,
            ) = row
            total = int(total)
            missing_count = int(missing_count)
            sections.setdefault(str(section), {})[str(group_name)] = {
                "cohort_rows": total,
                "respiration_observed": _metric(int(observed), total),
                "respiration_missing": _metric(missing_count, total),
                "missing_cause_among_missing": {
                    "no_pao2_in_window": _metric(int(no_pao2), missing_count),
                    "pao2_present_without_valid_fio2": _metric(int(no_fio2), missing_count),
                    "valid_pf_but_component_missing_internal_inconsistency": _metric(
                        int(inconsistent_missing), missing_count
                    ),
                },
                "component_observed_without_valid_pf_internal_inconsistency": int(
                    inconsistent_observed
                ),
                "stays_with_valid_pf_using_same_specimen_lab_fio2": int(lab_fio2),
                "stays_with_valid_pf_using_chart_fio2_fallback": int(chart_fio2),
            }
        return {
            "privacy": "Aggregate audit only; no subject_id, hadm_id, or stay_id values are emitted.",
            "database": str(database.resolve()),
            "runtime": {
                "threads": threads,
                "memory_limit": memory_limit.upper(),
                "spill_directory": (
                    str(spill_directory.resolve()) if spill_directory is not None else None
                ),
            },
            "score_window": "PaO2 charttime inclusive [ICU intime - 6h, ICU intime + 24h]",
            "fio2_pairing": (
                "Same-specimen lab FiO2 preferred; otherwise latest valid charted FiO2 "
                "from 0 through 4 hours before PaO2."
            ),
            "sections": sections,
            "charted_fio2_fallback_age": _fio2_fallback_age(connection),
        }
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit SOFA respiratory missingness without printing identifiers"
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("work/full/sofa_first_day_adapted.duckdb"),
    )
    parser.add_argument(
        "--threads", type=int, default=int(os.environ.get("DUCKDB_THREADS", "4"))
    )
    parser.add_argument(
        "--memory-limit", default=os.environ.get("DUCKDB_MEMORY_LIMIT", "18GB")
    )
    parser.add_argument("--spill-directory", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            audit(
                args.database.resolve(),
                threads=args.threads,
                memory_limit=args.memory_limit,
                spill_directory=(
                    args.spill_directory.resolve() if args.spill_directory is not None else None
                ),
            ),
            indent=2,
            sort_keys=True,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
