-- Project adaptation of MIT-LCP mimic-code v3.0.1 first_day_sofa.sql.
-- Upstream SHA-256: 02736bd4faf9885fed67de777ec85852b50e93ac1ddc03bd6e5039216ce3d86e
-- Adaptations: inline only SOFA-used first-day aggregates, and restore the
-- ventilated P/F <300 and <400 respiratory branches present in classic SOFA.
DROP TABLE IF EXISTS mimiciv_derived.sofa_first_day_adapted;
CREATE TABLE mimiciv_derived.sofa_first_day_adapted AS
WITH vaso_stg AS (
  SELECT ie.stay_id, 'norepinephrine' AS treatment, mv.vaso_rate AS rate
  FROM mimiciv_icu.icustays ie
  JOIN mimiciv_derived.norepinephrine mv ON ie.stay_id = mv.stay_id
    AND mv.starttime >= ie.intime - INTERVAL '6' HOUR
    AND mv.starttime <= ie.intime + INTERVAL '1' DAY
  UNION ALL
  SELECT ie.stay_id, 'epinephrine', mv.vaso_rate
  FROM mimiciv_icu.icustays ie
  JOIN mimiciv_derived.epinephrine mv ON ie.stay_id = mv.stay_id
    AND mv.starttime >= ie.intime - INTERVAL '6' HOUR
    AND mv.starttime <= ie.intime + INTERVAL '1' DAY
  UNION ALL
  SELECT ie.stay_id, 'dobutamine', mv.vaso_rate
  FROM mimiciv_icu.icustays ie
  JOIN mimiciv_derived.dobutamine mv ON ie.stay_id = mv.stay_id
    AND mv.starttime >= ie.intime - INTERVAL '6' HOUR
    AND mv.starttime <= ie.intime + INTERVAL '1' DAY
  UNION ALL
  SELECT ie.stay_id, 'dopamine', mv.vaso_rate
  FROM mimiciv_icu.icustays ie
  JOIN mimiciv_derived.dopamine mv ON ie.stay_id = mv.stay_id
    AND mv.starttime >= ie.intime - INTERVAL '6' HOUR
    AND mv.starttime <= ie.intime + INTERVAL '1' DAY
), vaso AS (
  SELECT ie.stay_id,
    MAX(CASE WHEN treatment = 'norepinephrine' THEN rate END) AS rate_norepinephrine,
    MAX(CASE WHEN treatment = 'epinephrine' THEN rate END) AS rate_epinephrine,
    MAX(CASE WHEN treatment = 'dopamine' THEN rate END) AS rate_dopamine,
    MAX(CASE WHEN treatment = 'dobutamine' THEN rate END) AS rate_dobutamine
  FROM mimiciv_icu.icustays ie
  LEFT JOIN vaso_stg v USING (stay_id)
  GROUP BY ie.stay_id
), pafi_events AS (
  SELECT ie.stay_id, bg.charttime, bg.pao2fio2ratio,
    CASE WHEN vd.stay_id IS NOT NULL THEN 1 ELSE 0 END AS isvent
  FROM mimiciv_icu.icustays ie
  LEFT JOIN mimiciv_derived.bg bg ON ie.subject_id = bg.subject_id
    AND bg.charttime >= ie.intime - INTERVAL '6' HOUR
    AND bg.charttime <= ie.intime + INTERVAL '1' DAY
  LEFT JOIN mimiciv_derived.ventilation vd ON ie.stay_id = vd.stay_id
    AND bg.charttime >= vd.starttime AND bg.charttime <= vd.endtime
    AND vd.ventilation_status = 'InvasiveVent'
), pafi AS (
  SELECT stay_id,
    MIN(CASE WHEN isvent = 0 THEN pao2fio2ratio END) AS pao2fio2_novent_min,
    MIN(CASE WHEN isvent = 1 THEN pao2fio2ratio END) AS pao2fio2_vent_min
  FROM pafi_events GROUP BY stay_id
), vitals AS (
  SELECT ie.stay_id, MIN(v.mbp) AS mbp_min
  FROM mimiciv_icu.icustays ie
  LEFT JOIN mimiciv_derived.vitalsign v ON ie.stay_id = v.stay_id
    AND v.charttime >= ie.intime - INTERVAL '6' HOUR
    AND v.charttime <= ie.intime + INTERVAL '1' DAY
  GROUP BY ie.stay_id
), chemistry_labs AS (
  SELECT ie.stay_id, MAX(chem.creatinine) AS creatinine_max
  FROM mimiciv_icu.icustays ie
  LEFT JOIN mimiciv_derived.chemistry chem ON chem.subject_id = ie.subject_id
    AND chem.charttime >= ie.intime - INTERVAL '6' HOUR
    AND chem.charttime <= ie.intime + INTERVAL '1' DAY
  GROUP BY ie.stay_id
), enzyme_labs AS (
  SELECT ie.stay_id, MAX(enz.bilirubin_total) AS bilirubin_max
  FROM mimiciv_icu.icustays ie
  LEFT JOIN mimiciv_derived.enzyme enz ON enz.subject_id = ie.subject_id
    AND enz.charttime >= ie.intime - INTERVAL '6' HOUR
    AND enz.charttime <= ie.intime + INTERVAL '1' DAY
  GROUP BY ie.stay_id
), cbc_labs AS (
  SELECT ie.stay_id, MIN(cbc.platelet) AS platelet_min
  FROM mimiciv_icu.icustays ie
  LEFT JOIN mimiciv_derived.complete_blood_count cbc ON cbc.subject_id = ie.subject_id
    AND cbc.charttime >= ie.intime - INTERVAL '6' HOUR
    AND cbc.charttime <= ie.intime + INTERVAL '1' DAY
  GROUP BY ie.stay_id
), urine AS (
  SELECT ie.stay_id, SUM(uo.urineoutput) AS urineoutput
  FROM mimiciv_icu.icustays ie
  LEFT JOIN mimiciv_derived.urine_output uo ON ie.stay_id = uo.stay_id
    AND uo.charttime >= ie.intime
    AND uo.charttime <= ie.intime + INTERVAL '1' DAY
  GROUP BY ie.stay_id
), gcs_ranked AS (
  SELECT ie.stay_id, g.gcs,
    ROW_NUMBER() OVER (PARTITION BY ie.stay_id ORDER BY g.gcs NULLS FIRST) AS seq
  FROM mimiciv_icu.icustays ie
  LEFT JOIN mimiciv_derived.gcs g ON ie.stay_id = g.stay_id
    AND g.charttime >= ie.intime - INTERVAL '6' HOUR
    AND g.charttime <= ie.intime + INTERVAL '1' DAY
), inputs AS (
  SELECT ie.subject_id, ie.hadm_id, ie.stay_id,
    v.mbp_min, va.rate_norepinephrine, va.rate_epinephrine,
    va.rate_dopamine, va.rate_dobutamine,
    chem.creatinine_max, enz.bilirubin_max, cbc.platelet_min,
    pf.pao2fio2_novent_min, pf.pao2fio2_vent_min,
    u.urineoutput, g.gcs AS gcs_min
  FROM mimiciv_icu.icustays ie
  LEFT JOIN vaso va USING (stay_id)
  LEFT JOIN pafi pf USING (stay_id)
  LEFT JOIN vitals v USING (stay_id)
  LEFT JOIN chemistry_labs chem USING (stay_id)
  LEFT JOIN enzyme_labs enz USING (stay_id)
  LEFT JOIN cbc_labs cbc USING (stay_id)
  LEFT JOIN urine u USING (stay_id)
  LEFT JOIN gcs_ranked g ON ie.stay_id = g.stay_id AND g.seq = 1
), components AS (
  SELECT *,
    CASE
      WHEN pao2fio2_vent_min < 100 THEN 4
      WHEN pao2fio2_vent_min < 200 THEN 3
      WHEN pao2fio2_vent_min < 300 OR pao2fio2_novent_min < 300 THEN 2
      WHEN pao2fio2_vent_min < 400 OR pao2fio2_novent_min < 400 THEN 1
      WHEN COALESCE(pao2fio2_vent_min, pao2fio2_novent_min) IS NULL THEN NULL
      ELSE 0 END AS respiration_score,
    CASE WHEN platelet_min < 20 THEN 4 WHEN platelet_min < 50 THEN 3
      WHEN platelet_min < 100 THEN 2 WHEN platelet_min < 150 THEN 1
      WHEN platelet_min IS NULL THEN NULL ELSE 0 END AS coagulation_score,
    CASE WHEN bilirubin_max >= 12 THEN 4 WHEN bilirubin_max >= 6 THEN 3
      WHEN bilirubin_max >= 2 THEN 2 WHEN bilirubin_max >= 1.2 THEN 1
      WHEN bilirubin_max IS NULL THEN NULL ELSE 0 END AS liver_score,
    CASE WHEN rate_dopamine > 15 OR rate_epinephrine > 0.1 OR rate_norepinephrine > 0.1 THEN 4
      WHEN rate_dopamine > 5 OR rate_epinephrine <= 0.1 OR rate_norepinephrine <= 0.1 THEN 3
      WHEN rate_dopamine > 0 OR rate_dobutamine > 0 THEN 2
      WHEN mbp_min < 70 THEN 1
      WHEN COALESCE(mbp_min, rate_dopamine, rate_dobutamine, rate_epinephrine, rate_norepinephrine) IS NULL THEN NULL
      ELSE 0 END AS cardiovascular_score,
    CASE WHEN gcs_min BETWEEN 13 AND 14 THEN 1 WHEN gcs_min BETWEEN 10 AND 12 THEN 2
      WHEN gcs_min BETWEEN 6 AND 9 THEN 3 WHEN gcs_min < 6 THEN 4
      WHEN gcs_min IS NULL THEN NULL ELSE 0 END AS cns_score,
    CASE WHEN creatinine_max >= 5 OR urineoutput < 200 THEN 4
      WHEN (creatinine_max >= 3.5 AND creatinine_max < 5) OR urineoutput < 500 THEN 3
      WHEN creatinine_max >= 2 AND creatinine_max < 3.5 THEN 2
      WHEN creatinine_max >= 1.2 AND creatinine_max < 2 THEN 1
      WHEN COALESCE(urineoutput, creatinine_max) IS NULL THEN NULL ELSE 0 END AS renal_score
  FROM inputs
)
SELECT *,
  COALESCE(respiration_score, 0) + COALESCE(coagulation_score, 0)
  + COALESCE(liver_score, 0) + COALESCE(cardiovascular_score, 0)
  + COALESCE(cns_score, 0) + COALESCE(renal_score, 0) AS sofa_first_day_adapted,
  'sofa-first-day-adapted-v1' AS adaptation_version,
  TRUE AS ventilated_pf_correction_applied
FROM components;
