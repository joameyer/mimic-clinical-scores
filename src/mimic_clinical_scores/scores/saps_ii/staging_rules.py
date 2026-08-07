"""SAPS II staging policy and exact boundary semantics."""

from __future__ import annotations

from dataclasses import dataclass

from mimic_clinical_scores.scores.saps_ii.specification import SAPSII_SPEC


@dataclass(frozen=True)
class StagingRule:
    raw_table: str
    cohort_key: str
    filter_summary: str


RULES = {
    "mimiciv_icu.icustays": StagingRule(
        "mimiciv_icu.icustays", "stay_id", "selected stay_id values only"
    ),
    "mimiciv_hosp.admissions": StagingRule(
        "mimiciv_hosp.admissions", "hadm_id", "cohort hadm_id values"
    ),
    "mimiciv_hosp.patients": StagingRule(
        "mimiciv_hosp.patients", "subject_id", "cohort subject_id values"
    ),
    "mimiciv_hosp.services": StagingRule(
        "mimiciv_hosp.services", "hadm_id", "all service history for cohort admissions"
    ),
    "mimiciv_hosp.diagnoses_icd": StagingRule(
        "mimiciv_hosp.diagnoses_icd", "hadm_id", "all diagnoses for cohort admissions"
    ),
    "mimiciv_icu.chartevents": StagingRule(
        "mimiciv_icu.chartevents",
        "stay_id",
        "selected stays and audited item IDs; all GCS/ventilation context, the official "
        "two-hour SpO2 lookback, and otherwise charttime > intime through intime+24h",
    ),
    "mimiciv_hosp.labevents": StagingRule(
        "mimiciv_hosp.labevents",
        "hadm_id",
        "cohort admissions, audited item IDs, charttime > intime and <= intime+24h",
    ),
    "mimiciv_icu.outputevents": StagingRule(
        "mimiciv_icu.outputevents",
        "stay_id",
        "selected stays, audited item IDs, charttime > intime and <= intime+24h",
    ),
}


CHARTEVENT_ITEM_IDS = SAPSII_SPEC.item_ids("mimiciv_icu.chartevents")
CHARTEVENT_FULL_CONTEXT_ITEM_IDS = SAPSII_SPEC.full_context_item_ids(
    "mimiciv_icu.chartevents"
)
LABEVENT_ITEM_IDS = SAPSII_SPEC.item_ids("mimiciv_hosp.labevents")
OUTPUTEVENT_ITEM_IDS = SAPSII_SPEC.item_ids("mimiciv_icu.outputevents")
