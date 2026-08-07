"""Declarative SAPS II dependencies pinned to MIT-LCP mimic-code v3.0.1."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from mimic_clinical_scores.common.specification import Concept
from mimic_clinical_scores.common.provenance import sha256_file


MIMIC_CODE_RELEASE = "v3.0.1"
MIMIC_CODE_COMMIT = "c7e07560dc847e32cbb0b2890213e8e7cbd8bc7e"

UPSTREAM_CONCEPTS = (
    Concept("age", "mimic-iv/concepts_duckdb/demographics/age.sql", "mimiciv_derived.age"),
    Concept("bg", "mimic-iv/concepts_duckdb/measurement/bg.sql", "mimiciv_derived.bg"),
    Concept(
        "chemistry",
        "mimic-iv/concepts_duckdb/measurement/chemistry.sql",
        "mimiciv_derived.chemistry",
    ),
    Concept(
        "complete_blood_count",
        "mimic-iv/concepts_duckdb/measurement/complete_blood_count.sql",
        "mimiciv_derived.complete_blood_count",
    ),
    Concept("enzyme", "mimic-iv/concepts_duckdb/measurement/enzyme.sql", "mimiciv_derived.enzyme"),
    Concept("gcs", "mimic-iv/concepts_duckdb/measurement/gcs.sql", "mimiciv_derived.gcs"),
    Concept(
        "oxygen_delivery",
        "mimic-iv/concepts_duckdb/measurement/oxygen_delivery.sql",
        "mimiciv_derived.oxygen_delivery",
    ),
    Concept(
        "urine_output",
        "mimic-iv/concepts_duckdb/measurement/urine_output.sql",
        "mimiciv_derived.urine_output",
    ),
    Concept(
        "ventilator_setting",
        "mimic-iv/concepts_duckdb/measurement/ventilator_setting.sql",
        "mimiciv_derived.ventilator_setting",
    ),
    Concept(
        "vitalsign",
        "mimic-iv/concepts_duckdb/measurement/vitalsign.sql",
        "mimiciv_derived.vitalsign",
    ),
    Concept(
        "ventilation",
        "mimic-iv/concepts_duckdb/treatment/ventilation.sql",
        "mimiciv_derived.ventilation",
    ),
)

SCORE_CONCEPT = Concept(
    "sapsii", "mimic-iv/concepts_duckdb/score/sapsii.sql", "mimiciv_derived.sapsii"
)

COMPONENT_COLUMNS = (
    "age_score",
    "hr_score",
    "sysbp_score",
    "temp_score",
    "pao2fio2_score",
    "uo_score",
    "bun_score",
    "wbc_score",
    "potassium_score",
    "sodium_score",
    "bicarbonate_score",
    "bilirubin_score",
    "gcs_score",
    "comorbidity_score",
    "admissiontype_score",
)

EXPECTED_HEADERS = {
    "hosp/admissions.csv.gz": (
        "subject_id", "hadm_id", "admittime", "dischtime", "deathtime",
        "admission_type", "admit_provider_id", "admission_location",
        "discharge_location", "insurance", "language", "marital_status",
        "race", "edregtime", "edouttime", "hospital_expire_flag",
    ),
    "hosp/diagnoses_icd.csv.gz": (
        "subject_id", "hadm_id", "seq_num", "icd_code", "icd_version",
    ),
    "hosp/labevents.csv.gz": (
        "labevent_id", "subject_id", "hadm_id", "specimen_id", "itemid",
        "order_provider_id", "charttime", "storetime", "value", "valuenum",
        "valueuom", "ref_range_lower", "ref_range_upper", "flag", "priority",
        "comments",
    ),
    "hosp/patients.csv.gz": (
        "subject_id", "gender", "anchor_age", "anchor_year", "anchor_year_group", "dod",
    ),
    "hosp/services.csv.gz": (
        "subject_id", "hadm_id", "transfertime", "prev_service", "curr_service",
    ),
    "icu/chartevents.csv.gz": (
        "subject_id", "hadm_id", "stay_id", "caregiver_id", "charttime",
        "storetime", "itemid", "value", "valuenum", "valueuom", "warning",
    ),
    "icu/icustays.csv.gz": (
        "subject_id", "hadm_id", "stay_id", "first_careunit", "last_careunit",
        "intime", "outtime", "los",
    ),
    "icu/outputevents.csv.gz": (
        "subject_id", "hadm_id", "stay_id", "caregiver_id", "charttime",
        "storetime", "itemid", "value", "valueuom",
    ),
}


def load_itemid_manifest() -> dict[str, Any]:
    manifest_path = files("mimic_clinical_scores.scores.saps_ii").joinpath(
        "itemid_manifest.v1.json"
    )
    return json.loads(manifest_path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class SAPSIISpecification:
    name: str = "saps_ii"
    mimic_code_release: str = MIMIC_CODE_RELEASE
    mimic_code_commit: str = MIMIC_CODE_COMMIT
    concepts: tuple[Concept, ...] = UPSTREAM_CONCEPTS
    score_concept: Concept = SCORE_CONCEPT
    component_columns: tuple[str, ...] = COMPONENT_COLUMNS
    required_raw_tables: tuple[str, ...] = tuple(EXPECTED_HEADERS)
    expected_headers: dict[str, tuple[str, ...]] = None  # type: ignore[assignment]
    score_columns: tuple[str, ...] = ("sapsii",)
    probability_columns: tuple[str, ...] = ("sapsii_prob",)
    score_table: str = "mimiciv_derived.sapsii"
    provenance_label: str = "official MIT-LCP mimic-code SAPS II"
    item_manifest_version: str = "saps-ii-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected_headers", EXPECTED_HEADERS)

    def vendor_root(self, project_root: Path) -> Path:
        return project_root / "vendor" / "mimic-code"

    def score_vendor_root(self, project_root: Path) -> Path:
        return self.vendor_root(project_root)

    def item_ids(self, raw_table: str) -> frozenset[int]:
        return frozenset(
            int(entry["item_id"])
            for entry in load_itemid_manifest()["entries"]
            if entry["raw_table"] == raw_table
        )

    def full_context_item_ids(self, raw_table: str) -> frozenset[int]:
        if raw_table != "mimiciv_icu.chartevents":
            return frozenset()
        all_context_concepts = {
            "measurement/gcs.sql",
            "measurement/oxygen_delivery.sql",
            "measurement/ventilator_setting.sql",
        }
        return frozenset(
            int(entry["item_id"])
            for entry in load_itemid_manifest()["entries"]
            if entry["raw_table"] == raw_table
            and entry["source_concept"] in all_context_concepts
        )

    def sql_hashes(self, project_root: Path) -> dict[str, str]:
        root = self.vendor_root(project_root)
        concepts = (*self.concepts, self.score_concept)
        return {concept.sql_relative_path: sha256_file(root / concept.sql_relative_path) for concept in concepts}

    def scores_projection_sql(self) -> str:
        from mimic_clinical_scores.scores.saps_ii.scoring import scores_projection_sql

        return scores_projection_sql()

    def missingness_projection_sql(self) -> str:
        from mimic_clinical_scores.scores.saps_ii.scoring import missingness_projection_sql

        return missingness_projection_sql()

    def staging_rules(self):
        from mimic_clinical_scores.scores.saps_ii.staging_rules import RULES

        return RULES


SAPSII_SPEC = SAPSIISpecification()
