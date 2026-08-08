"""Raw retention rules for discharge-relative hourly SOFA."""

from mimic_clinical_scores.scores.saps_ii.staging_rules import StagingRule


RULES = {
    "mimiciv_icu.icustays": StagingRule(
        "mimiciv_icu.icustays", "stay_id", "selected stay_id values only"
    ),
    "mimiciv_hosp.admissions": StagingRule(
        "mimiciv_hosp.admissions", "hadm_id",
        "cohort admissions for mortality labels; no outcome-based filtering",
    ),
    "mimiciv_icu.chartevents": StagingRule(
        "mimiciv_icu.chartevents", "stay_id",
        "audited SOFA IDs from 24h before max(intime,outtime-168h) through outtime; "
        "full selected-stay context for HR timing, weight, GCS, FiO2/SpO2, oxygen "
        "delivery, and ventilation reconstruction",
    ),
    "mimiciv_hosp.labevents": StagingRule(
        "mimiciv_hosp.labevents", "hadm_id",
        "audited SOFA lab IDs from 24h before max(intime,outtime-168h) through outtime",
    ),
    "mimiciv_icu.inputevents": StagingRule(
        "mimiciv_icu.inputevents", "stay_id",
        "four audited vasoactive IDs whose infusion overlaps the internal reverse grid",
    ),
    "mimiciv_icu.outputevents": StagingRule(
        "mimiciv_icu.outputevents", "stay_id",
        "all earlier audited urine rows for each eligible selected stay through outtime, "
        "preserving the predecessor required by LAG and nested urine lookback",
    ),
}
