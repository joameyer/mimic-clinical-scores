"""Raw retention rules for the adapted classic first-day SOFA."""

from mimic_clinical_scores.scores.saps_ii.staging_rules import StagingRule


RULES = {
    "mimiciv_icu.icustays": StagingRule(
        "mimiciv_icu.icustays", "stay_id", "selected stay_id values only"
    ),
    "mimiciv_icu.chartevents": StagingRule(
        "mimiciv_icu.chartevents", "stay_id",
        "audited SOFA item IDs; vitals inclusive [intime-6h,intime+24h]; full selected-stay context for GCS, FiO2/SpO2, oxygen delivery, and ventilation episodes",
    ),
    "mimiciv_hosp.labevents": StagingRule(
        "mimiciv_hosp.labevents", "subject_id",
        "audited official blood-gas/CBC/chemistry/enzyme item IDs; inclusive [intime-6h,intime+24h] for any selected stay of the subject",
    ),
    "mimiciv_icu.inputevents": StagingRule(
        "mimiciv_icu.inputevents", "stay_id",
        "four audited vasoactive item IDs with starttime inclusive [intime-6h,intime+24h]",
    ),
    "mimiciv_icu.outputevents": StagingRule(
        "mimiciv_icu.outputevents", "stay_id",
        "audited urine-output item IDs inclusive [intime,intime+24h]",
    ),
}
