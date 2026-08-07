"""Protected output projections for the adapted classic first-day SOFA."""

from mimic_clinical_scores.scores.sofa_first_day_adapted.specification import COMPONENT_COLUMNS


TABLE = "mimiciv_derived.sofa_first_day_adapted"


def scores_projection_sql() -> str:
    return f"""
        SELECT
          s.stay_id, s.subject_id, s.hadm_id, i.intime, i.outtime,
          DATE_DIFF('microseconds', i.intime, i.outtime) / 3600000000.0 AS icu_los_hours,
          CASE WHEN i.outtime IS NULL THEN NULL ELSE LEAST(24.0, GREATEST(0.0,
            DATE_DIFF('microseconds', i.intime, i.outtime) / 3600000000.0)) END AS available_first_day_hours,
          CASE WHEN i.outtime IS NULL THEN NULL ELSE
            DATE_DIFF('microseconds', i.intime, i.outtime) / 3600000000.0 < 24.0 END AS stay_shorter_than_24h,
          s.sofa_first_day_adapted,
          i.intime - INTERVAL '6' HOUR AS score_window_start,
          i.intime + INTERVAL '24' HOUR AS score_window_end,
          s.respiration_score, s.coagulation_score, s.liver_score,
          s.cardiovascular_score, s.cns_score, s.renal_score,
          s.pao2fio2_novent_min, s.pao2fio2_vent_min, s.platelet_min,
          s.bilirubin_max, s.mbp_min, s.rate_norepinephrine,
          s.rate_epinephrine, s.rate_dopamine, s.rate_dobutamine,
          s.gcs_min, s.creatinine_max, s.urineoutput,
          s.adaptation_version, s.ventilated_pf_correction_applied
        FROM {TABLE} s
        JOIN mimiciv_icu.icustays i USING (stay_id)
        ORDER BY s.stay_id
    """


def missingness_projection_sql() -> str:
    indicators = ",\n            ".join(
        f"{column} IS NULL AS {column}_missing" for column in COMPONENT_COLUMNS
    )
    count = " + ".join(f"CAST({column} IS NULL AS INTEGER)" for column in COMPONENT_COLUMNS)
    return f"""
        SELECT stay_id,
            {indicators},
            {count} AS number_of_missing_components,
            ({count}) = 0 AS complete_components
        FROM {TABLE}
        ORDER BY stay_id
    """
