"""Protected projections for ICU-relative hourly SOFA."""

COMPONENTS = ("respiration", "coagulation", "liver", "cardiovascular", "cns", "renal")
TABLE = "mimiciv_derived.sofa_hourly_14d"


def scores_projection_sql() -> str:
    rolling = ",\n          ".join(
        f"s.{name}_24hours_raw, s.{name}_24hours" for name in COMPONENTS
    )
    hourly = ",\n          ".join(f"s.{name}" for name in COMPONENTS)
    return f"""
        SELECT
          s.subject_id, s.hadm_id, s.stay_id, s.hr AS hour_index,
          i.intime, i.outtime,
          DATE_DIFF('microseconds', i.intime, i.outtime) / 3600000000.0 AS icu_los_hours,
          s.starttime AS hour_start, s.endtime AS hour_end,
          s.endtime - INTERVAL '24' HOUR AS trailing_window_start,
          s.endtime AS trailing_window_end,
          s.sofa_24hours AS sofa_hourly_24h,
          {rolling},
          {hourly},
          s.pao2fio2ratio_novent, s.pao2fio2ratio_vent,
          s.rate_epinephrine, s.rate_norepinephrine,
          s.rate_dopamine, s.rate_dobutamine, s.meanbp_min, s.gcs_min,
          s.uo_24hr, s.bilirubin_max, s.creatinine_max, s.platelet_min,
          'sofa-hourly-14d-v2' AS adaptation_version
        FROM {TABLE} s
        JOIN mimiciv_icu.icustays i USING (stay_id)
        ORDER BY s.stay_id, s.hr
    """


def missingness_projection_sql() -> str:
    indicators = ",\n            ".join(
        f"{name}_24hours_raw IS NULL AS {name}_score_missing" for name in COMPONENTS
    )
    count = " + ".join(
        f"CAST({name}_24hours_raw IS NULL AS INTEGER)" for name in COMPONENTS
    )
    return f"""
        SELECT stay_id, hr AS hour_index, starttime AS hour_start, endtime AS hour_end,
            {indicators},
            {count} AS number_of_missing_components,
            ({count}) = 0 AS complete_components
        FROM {TABLE}
        ORDER BY stay_id, hr
    """
