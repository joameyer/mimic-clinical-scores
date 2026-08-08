"""Protected projections for discharge-relative hourly SOFA."""

COMPONENTS = ("respiration", "coagulation", "liver", "cardiovascular", "cns", "renal")
TABLE = "mimiciv_derived.sofa_hourly_reverse_7d"


def scores_projection_sql() -> str:
    rolling = ",\n          ".join(
        f"s.{name}_24hours_raw, s.{name}_24hours" for name in COMPONENTS
    )
    hourly = ",\n          ".join(f"s.{name}" for name in COMPONENTS)
    return f"""
        SELECT
          s.subject_id, s.hadm_id, s.stay_id,
          s.hours_before_discharge,
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
          s.deathtime, s.hospital_expire_flag,
          (s.deathtime IS NOT NULL AND s.deathtime <= i.outtime)
            AS death_recorded_by_icu_discharge,
          (s.deathtime IS NOT NULL AND s.deathtime >= i.intime AND s.deathtime <= i.outtime)
            AS died_during_icu_stay,
          NOT (s.deathtime IS NOT NULL AND s.deathtime <= i.outtime)
            AS no_death_recorded_by_icu_discharge,
          'sofa-hourly-reverse-7d-v2' AS adaptation_version
        FROM {TABLE} s
        JOIN mimiciv_icu.icustays i USING (stay_id)
        ORDER BY s.stay_id, s.hours_before_discharge
    """


def missingness_projection_sql() -> str:
    indicators = ",\n            ".join(
        f"{name}_24hours_raw IS NULL AS {name}_score_missing" for name in COMPONENTS
    )
    count = " + ".join(
        f"CAST({name}_24hours_raw IS NULL AS INTEGER)" for name in COMPONENTS
    )
    return f"""
        SELECT stay_id, hours_before_discharge,
            starttime AS hour_start, endtime AS hour_end,
            {indicators},
            {count} AS number_of_missing_components,
            ({count}) = 0 AS complete_components
        FROM {TABLE}
        ORDER BY stay_id, hours_before_discharge
    """
