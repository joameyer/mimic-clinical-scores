-- Adaptation of MIT-LCP mimic-code v3.0.1 concepts_duckdb/score/sofa.sql.
-- Upstream SHA-256: 5af9c75bdaeb9342138a0fbc8cbef33b132508689e3ac492ab574af1c7ff05b0
-- Adaptations: replace charttime/wall-clock icustay_hourly with non-overlapping
-- ICU-intime-relative 8-hour blocks spanning the complete recorded ICU stay; retain
-- 24-hour pre-ICU context; preserve exact elapsed trailing 24-hour windows, including
-- at a partial discharge block; retain nullable rolling components and auditable
-- composite inputs; and require positive, at-least-one-hour vasoactive episodes.
-- Component thresholds are unchanged.
DROP TABLE IF EXISTS mimiciv_derived.sofa_8h_all_stay;
CREATE TABLE mimiciv_derived.sofa_8h_all_stay AS
WITH grid_bounds AS (
  SELECT
    subject_id,
    hadm_id,
    stay_id,
    intime,
    outtime,
    TRY_CAST(
      CEIL(DATE_DIFF('microseconds', intime, outtime) / 28800000000.0)
      AS INTEGER
    ) - 1 AS last_output_block,
    outtime AS last_output_end
  FROM mimiciv_icu.icustays
  WHERE outtime IS NOT NULL AND outtime > intime
), grid AS (
  SELECT
    gb.subject_id,
    gb.hadm_id,
    gb.stay_id,
    gb.intime,
    gb.outtime,
    TRY_CAST(block_value AS INTEGER) AS hr
  FROM grid_bounds gb
  CROSS JOIN UNNEST(GENERATE_SERIES(-3, gb.last_output_block)) AS blocks(block_value)
), regular_co AS (
  SELECT
    subject_id,
    hadm_id,
    stay_id,
    hr,
    intime + hr * INTERVAL '8' HOUR AS starttime,
    CASE
      WHEN hr < 0 THEN intime + (hr + 1) * INTERVAL '8' HOUR
      ELSE LEAST(intime + (hr + 1) * INTERVAL '8' HOUR, outtime)
    END AS endtime,
    FALSE AS is_boundary,
    NULL::INTEGER AS boundary_target_hr
  FROM grid
), boundary_co AS (
  -- A partial final block shifts the true 24-hour start away from the
  -- intime-aligned internal grid. This extra interval is exactly the omitted
  -- leading segment; its component maxima are merged only into that final block.
  SELECT
    subject_id,
    hadm_id,
    stay_id,
    -1000000 AS hr,
    last_output_end - INTERVAL '24' HOUR AS starttime,
    intime + (last_output_block - 2) * INTERVAL '8' HOUR AS endtime,
    TRUE AS is_boundary,
    last_output_block AS boundary_target_hr
  FROM grid_bounds
  WHERE last_output_end - INTERVAL '24' HOUR
        < intime + (last_output_block - 2) * INTERVAL '8' HOUR
), co AS (
  SELECT * FROM regular_co
  UNION ALL
  SELECT * FROM boundary_co
), pafi AS (
  SELECT
    ie.stay_id,
    bg.charttime,
    bg.po2 AS pao2,
    COALESCE(bg.fio2, bg.fio2_chartevents) AS fio2,
    CASE
      WHEN bg.fio2 IS NOT NULL THEN 'labevents'
      WHEN bg.fio2_chartevents IS NOT NULL THEN 'chartevents'
    END AS fio2_source,
    CASE
      WHEN vd.stay_id IS NULL THEN bg.pao2fio2ratio
    END AS pao2fio2ratio_novent,
    CASE
      WHEN vd.stay_id IS NOT NULL THEN bg.pao2fio2ratio
    END AS pao2fio2ratio_vent
  FROM mimiciv_icu.icustays AS ie
  INNER JOIN mimiciv_derived.bg AS bg
    ON ie.subject_id = bg.subject_id
  LEFT JOIN mimiciv_derived.ventilation AS vd
    ON ie.stay_id = vd.stay_id
    AND bg.charttime >= vd.starttime
    AND bg.charttime <= vd.endtime
    AND vd.ventilation_status = 'InvasiveVent'
  WHERE specimen = 'ART.'
), vs AS (
  SELECT co.stay_id, co.hr, MIN(vs.mbp) AS meanbp_min
  FROM co
  LEFT JOIN mimiciv_derived.vitalsign AS vs
    ON co.stay_id = vs.stay_id
    AND co.starttime < vs.charttime
    AND co.endtime >= vs.charttime
  GROUP BY co.stay_id, co.hr
), gcs AS (
  SELECT
    co.stay_id,
    co.hr,
    MIN(gcs.gcs) AS gcs_min,
    ARG_MIN(
      STRUCT_PACK(
        charttime := gcs.charttime,
        motor := gcs.gcs_motor,
        verbal := gcs.gcs_verbal,
        eyes := gcs.gcs_eyes,
        unable := gcs.gcs_unable,
        components_measured :=
          CAST(gcs.gcs_motor IS NOT NULL AS INTEGER)
          + CAST(gcs.gcs_verbal IS NOT NULL AS INTEGER)
          + CAST(gcs.gcs_eyes IS NOT NULL AS INTEGER)
      ),
      STRUCT_PACK(gcs := gcs.gcs, charttime := gcs.charttime)
    ) FILTER (WHERE gcs.gcs IS NOT NULL) AS gcs_min_observation
  FROM co
  LEFT JOIN mimiciv_derived.gcs AS gcs
    ON co.stay_id = gcs.stay_id
    AND co.starttime < gcs.charttime
    AND co.endtime >= gcs.charttime
  GROUP BY co.stay_id, co.hr
), bili AS (
  SELECT co.stay_id, co.hr, MAX(enz.bilirubin_total) AS bilirubin_max
  FROM co
  LEFT JOIN mimiciv_derived.enzyme AS enz
    ON co.hadm_id = enz.hadm_id
    AND co.starttime < enz.charttime
    AND co.endtime >= enz.charttime
  GROUP BY co.stay_id, co.hr
), cr AS (
  SELECT co.stay_id, co.hr, MAX(chem.creatinine) AS creatinine_max
  FROM co
  LEFT JOIN mimiciv_derived.chemistry AS chem
    ON co.hadm_id = chem.hadm_id
    AND co.starttime < chem.charttime
    AND co.endtime >= chem.charttime
  GROUP BY co.stay_id, co.hr
), plt AS (
  SELECT co.stay_id, co.hr, MIN(cbc.platelet) AS platelet_min
  FROM co
  LEFT JOIN mimiciv_derived.complete_blood_count AS cbc
    ON co.hadm_id = cbc.hadm_id
    AND co.starttime < cbc.charttime
    AND co.endtime >= cbc.charttime
  GROUP BY co.stay_id, co.hr
), pf AS (
  SELECT
    co.stay_id,
    co.hr,
    MIN(pafi.pao2fio2ratio_novent) AS pao2fio2ratio_novent,
    ARG_MIN(
      STRUCT_PACK(
        charttime := pafi.charttime,
        pao2 := pafi.pao2,
        fio2 := pafi.fio2,
        fio2_source := pafi.fio2_source
      ),
      STRUCT_PACK(
        ratio := pafi.pao2fio2ratio_novent,
        charttime := pafi.charttime
      )
    ) FILTER (
      WHERE pafi.pao2fio2ratio_novent IS NOT NULL
    ) AS pao2fio2ratio_novent_observation,
    MIN(pafi.pao2fio2ratio_vent) AS pao2fio2ratio_vent,
    ARG_MIN(
      STRUCT_PACK(
        charttime := pafi.charttime,
        pao2 := pafi.pao2,
        fio2 := pafi.fio2,
        fio2_source := pafi.fio2_source
      ),
      STRUCT_PACK(
        ratio := pafi.pao2fio2ratio_vent,
        charttime := pafi.charttime
      )
    ) FILTER (
      WHERE pafi.pao2fio2ratio_vent IS NOT NULL
    ) AS pao2fio2ratio_vent_observation
  FROM co
  LEFT JOIN pafi
    ON co.stay_id = pafi.stay_id
    AND co.starttime < pafi.charttime
    AND co.endtime >= pafi.charttime
  GROUP BY co.stay_id, co.hr
), uo_observations AS (
  SELECT
    co.stay_id,
    co.hr,
    uo.charttime,
    uo.urineoutput_24hr,
    uo.uo_tm_24hr,
    CASE
      WHEN uo.uo_tm_24hr >= 22 AND uo.uo_tm_24hr <= 30
      THEN uo.urineoutput_24hr / uo.uo_tm_24hr * 24
    END AS uo_24hr
  FROM co
  LEFT JOIN mimiciv_derived.urine_output_rate AS uo
    ON co.stay_id = uo.stay_id
    AND co.starttime < uo.charttime
    AND co.endtime >= uo.charttime
), uo AS (
  SELECT
    stay_id,
    hr,
    MAX(uo_24hr) AS uo_24hr,
    ARG_MAX(
      STRUCT_PACK(
        charttime := charttime,
        urineoutput_24hr := urineoutput_24hr,
        uo_tm_24hr := uo_tm_24hr
      ),
      STRUCT_PACK(uo_24hr := uo_24hr, charttime := charttime)
    ) FILTER (WHERE uo_24hr IS NOT NULL) AS uo_24hr_observation
  FROM uo_observations
  GROUP BY stay_id, hr
), vaso AS (
  SELECT
    co.stay_id,
    co.hr,
    MAX(CASE WHEN epi.vaso_rate > 0 THEN epi.vaso_rate END) AS rate_epinephrine,
    MAX(CASE WHEN nor.vaso_rate > 0 THEN nor.vaso_rate END) AS rate_norepinephrine,
    MAX(CASE WHEN dop.vaso_rate > 0 THEN dop.vaso_rate END) AS rate_dopamine,
    MAX(CASE WHEN dob.vaso_rate > 0 THEN dob.vaso_rate END) AS rate_dobutamine
  FROM co
  LEFT JOIN mimiciv_derived.epinephrine AS epi
    ON co.stay_id = epi.stay_id
    AND epi.starttime < co.endtime
    AND epi.endtime > co.starttime
    AND epi.endtime >= epi.starttime + INTERVAL '1' HOUR
  LEFT JOIN mimiciv_derived.norepinephrine AS nor
    ON co.stay_id = nor.stay_id
    AND nor.starttime < co.endtime
    AND nor.endtime > co.starttime
    AND nor.endtime >= nor.starttime + INTERVAL '1' HOUR
  LEFT JOIN mimiciv_derived.dopamine AS dop
    ON co.stay_id = dop.stay_id
    AND dop.starttime < co.endtime
    AND dop.endtime > co.starttime
    AND dop.endtime >= dop.starttime + INTERVAL '1' HOUR
  LEFT JOIN mimiciv_derived.dobutamine AS dob
    ON co.stay_id = dob.stay_id
    AND dob.starttime < co.endtime
    AND dob.endtime > co.starttime
    AND dob.endtime >= dob.starttime + INTERVAL '1' HOUR
  WHERE
    NOT epi.stay_id IS NULL OR NOT nor.stay_id IS NULL
    OR NOT dop.stay_id IS NULL OR NOT dob.stay_id IS NULL
  GROUP BY co.stay_id, co.hr
), scorecomp AS (
  SELECT
    co.subject_id,
    co.hadm_id,
    co.stay_id,
    co.hr,
    co.starttime,
    co.endtime,
    co.is_boundary,
    co.boundary_target_hr,
    pf.pao2fio2ratio_novent,
    pf.pao2fio2ratio_novent_observation.charttime AS pao2fio2ratio_novent_charttime,
    pf.pao2fio2ratio_novent_observation.pao2 AS pao2_novent,
    pf.pao2fio2ratio_novent_observation.fio2 AS fio2_novent,
    pf.pao2fio2ratio_novent_observation.fio2_source AS fio2_source_novent,
    pf.pao2fio2ratio_vent,
    pf.pao2fio2ratio_vent_observation.charttime AS pao2fio2ratio_vent_charttime,
    pf.pao2fio2ratio_vent_observation.pao2 AS pao2_vent,
    pf.pao2fio2ratio_vent_observation.fio2 AS fio2_vent,
    pf.pao2fio2ratio_vent_observation.fio2_source AS fio2_source_vent,
    vaso.rate_epinephrine,
    vaso.rate_norepinephrine,
    vaso.rate_dopamine,
    vaso.rate_dobutamine,
    vs.meanbp_min,
    gcs.gcs_min,
    gcs.gcs_min_observation.charttime AS gcs_charttime,
    gcs.gcs_min_observation.motor AS gcs_motor,
    gcs.gcs_min_observation.verbal AS gcs_verbal,
    gcs.gcs_min_observation.eyes AS gcs_eyes,
    gcs.gcs_min_observation.unable AS gcs_unable,
    gcs.gcs_min_observation.components_measured AS gcs_components_measured,
    uo.uo_24hr,
    uo.uo_24hr_observation.charttime AS uo_24hr_charttime,
    uo.uo_24hr_observation.urineoutput_24hr,
    uo.uo_24hr_observation.uo_tm_24hr,
    bili.bilirubin_max,
    cr.creatinine_max,
    plt.platelet_min
  FROM co
  LEFT JOIN vs ON co.stay_id = vs.stay_id AND co.hr = vs.hr
  LEFT JOIN gcs ON co.stay_id = gcs.stay_id AND co.hr = gcs.hr
  LEFT JOIN bili ON co.stay_id = bili.stay_id AND co.hr = bili.hr
  LEFT JOIN cr ON co.stay_id = cr.stay_id AND co.hr = cr.hr
  LEFT JOIN plt ON co.stay_id = plt.stay_id AND co.hr = plt.hr
  LEFT JOIN pf ON co.stay_id = pf.stay_id AND co.hr = pf.hr
  LEFT JOIN uo ON co.stay_id = uo.stay_id AND co.hr = uo.hr
  LEFT JOIN vaso ON co.stay_id = vaso.stay_id AND co.hr = vaso.hr
), scorecalc AS (
  SELECT
    scorecomp.*,
    CASE
      WHEN pao2fio2ratio_vent < 100 THEN 4
      WHEN pao2fio2ratio_vent < 200 THEN 3
      WHEN pao2fio2ratio_novent < 300 THEN 2
      WHEN pao2fio2ratio_vent < 300 THEN 2
      WHEN pao2fio2ratio_novent < 400 THEN 1
      WHEN pao2fio2ratio_vent < 400 THEN 1
      WHEN COALESCE(pao2fio2ratio_vent, pao2fio2ratio_novent) IS NULL THEN NULL
      ELSE 0
    END AS respiration,
    CASE
      WHEN platelet_min < 20 THEN 4
      WHEN platelet_min < 50 THEN 3
      WHEN platelet_min < 100 THEN 2
      WHEN platelet_min < 150 THEN 1
      WHEN platelet_min IS NULL THEN NULL
      ELSE 0
    END AS coagulation,
    CASE
      WHEN bilirubin_max >= 12.0 THEN 4
      WHEN bilirubin_max >= 6.0 THEN 3
      WHEN bilirubin_max >= 2.0 THEN 2
      WHEN bilirubin_max >= 1.2 THEN 1
      WHEN bilirubin_max IS NULL THEN NULL
      ELSE 0
    END AS liver,
    CASE
      WHEN rate_dopamine > 15 OR rate_epinephrine > 0.1 OR rate_norepinephrine > 0.1 THEN 4
      WHEN rate_dopamine > 5
        OR (rate_epinephrine > 0 AND rate_epinephrine <= 0.1)
        OR (rate_norepinephrine > 0 AND rate_norepinephrine <= 0.1) THEN 3
      WHEN rate_dopamine > 0 OR rate_dobutamine > 0 THEN 2
      WHEN meanbp_min < 70 THEN 1
      WHEN COALESCE(meanbp_min, rate_dopamine, rate_dobutamine, rate_epinephrine, rate_norepinephrine) IS NULL THEN NULL
      ELSE 0
    END AS cardiovascular,
    CASE
      WHEN gcs_min >= 13 AND gcs_min <= 14 THEN 1
      WHEN gcs_min >= 10 AND gcs_min <= 12 THEN 2
      WHEN gcs_min >= 6 AND gcs_min <= 9 THEN 3
      WHEN gcs_min < 6 THEN 4
      WHEN gcs_min IS NULL THEN NULL
      ELSE 0
    END AS cns,
    CASE
      WHEN creatinine_max >= 5.0 THEN 4
      WHEN uo_24hr < 200 THEN 4
      WHEN creatinine_max >= 3.5 AND creatinine_max < 5.0 THEN 3
      WHEN uo_24hr < 500 THEN 3
      WHEN creatinine_max >= 2.0 AND creatinine_max < 3.5 THEN 2
      WHEN creatinine_max >= 1.2 AND creatinine_max < 2.0 THEN 1
      WHEN COALESCE(uo_24hr, creatinine_max) IS NULL THEN NULL
      ELSE 0
    END AS renal
  FROM scorecomp
), rolling_base AS (
  SELECT
    s.*,
    MAX(respiration) OVER w AS respiration_24hours_raw,
    MAX(coagulation) OVER w AS coagulation_24hours_raw,
    MAX(liver) OVER w AS liver_24hours_raw,
    MAX(cardiovascular) OVER w AS cardiovascular_24hours_raw,
    MAX(cns) OVER w AS cns_24hours_raw,
    MAX(renal) OVER w AS renal_24hours_raw
  FROM scorecalc AS s
  WHERE NOT is_boundary
  WINDOW w AS (
    PARTITION BY stay_id ORDER BY hr NULLS FIRST
    ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
  )
), rolling AS (
  SELECT
    rb.* EXCLUDE (
      respiration_24hours_raw, coagulation_24hours_raw, liver_24hours_raw,
      cardiovascular_24hours_raw, cns_24hours_raw, renal_24hours_raw
    ),
    GREATEST(rb.respiration_24hours_raw, b.respiration) AS respiration_24hours_raw,
    GREATEST(rb.coagulation_24hours_raw, b.coagulation) AS coagulation_24hours_raw,
    GREATEST(rb.liver_24hours_raw, b.liver) AS liver_24hours_raw,
    GREATEST(rb.cardiovascular_24hours_raw, b.cardiovascular) AS cardiovascular_24hours_raw,
    GREATEST(rb.cns_24hours_raw, b.cns) AS cns_24hours_raw,
    GREATEST(rb.renal_24hours_raw, b.renal) AS renal_24hours_raw
  FROM rolling_base AS rb
  LEFT JOIN scorecalc AS b
    ON b.is_boundary
    AND rb.stay_id = b.stay_id
    AND rb.hr = b.boundary_target_hr
), score_final AS (
  SELECT
    rolling.*,
    COALESCE(respiration_24hours_raw, 0) AS respiration_24hours,
    COALESCE(coagulation_24hours_raw, 0) AS coagulation_24hours,
    COALESCE(liver_24hours_raw, 0) AS liver_24hours,
    COALESCE(cardiovascular_24hours_raw, 0) AS cardiovascular_24hours,
    COALESCE(cns_24hours_raw, 0) AS cns_24hours,
    COALESCE(renal_24hours_raw, 0) AS renal_24hours,
    COALESCE(respiration_24hours_raw, 0)
      + COALESCE(coagulation_24hours_raw, 0)
      + COALESCE(liver_24hours_raw, 0)
      + COALESCE(cardiovascular_24hours_raw, 0)
      + COALESCE(cns_24hours_raw, 0)
      + COALESCE(renal_24hours_raw, 0) AS sofa_24hours
  FROM rolling
)
SELECT *
FROM score_final
WHERE hr >= 0
ORDER BY stay_id, hr;
