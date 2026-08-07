"""Raw retention rules for ICU-relative hourly SOFA through day 14."""

from mimic_clinical_scores.scores.saps_ii.staging_rules import StagingRule


RULES = {
    "mimiciv_icu.icustays": StagingRule(
        "mimiciv_icu.icustays", "stay_id", "selected stay_id values only"
    ),
    "mimiciv_icu.chartevents": StagingRule(
        "mimiciv_icu.chartevents", "stay_id",
        "audited SOFA item IDs; ordinary measurements from intime-24h through "
        "min(outtime,intime+336h); complete selected-stay context for heart-rate timing, "
        "weight, GCS, FiO2/SpO2, oxygen delivery, and ventilation reconstruction",
    ),
    "mimiciv_hosp.labevents": StagingRule(
        "mimiciv_hosp.labevents", "hadm_id",
        "audited blood-gas/CBC/chemistry/enzyme item IDs from intime-24h through "
        "min(outtime,intime+336h) for each selected ICU stay",
    ),
    "mimiciv_icu.inputevents": StagingRule(
        "mimiciv_icu.inputevents", "stay_id",
        "four audited vasoactive item IDs whose infusion interval overlaps intime-24h "
        "through min(outtime,intime+336h)",
    ),
    "mimiciv_icu.outputevents": StagingRule(
        "mimiciv_icu.outputevents", "stay_id",
        "audited urine-output item IDs from intime-48h through "
        "min(outtime,intime+336h), preserving nested 24h urine lookback",
    ),
}
