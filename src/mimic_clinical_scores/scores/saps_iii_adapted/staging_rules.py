"""Raw retention policy for the adapted SAPS III admission score."""

from mimic_clinical_scores.scores.saps_ii.staging_rules import StagingRule


RULES = {
    "mimiciv_icu.icustays": StagingRule("mimiciv_icu.icustays", "stay_id", "selected stay_id values only"),
    "mimiciv_hosp.admissions": StagingRule("mimiciv_hosp.admissions", "hadm_id", "cohort admissions"),
    "mimiciv_hosp.patients": StagingRule("mimiciv_hosp.patients", "subject_id", "cohort subjects"),
    "mimiciv_hosp.services": StagingRule("mimiciv_hosp.services", "hadm_id", "all service history for cohort admissions"),
    "mimiciv_hosp.transfers": StagingRule("mimiciv_hosp.transfers", "hadm_id", "all transfer history for cohort admissions to infer pre-ICU location"),
    "mimiciv_hosp.diagnoses_icd": StagingRule("mimiciv_hosp.diagnoses_icd", "hadm_id", "all hospital diagnoses for cohort admissions; explicitly post-hoc proxy data"),
    "mimiciv_hosp.procedures_icd": StagingRule("mimiciv_hosp.procedures_icd", "hadm_id", "procedures for cohort admissions; day-resolution surgery proxy"),
    "mimiciv_icu.chartevents": StagingRule("mimiciv_icu.chartevents", "stay_id", "audited physiology/GCS/temperature-site/ventilation item IDs; inclusive [intime-1h, intime+1h], with FiO2 retained from intime-3h and support settings from intime-2h for gas-time lookback"),
    "mimiciv_hosp.labevents": StagingRule("mimiciv_hosp.labevents", "hadm_id", "audited laboratory and blood-gas specimen IDs; union of inclusive [intime-1h, intime+1h] windows for staging only; score SQL reapplies the current stay window"),
    "mimiciv_icu.inputevents": StagingRule("mimiciv_icu.inputevents", "stay_id", "audited vasoactive infusions overlapping the 24h before ICU admission"),
}
