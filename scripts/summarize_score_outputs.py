#!/usr/bin/env python3
"""Print aggregate, identifier-free summaries for completed score outputs."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb


SCORES = {
    "saps_ii": {
        "total": "sapsii_official",
        "probabilities": ("sapsii_prob_official",),
        "proxy_columns": (),
    },
    "saps_iii_adapted": {
        "total": "saps_iii_adapted",
        "probabilities": (
            "saps_iii_prob_global_adapted",
            "saps_iii_prob_north_america_adapted",
        ),
        "proxy_columns": (
            "mechanical_ventilation_proxy",
            "vasoactive_preicu_proxy",
            "planned_icu_proxy",
            "diagnoses_are_posthoc_proxies",
            "nyha_iv_available",
            "cancer_therapy_available",
            "pre_sedation_gcs_available",
        ),
    },
    "sofa_first_day_adapted": {
        "total": "sofa_first_day_adapted",
        "probabilities": (),
        "proxy_columns": ("ventilated_pf_correction_applied",),
    },
}


def _metric(observed: int, total: int) -> dict[str, int | float | None]:
    return {
        "count": observed,
        "percentage": round(100.0 * observed / total, 6) if total else None,
    }


def _distribution(connection: duckdb.DuckDBPyConnection, column: str) -> dict[str, Any]:
    row = connection.execute(
        f"""
        SELECT COUNT({column}), MIN({column}), quantile_cont({column}, 0.25),
               MEDIAN({column}), AVG({column}), quantile_cont({column}, 0.75),
               MAX({column})
        FROM scores
        """
    ).fetchone()
    names = ("observed_count", "minimum", "p25", "median", "mean", "p75", "maximum")
    return dict(zip(names, row, strict=True))


def _run_duration_seconds(manifest: dict[str, Any]) -> float | None:
    try:
        start = datetime.fromisoformat(manifest["start_timestamp_utc"])
        end = datetime.fromisoformat(manifest["completion_timestamp_utc"])
    except (KeyError, TypeError, ValueError):
        return None
    return (end - start).total_seconds()


def summarize_score(output_directory: Path, score_name: str) -> dict[str, Any]:
    config = SCORES[score_name]
    paths = {
        "scores": output_directory / "scores.parquet",
        "missingness": output_directory / "score_missingness.parquet",
        "manifest": output_directory / "run_manifest.json",
    }
    missing_files = [str(path) for path in paths.values() if not path.is_file()]
    if missing_files:
        raise FileNotFoundError("Missing completed outputs: " + ", ".join(missing_files))

    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    connection = duckdb.connect()
    try:
        connection.read_parquet(str(paths["scores"])).create_view("scores")
        connection.read_parquet(str(paths["missingness"])).create_view("missingness")
        score_columns = {row[0] for row in connection.execute("DESCRIBE scores").fetchall()}
        missingness_columns = {
            row[0] for row in connection.execute("DESCRIBE missingness").fetchall()
        }
        component_flags = sorted(
            column for column in missingness_columns if column.endswith("_missing")
        )
        required_score_columns = {
            "stay_id", "stay_shorter_than_24h", config["total"],
            *config["probabilities"], *config["proxy_columns"],
        }
        absent = sorted(required_score_columns - score_columns)
        if absent:
            raise ValueError(f"{score_name} scores.parquet lacks columns: {absent}")
        if not component_flags:
            raise ValueError(f"{score_name} has no component missingness columns")

        rows, unique_stays, null_stays = map(
            int,
            connection.execute(
                "SELECT COUNT(*), COUNT(DISTINCT stay_id), "
                "COUNT(*) FILTER (WHERE stay_id IS NULL) FROM scores"
            ).fetchone(),
        )
        missing_rows = int(connection.execute("SELECT COUNT(*) FROM missingness").fetchone()[0])
        membership_difference = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM (
                  (SELECT stay_id FROM scores EXCEPT SELECT stay_id FROM missingness)
                  UNION ALL
                  (SELECT stay_id FROM missingness EXCEPT SELECT stay_id FROM scores)
                ) differences
                """
            ).fetchone()[0]
        )

        component_missingness: dict[str, Any] = {}
        for flag in component_flags:
            missing = int(
                connection.execute(
                    f"SELECT COUNT(*) FILTER (WHERE {flag}) FROM missingness"
                ).fetchone()[0]
            )
            component_missingness[flag.removesuffix("_missing")] = {
                "observed": _metric(rows - missing, rows),
                "missing": _metric(missing, rows),
            }

        missing_count_distribution = [
            {
                "number_of_missing_components": int(number),
                **_metric(int(count), rows),
            }
            for number, count in connection.execute(
                """
                SELECT number_of_missing_components, COUNT(*)
                FROM missingness
                GROUP BY number_of_missing_components
                ORDER BY number_of_missing_components
                """
            ).fetchall()
        ]

        strata: dict[str, Any] = {}
        for name, predicate in {
            "shorter_than_24h": "s.stay_shorter_than_24h IS TRUE",
            "at_least_24h": "s.stay_shorter_than_24h IS FALSE",
            "unknown_length": "s.stay_shorter_than_24h IS NULL",
        }.items():
            total, complete, mean_missing, median_missing = connection.execute(
                f"""
                SELECT COUNT(*), COUNT(*) FILTER (WHERE m.complete_components),
                       AVG(m.number_of_missing_components),
                       MEDIAN(m.number_of_missing_components)
                FROM scores s JOIN missingness m USING (stay_id)
                WHERE {predicate}
                """
            ).fetchone()
            strata[name] = {
                "rows": int(total),
                "complete_components": _metric(int(complete), int(total)),
                "mean_missing_components": mean_missing,
                "median_missing_components": median_missing,
            }

        proxy_prevalence: dict[str, Any] = {}
        for column in config["proxy_columns"]:
            true_count, false_count, null_count = map(
                int,
                connection.execute(
                    f"""
                    SELECT COUNT(*) FILTER (WHERE {column} IS TRUE),
                           COUNT(*) FILTER (WHERE {column} IS FALSE),
                           COUNT(*) FILTER (WHERE {column} IS NULL)
                    FROM scores
                    """
                ).fetchone(),
            )
            proxy_prevalence[column] = {
                "true": _metric(true_count, rows),
                "false": _metric(false_count, rows),
                "null": _metric(null_count, rows),
            }

        staging = {
            table: {
                "source_row_count": details.get("source_row_count"),
                "retained_row_count": details.get("retained_row_count"),
                "retention_fraction": details.get("retention_fraction"),
                "processing_time_seconds": details.get("processing_time_seconds"),
                "filters": details.get("filters"),
            }
            for table, details in sorted(manifest.get("staging_statistics", {}).items())
        }
        runtime = manifest.get("runtime_configuration", {})
        return {
            "score_name": score_name,
            "row_validation": {
                "rows": rows,
                "unique_stays": unique_stays,
                "null_stay_ids": null_stays,
                "missingness_rows": missing_rows,
                "score_missingness_membership_difference": membership_difference,
            },
            "provenance": {
                "run_identity_hash": manifest.get("run_identity_hash"),
                "run_mode": manifest.get("run_mode"),
                "mimic_version": manifest.get("mimic_version"),
                "score_provenance": manifest.get("score_provenance"),
                "official_mimic_code_release": manifest.get("official_mimic_code_release"),
                "official_mimic_code_commit": manifest.get("official_mimic_code_commit"),
                "item_id_manifest_version": manifest.get("item_id_manifest_version"),
                "concept_dependency_order": manifest.get("concept_dependency_order"),
            },
            "deployment": {
                "start_timestamp_utc": manifest.get("start_timestamp_utc"),
                "completion_timestamp_utc": manifest.get("completion_timestamp_utc"),
                "manifest_elapsed_seconds": _run_duration_seconds(manifest),
                "threads": runtime.get("threads"),
                "memory_limit": runtime.get("memory_limit"),
                "slurm_metadata": manifest.get("slurm_metadata", {}),
                "staging": staging,
            },
            "score_distribution": _distribution(connection, config["total"]),
            "probability_distributions": {
                column: _distribution(connection, column)
                for column in config["probabilities"]
            },
            "component_missingness": component_missingness,
            "missing_component_count_distribution": missing_count_distribution,
            "missingness_by_icu_duration": strata,
            "proxy_and_availability_prevalence": proxy_prevalence,
        }
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Summarize completed clinical-score outputs without identifiers"
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--mode", choices=("dev100", "full"), default="full")
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    summaries = {
        name: summarize_score(project_root / "outputs" / args.mode / name, name)
        for name in SCORES
    }
    manifests = {
        name: json.loads(
            (project_root / "outputs" / args.mode / name / "run_manifest.json").read_text()
        )
        for name in SCORES
    }
    row_counts = {summary["row_validation"]["rows"] for summary in summaries.values()}
    cohort_hashes = {manifest.get("cohort_ordered_id_hash") for manifest in manifests.values()}
    result = {
        "privacy": "Aggregate summary only; no stay_id values are emitted.",
        "mode": args.mode,
        "cross_score_validation": {
            "same_row_count": len(row_counts) == 1,
            "same_cohort_ordered_id_hash": len(cohort_hashes) == 1,
        },
        "scores": summaries,
    }
    print(json.dumps(result, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
