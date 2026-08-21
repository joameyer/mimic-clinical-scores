"""Raw retention rules for complete-stay eight-hour SOFA."""

from mimic_clinical_scores.scores.saps_ii.staging_rules import StagingRule


RULES = {
    "mimiciv_icu.icustays": StagingRule(
        "mimiciv_icu.icustays", "stay_id", "selected stay_id values only"
    ),
    "mimiciv_icu.chartevents": StagingRule(
        "mimiciv_icu.chartevents", "stay_id",
        "audited SOFA item IDs; ordinary measurements from intime-24h through "
        "outtime for eligible stays; complete selected-stay context for heart-rate "
        "timing, weight, GCS, FiO2/SpO2, oxygen delivery, and ventilation reconstruction",
    ),
    "mimiciv_hosp.labevents": StagingRule(
        "mimiciv_hosp.labevents", "hadm_id",
        "audited blood-gas/CBC/chemistry/enzyme item IDs from intime-24h through "
        "outtime for each eligible selected ICU stay",
    ),
    "mimiciv_icu.inputevents": StagingRule(
        "mimiciv_icu.inputevents", "stay_id",
        "four audited vasoactive item IDs whose infusion interval overlaps intime-24h "
        "through outtime for each eligible selected stay",
    ),
    "mimiciv_icu.outputevents": StagingRule(
        "mimiciv_icu.outputevents", "stay_id",
        "all earlier audited urine-output rows for each eligible selected stay through "
        "outtime, preserving the predecessor required by LAG and the nested 24h lookback",
    ),
}
