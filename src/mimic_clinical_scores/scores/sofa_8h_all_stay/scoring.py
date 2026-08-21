"""Protected projections for complete-stay eight-hour SOFA."""

COMPONENTS = ("respiration", "coagulation", "liver", "cardiovascular", "cns", "renal")
TABLE = "mimiciv_derived.sofa_8h_all_stay"


def scores_projection_sql() -> str:
    rolling = ",\n          ".join(
        f"s.{name}_24hours_raw, s.{name}_24hours" for name in COMPONENTS
    )
    block = ",\n          ".join(f"s.{name}" for name in COMPONENTS)
    return f"""
        SELECT
          s.subject_id, s.hadm_id, s.stay_id, s.hr AS block_index,
          i.intime, i.outtime,
          DATE_DIFF('microseconds', i.intime, i.outtime) / 3600000000.0
            AS icu_los_hours,
          s.starttime AS block_start, s.endtime AS block_end,
          DATE_DIFF('microseconds', s.starttime, s.endtime) / 3600000000.0
            AS block_duration_hours,
          s.endtime - INTERVAL '24' HOUR AS trailing_window_start,
          s.endtime AS trailing_window_end,
          s.sofa_24hours AS sofa_trailing_24h,
          {rolling},
          {block},
          s.pao2fio2ratio_novent, s.pao2fio2ratio_novent_charttime,
          s.pao2_novent, s.fio2_novent, s.fio2_source_novent,
          s.pao2fio2ratio_vent, s.pao2fio2ratio_vent_charttime,
          s.pao2_vent, s.fio2_vent, s.fio2_source_vent,
          s.rate_epinephrine, s.rate_norepinephrine,
          s.rate_dopamine, s.rate_dobutamine, s.meanbp_min,
          s.gcs_min, s.gcs_charttime, s.gcs_motor, s.gcs_verbal,
          s.gcs_eyes, s.gcs_unable, s.gcs_components_measured,
          s.uo_24hr, s.uo_24hr_charttime, s.urineoutput_24hr, s.uo_tm_24hr,
          s.bilirubin_max, s.creatinine_max, s.platelet_min,
          'sofa-8h-all-stay-v1' AS adaptation_version
        FROM {TABLE} s
        JOIN mimiciv_icu.icustays i USING (stay_id)
        ORDER BY s.stay_id, s.hr
    """


def missingness_projection_sql() -> str:
    indicators = ",\n            ".join(
        f"{name}_24hours_raw IS NULL AS {name}_score_missing"
        for name in COMPONENTS
    )
    count = " + ".join(
        f"CAST({name}_24hours_raw IS NULL AS INTEGER)" for name in COMPONENTS
    )
    return f"""
        SELECT stay_id, hr AS block_index,
            starttime AS block_start, endtime AS block_end,
            {indicators},
            {count} AS number_of_missing_components,
            ({count}) = 0 AS complete_components
        FROM {TABLE}
        ORDER BY stay_id, hr
    """
