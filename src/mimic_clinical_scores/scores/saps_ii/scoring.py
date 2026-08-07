"""SAPS II concept execution declarations and output projection."""

from __future__ import annotations

from mimic_clinical_scores.scores.saps_ii.specification import COMPONENT_COLUMNS


OFFICIAL_SCORE_TABLE = "mimiciv_derived.sapsii"


def scores_projection_sql() -> str:
    components = ",\n       ".join(f"s.{column}" for column in COMPONENT_COLUMNS)
    return f"""
        SELECT
            s.stay_id,
            s.subject_id,
            s.hadm_id,
            i.intime,
            i.outtime,
            DATE_DIFF('microseconds', i.intime, i.outtime) / 3600000000.0 AS icu_los_hours,
            CASE
              WHEN i.outtime IS NULL OR i.intime IS NULL THEN NULL
              ELSE LEAST(24.0, GREATEST(0.0,
                   DATE_DIFF('microseconds', i.intime, i.outtime) / 3600000000.0))
            END AS available_first_day_hours,
            CASE
              WHEN i.outtime IS NULL OR i.intime IS NULL THEN NULL
              ELSE DATE_DIFF('microseconds', i.intime, i.outtime) / 3600000000.0 < 24.0
            END AS stay_shorter_than_24h,
            s.sapsii AS sapsii_official,
            s.sapsii_prob AS sapsii_prob_official,
            s.starttime AS score_window_start,
            s.endtime AS score_window_end,
            {components}
        FROM mimiciv_derived.sapsii s
        INNER JOIN mimiciv_icu.icustays i USING (stay_id)
        ORDER BY s.stay_id
    """


def missingness_projection_sql() -> str:
    indicators = ",\n            ".join(
        f"{column} IS NULL AS {column}_missing"
        for column in COMPONENT_COLUMNS
    )
    missing_count = " + ".join(
        f"CAST({column} IS NULL AS INTEGER)" for column in COMPONENT_COLUMNS
    )
    return f"""
        SELECT stay_id,
            {indicators},
            {missing_count} AS number_of_missing_components,
            ({missing_count}) = 0 AS complete_components
        FROM mimiciv_derived.sapsii
        ORDER BY stay_id
    """
