-- Project-owned MIMIC-IV adaptation of the original 2005 SAPS III admission model.
-- It is intentionally NOT labelled official SAPS III. See docs/scores/saps_iii_adapted.md.
CREATE TABLE mimiciv_derived.saps_iii_adapted AS
WITH cohort AS (
  SELECT i.*, a.admittime, a.admission_type, a.admission_location,
         p.anchor_age + EXTRACT(year FROM i.intime) - p.anchor_year AS age,
         GREATEST(0.0, DATE_DIFF('second', a.admittime, i.intime) / 86400.0) AS preicu_days
  FROM mimiciv_icu.icustays i
  JOIN mimiciv_hosp.admissions a USING (subject_id, hadm_id)
  JOIN mimiciv_hosp.patients p USING (subject_id)
),
prior_location AS (
  SELECT c.stay_id, arg_max(t.careunit, t.intime) AS careunit
  FROM cohort c LEFT JOIN mimiciv_hosp.transfers t
    ON t.hadm_id = c.hadm_id AND t.intime < c.intime
  GROUP BY c.stay_id
),
services AS (
  SELECT c.stay_id, arg_max(s.curr_service, s.transfertime) FILTER (WHERE s.transfertime <= c.intime) AS service
  FROM cohort c LEFT JOIN mimiciv_hosp.services s USING (hadm_id)
  GROUP BY c.stay_id
),
dx AS (
  SELECT c.stay_id,
    MAX(CASE WHEN (d.icd_version=10 AND regexp_matches(replace(upper(d.icd_code),'.',''), '^(B2[0-4])')) OR (d.icd_version=9 AND regexp_matches(d.icd_code, '^04[2-4]')) THEN 1 ELSE 0 END) AS aids,
    MAX(CASE WHEN (d.icd_version=10 AND regexp_matches(replace(upper(d.icd_code),'.',''), '^C7[7-9]')) OR (d.icd_version=9 AND regexp_matches(d.icd_code, '^19[6-9]')) THEN 1 ELSE 0 END) AS metastatic,
    MAX(CASE WHEN (d.icd_version=10 AND regexp_matches(replace(upper(d.icd_code),'.',''), '^C(8[1-9]|9[0-6])')) OR (d.icd_version=9 AND regexp_matches(d.icd_code, '^(20[0-8])')) THEN 1 ELSE 0 END) AS hematologic,
    MAX(CASE WHEN (d.icd_version=10 AND regexp_matches(replace(upper(d.icd_code),'.',''), '^(K703|K717|K72[19]|K74|K766)')) OR (d.icd_version=9 AND regexp_matches(d.icd_code, '^571[256]')) THEN 1 ELSE 0 END) AS cirrhosis,
    MAX(CASE WHEN (d.icd_version=10 AND regexp_matches(replace(upper(d.icd_code),'.',''), '^(I4[789]|R0[01])')) OR (d.icd_version=9 AND regexp_matches(d.icd_code, '^(427|7850)')) THEN 1 ELSE 0 END) AS rhythm,
    MAX(CASE WHEN (d.icd_version=10 AND regexp_matches(replace(upper(d.icd_code),'.',''), '^(G40|R56)')) OR (d.icd_version=9 AND regexp_matches(d.icd_code, '^(345|7803)')) THEN 1 ELSE 0 END) AS seizure,
    MAX(CASE WHEN (d.icd_version=10 AND regexp_matches(replace(upper(d.icd_code),'.',''), '^(R571|R578|R579)')) OR (d.icd_version=9 AND regexp_matches(d.icd_code, '^7855[19]')) THEN 1 ELSE 0 END) AS nonseptic_shock,
    MAX(CASE WHEN (d.icd_version=10 AND regexp_matches(replace(upper(d.icd_code),'.',''), '^R6521')) OR (d.icd_version=9 AND regexp_matches(d.icd_code, '^78552')) THEN 1 ELSE 0 END) AS septic_shock,
    MAX(CASE WHEN (d.icd_version=10 AND regexp_matches(replace(upper(d.icd_code),'.',''), '^T782')) OR (d.icd_version=9 AND regexp_matches(d.icd_code, '^9950')) THEN 1 ELSE 0 END) AS anaphylaxis,
    MAX(CASE WHEN (d.icd_version=10 AND regexp_matches(replace(upper(d.icd_code),'.',''), '^K72')) OR (d.icd_version=9 AND regexp_matches(d.icd_code, '^5722')) THEN 1 ELSE 0 END) AS liver_failure,
    MAX(CASE WHEN (d.icd_version=10 AND regexp_matches(replace(upper(d.icd_code),'.',''), '^K85')) OR (d.icd_version=9 AND regexp_matches(d.icd_code, '^5770')) THEN 1 ELSE 0 END) AS pancreatitis,
    MAX(CASE WHEN (d.icd_version=10 AND regexp_matches(replace(upper(d.icd_code),'.',''), '^(R10|K5[5-7]|K6[3-6])')) OR (d.icd_version=9 AND regexp_matches(d.icd_code, '^(789|56)')) THEN 1 ELSE 0 END) AS acute_abdomen,
    MAX(CASE WHEN (d.icd_version=10 AND regexp_matches(replace(upper(d.icd_code),'.',''), '^(D3[23]|C7[01])')) OR (d.icd_version=9 AND regexp_matches(d.icd_code, '^(191|225)')) THEN 1 ELSE 0 END) AS intracranial_mass,
    MAX(CASE WHEN (d.icd_version=10 AND regexp_matches(replace(upper(d.icd_code),'.',''), '^(I6[0-4]|R29)')) OR (d.icd_version=9 AND regexp_matches(d.icd_code, '^(43[0-4]|781)')) THEN 1 ELSE 0 END) AS focal_deficit,
    MAX(CASE WHEN (d.icd_version=10 AND regexp_matches(replace(upper(d.icd_code),'.',''), '^(R4[01]|F05)')) OR (d.icd_version=9 AND regexp_matches(d.icd_code, '^(7800|2930)')) THEN 1 ELSE 0 END) AS altered_mental,
    MAX(CASE WHEN (d.icd_version=10 AND regexp_matches(replace(upper(d.icd_code),'.',''), '^(J1[0-8]|J69|J85|J86)')) OR (d.icd_version=9 AND regexp_matches(d.icd_code, '^(48[0-7]|507|510)')) THEN 1 ELSE 0 END) AS respiratory_infection,
    MAX(CASE WHEN (d.icd_version=10 AND regexp_matches(replace(upper(d.icd_code),'.',''), '^[ST]')) OR (d.icd_version=9 AND regexp_matches(d.icd_code, '^(8|9)')) THEN 1 ELSE 0 END) AS trauma
  FROM cohort c LEFT JOIN mimiciv_hosp.diagnoses_icd d USING (hadm_id)
  GROUP BY c.stay_id
),
procedures AS (
  SELECT c.stay_id,
    MAX(CASE WHEN pr.chartdate <= CAST(c.intime AS DATE) THEN 1 ELSE 0 END) AS surgery,
    MAX(CASE WHEN ((pr.icd_version=10 AND regexp_matches(replace(upper(pr.icd_code),'.',''), '^(02[012])')) OR (pr.icd_version=9 AND regexp_matches(pr.icd_code, '^(36(1[0-9]|2)|361)'))) AND pr.chartdate <= CAST(c.intime AS DATE) THEN 1 ELSE 0 END) AS cabg,
    MAX(CASE WHEN ((pr.icd_version=10 AND regexp_matches(replace(upper(pr.icd_code),'.',''), '^(00|01|03)')) OR (pr.icd_version=9 AND regexp_matches(pr.icd_code, '^0[1-5]'))) AND pr.chartdate <= CAST(c.intime AS DATE) THEN 1 ELSE 0 END) AS neurosurgery,
    MAX(CASE WHEN ((pr.icd_version=10 AND regexp_matches(replace(upper(pr.icd_code),'.',''), '^(0TY|0FY|0BY|0DY|0GY|0UY)')) OR (pr.icd_version=9 AND regexp_matches(pr.icd_code, '^(50[56]|55[6-9]|33[56]|46[89]|528)'))) AND pr.chartdate <= CAST(c.intime AS DATE) THEN 1 ELSE 0 END) AS transplant
  FROM cohort c LEFT JOIN mimiciv_hosp.procedures_icd pr USING (hadm_id)
  GROUP BY c.stay_id
),
gcs_rows AS (
  SELECT c.stay_id, ce.charttime,
    MAX(ce.valuenum) FILTER (WHERE ce.itemid=220739 AND ce.valuenum BETWEEN 1 AND 4) AS eye,
    MAX(ce.valuenum) FILTER (WHERE ce.itemid=223900 AND ce.valuenum BETWEEN 1 AND 5) AS verbal,
    MAX(ce.valuenum) FILTER (WHERE ce.itemid=223901 AND ce.valuenum BETWEEN 1 AND 6) AS motor
  FROM cohort c LEFT JOIN mimiciv_icu.chartevents ce USING (stay_id)
  WHERE ce.itemid IN (220739,223900,223901)
  GROUP BY c.stay_id, ce.charttime
),
gcs AS (
  SELECT stay_id, MIN(eye+verbal+motor) FILTER (
    WHERE eye IS NOT NULL AND verbal IS NOT NULL AND motor IS NOT NULL
  ) AS gcs_estimated
  FROM gcs_rows GROUP BY stay_id
),
chart AS (
  SELECT c.stay_id,
    MAX(ce.valuenum) FILTER (WHERE ce.itemid=220045 AND ce.valuenum BETWEEN 0 AND 400) AS hr_max,
    MIN(ce.valuenum) FILTER (WHERE ce.itemid IN (220050,220179,225309) AND ce.valuenum BETWEEN 0 AND 300) AS sbp_min,
    MAX(CASE WHEN ce.itemid=223761 THEN (ce.valuenum-32)/1.8 ELSE ce.valuenum END)
      FILTER (WHERE ce.itemid IN (223761,223762) AND ce.valuenum IS NOT NULL) AS temp_max,
    MAX(CASE WHEN ce.itemid IN (223848,223849,224684,224688,224690,229314) AND coalesce(ce.value,'') <> '' THEN 1 ELSE 0 END) AS mechanical_ventilation
  FROM cohort c LEFT JOIN mimiciv_icu.chartevents ce USING (stay_id)
  GROUP BY c.stay_id
),
labs AS (
  SELECT c.stay_id,
    MAX(le.valuenum) FILTER (WHERE le.itemid=50885 AND le.valuenum BETWEEN 0 AND 100) AS bilirubin_max,
    MAX(le.valuenum) FILTER (WHERE le.itemid=50912 AND le.valuenum BETWEEN 0 AND 50) AS creatinine_max,
    MIN(le.valuenum) FILTER (WHERE le.itemid=51301 AND le.valuenum BETWEEN 0 AND 1000) AS wbc_min,
    MIN(le.valuenum) FILTER (WHERE le.itemid=51265 AND le.valuenum BETWEEN 0 AND 10000) AS platelet_min,
    MIN(le.valuenum) FILTER (WHERE le.itemid=50820 AND le.valuenum BETWEEN 6.0 AND 8.0) AS ph_min,
    MIN(le.valuenum) FILTER (WHERE le.itemid=50821 AND le.valuenum BETWEEN 0 AND 800) AS pao2_min,
    MIN(le.valuenum / NULLIF(CASE WHEN coalesce(fio2.valuenum, chart_fio2.valuenum) <= 1
                                  THEN coalesce(fio2.valuenum, chart_fio2.valuenum)
                                  ELSE coalesce(fio2.valuenum, chart_fio2.valuenum)/100 END,0))
      FILTER (WHERE le.itemid=50821 AND coalesce(fio2.valuenum, chart_fio2.valuenum) > 0) AS pf_min
  FROM cohort c LEFT JOIN mimiciv_hosp.labevents le ON le.hadm_id=c.hadm_id
  LEFT JOIN mimiciv_hosp.labevents fio2 ON fio2.hadm_id=le.hadm_id
       AND fio2.itemid=50816 AND fio2.charttime=le.charttime
       AND (fio2.specimen_id=le.specimen_id OR fio2.specimen_id IS NULL OR le.specimen_id IS NULL)
  LEFT JOIN LATERAL (
    SELECT ce.valuenum FROM mimiciv_icu.chartevents ce
    WHERE ce.stay_id=c.stay_id AND ce.itemid=223835 AND ce.valuenum>0
      AND ce.charttime<=le.charttime AND ce.charttime>=le.charttime-INTERVAL '2' HOUR
    ORDER BY ce.charttime DESC LIMIT 1
  ) chart_fio2 ON TRUE
  GROUP BY c.stay_id
),
vasoactive AS (
  SELECT c.stay_id,
    MAX(CASE WHEN DATE_DIFF('second', GREATEST(ie.starttime,c.intime-INTERVAL '24' HOUR), LEAST(ie.endtime,c.intime)) >= 3600
                  AND (ie.itemid <> 221662 OR (ie.rate >= 5 AND lower(coalesce(ie.rateuom,'')) LIKE '%mcg/kg/min%'))
             THEN 1 ELSE 0 END) AS vasoactive_preicu
  FROM cohort c LEFT JOIN mimiciv_icu.inputevents ie USING (stay_id)
  GROUP BY c.stay_id
),
raw AS (
 SELECT c.*, pl.careunit AS prior_careunit, sv.service, dx.*, pr.*, ch.*, la.*, va.vasoactive_preicu,
   gc.gcs_estimated,
   CASE WHEN upper(coalesce(c.admission_type,'')) LIKE '%ELECTIVE%'
          AND (upper(coalesce(sv.service,'')) LIKE '%SURG%' OR upper(coalesce(pl.careunit,'')) LIKE '%OR%') THEN 1 ELSE 0 END AS planned_icu_proxy
 FROM cohort c
 LEFT JOIN prior_location pl USING (stay_id) LEFT JOIN services sv USING (stay_id)
 LEFT JOIN dx USING (stay_id) LEFT JOIN procedures pr USING (stay_id)
 LEFT JOIN chart ch USING (stay_id) LEFT JOIN gcs gc USING (stay_id) LEFT JOIN labs la USING (stay_id)
 LEFT JOIN vasoactive va USING (stay_id)
),
components AS (
 SELECT raw.*,
   CASE WHEN age IS NULL THEN NULL WHEN age <40 THEN 0 WHEN age<60 THEN 5 WHEN age<70 THEN 9 WHEN age<75 THEN 13 WHEN age<80 THEN 15 ELSE 18 END AS age_score,
   CASE WHEN preicu_days<14 THEN 0 WHEN preicu_days<28 THEN 6 ELSE 7 END AS hospital_los_score,
   CASE WHEN upper(coalesce(prior_careunit,'')) LIKE '%OR%' THEN 0
        WHEN upper(coalesce(prior_careunit,'')) LIKE '%EMERGENCY%' THEN 5
        WHEN regexp_matches(upper(coalesce(prior_careunit,'')), '(ICU|CCU|PACU|INTERMEDIATE)') THEN 7
        WHEN prior_careunit IS NULL THEN 0 ELSE 8 END AS admission_location_score,
   11*coalesce(metastatic,0)+6*coalesce(hematologic,0)+8*coalesce(cirrhosis,0)+8*coalesce(aids,0) AS comorbidity_score,
   CASE WHEN coalesce(vasoactive_preicu,0)=1 THEN 3 ELSE 0 END AS vasoactive_score,
   CASE WHEN planned_icu_proxy=1 THEN 0 ELSE 3 END AS planned_icu_score,
   CASE WHEN coalesce(intracranial_mass,0)=1 THEN 10 WHEN coalesce(pancreatitis,0)=1 THEN 9
        WHEN coalesce(focal_deficit,0)=1 THEN 7 WHEN coalesce(liver_failure,0)=1 THEN 6
        WHEN coalesce(septic_shock,0)=1 OR coalesce(anaphylaxis,0)=1 THEN 5
        WHEN coalesce(altered_mental,0)=1 THEN 4
        WHEN coalesce(nonseptic_shock,0)=1 OR coalesce(acute_abdomen,0)=1 THEN 3
        WHEN coalesce(seizure,0)=1 THEN -4 WHEN coalesce(rhythm,0)=1 THEN -5 ELSE 0 END AS admission_reason_score,
   CASE WHEN coalesce(surgery,0)=0 THEN 5 WHEN upper(coalesce(admission_type,'')) LIKE '%ELECTIVE%' THEN 0 ELSE 6 END AS surgery_status_score,
   CASE WHEN coalesce(transplant,0)=1 THEN -11 WHEN coalesce(trauma,0)=1 THEN -8
        WHEN coalesce(cabg,0)=1 THEN -6 WHEN coalesce(neurosurgery,0)=1 THEN 5 ELSE 0 END AS surgical_site_score,
   4*CAST(preicu_days>=2 AND (coalesce(respiratory_infection,0)=1 OR coalesce(septic_shock,0)=1) AS INTEGER)
      +5*coalesce(respiratory_infection,0) AS infection_score,
   CASE WHEN gcs_estimated IS NULL THEN NULL WHEN gcs_estimated<=4 THEN 15 WHEN gcs_estimated=5 THEN 10
        WHEN gcs_estimated=6 THEN 7 WHEN gcs_estimated<=12 THEN 2 ELSE 0 END AS gcs_score,
   CASE WHEN hr_max IS NULL THEN NULL WHEN hr_max<120 THEN 0 WHEN hr_max<160 THEN 5 ELSE 7 END AS hr_score,
   CASE WHEN sbp_min IS NULL THEN NULL WHEN sbp_min<40 THEN 11 WHEN sbp_min<70 THEN 8 WHEN sbp_min<120 THEN 3 ELSE 0 END AS sysbp_score,
   CASE WHEN temp_max IS NULL THEN NULL WHEN temp_max<35 THEN 7 ELSE 0 END AS temp_score,
   CASE WHEN bilirubin_max IS NULL THEN NULL WHEN bilirubin_max<2 THEN 0 WHEN bilirubin_max<6 THEN 4 ELSE 5 END AS bilirubin_score,
   CASE WHEN creatinine_max IS NULL THEN NULL WHEN creatinine_max<1.2 THEN 0 WHEN creatinine_max<2 THEN 2 WHEN creatinine_max<3.5 THEN 7 ELSE 8 END AS creatinine_score,
   CASE WHEN wbc_min IS NULL THEN NULL WHEN wbc_min<15 THEN 0 ELSE 2 END AS wbc_score,
   CASE WHEN platelet_min IS NULL THEN NULL WHEN platelet_min<20 THEN 13 WHEN platelet_min<50 THEN 8 WHEN platelet_min<100 THEN 5 ELSE 0 END AS platelet_score,
   CASE WHEN ph_min IS NULL THEN NULL WHEN ph_min<=7.25 THEN 3 ELSE 0 END AS ph_score,
   CASE WHEN mechanical_ventilation=1 THEN CASE WHEN pf_min IS NULL THEN NULL WHEN pf_min<100 THEN 11 ELSE 7 END
        ELSE CASE WHEN pao2_min IS NULL THEN NULL WHEN pao2_min<60 THEN 5 ELSE 0 END END AS oxygenation_score
 FROM raw
),
total AS (
 SELECT components.*,
   16 + coalesce(age_score,0)+coalesce(hospital_los_score,0)+coalesce(admission_location_score,0)
      +coalesce(comorbidity_score,0)+coalesce(vasoactive_score,0)+coalesce(planned_icu_score,0)
      +coalesce(admission_reason_score,0)+coalesce(surgery_status_score,0)+coalesce(surgical_site_score,0)
      +coalesce(infection_score,0)+coalesce(gcs_score,0)+coalesce(hr_score,0)+coalesce(sysbp_score,0)
      +coalesce(temp_score,0)+coalesce(bilirubin_score,0)+coalesce(creatinine_score,0)
      +coalesce(wbc_score,0)+coalesce(platelet_score,0)+coalesce(ph_score,0)+coalesce(oxygenation_score,0) AS score
 FROM components
)
SELECT stay_id, subject_id, hadm_id, intime, outtime,
  DATE_DIFF('microseconds',intime,outtime)/3600000000.0 AS icu_los_hours,
  CASE WHEN outtime IS NULL THEN NULL ELSE LEAST(24.0,GREATEST(0.0,DATE_DIFF('microseconds',intime,outtime)/3600000000.0)) END AS available_first_day_hours,
  CASE WHEN outtime IS NULL THEN NULL ELSE DATE_DIFF('microseconds',intime,outtime)/3600000000.0<24 END AS stay_shorter_than_24h,
  intime-INTERVAL '1' HOUR AS score_window_start, intime+INTERVAL '1' HOUR AS score_window_end,
  score AS saps_iii_adapted,
  1/(1+exp(-(-32.6659+7.3068*ln(score+20.5958)))) AS saps_iii_prob_global_adapted,
  1/(1+exp(-(-18.8839+4.3979*ln(score+1)))) AS saps_iii_prob_north_america_adapted,
  age_score,hospital_los_score,admission_location_score,comorbidity_score,vasoactive_score,
  planned_icu_score,admission_reason_score,surgery_status_score,surgical_site_score,infection_score,
  gcs_score,hr_score,sysbp_score,temp_score,bilirubin_score,creatinine_score,wbc_score,platelet_score,ph_score,oxygenation_score,
  age,preicu_days,prior_careunit,service,gcs_estimated,hr_max,sbp_min,temp_max,bilirubin_max,
  creatinine_max,wbc_min,platelet_min,ph_min,pao2_min,pf_min,
  mechanical_ventilation=1 AS mechanical_ventilation_proxy,
  vasoactive_preicu=1 AS vasoactive_preicu_proxy,
  planned_icu_proxy=1 AS planned_icu_proxy,
  TRUE AS diagnoses_are_posthoc_proxies,
  FALSE AS nyha_iv_available,
  FALSE AS cancer_therapy_available,
  FALSE AS pre_sedation_gcs_available,
  'saps-iii-adapted-v1' AS adaptation_version
FROM total ORDER BY stay_id;
