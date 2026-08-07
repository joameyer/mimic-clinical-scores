"""Pinned dependencies for the documented classic first-day SOFA adaptation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from mimic_clinical_scores.common.provenance import sha256_file
from mimic_clinical_scores.common.specification import Concept
from mimic_clinical_scores.scores.saps_ii.specification import (
    EXPECTED_HEADERS as SAPSII_HEADERS,
    MIMIC_CODE_COMMIT,
    MIMIC_CODE_RELEASE,
)


COMPONENT_COLUMNS = (
    "respiration_score",
    "coagulation_score",
    "liver_score",
    "cardiovascular_score",
    "cns_score",
    "renal_score",
)

UPSTREAM_CONCEPTS = (
    Concept("bg", "mimic-iv/concepts_duckdb/measurement/bg.sql", "mimiciv_derived.bg"),
    Concept("chemistry", "mimic-iv/concepts_duckdb/measurement/chemistry.sql", "mimiciv_derived.chemistry"),
    Concept("complete_blood_count", "mimic-iv/concepts_duckdb/measurement/complete_blood_count.sql", "mimiciv_derived.complete_blood_count"),
    Concept("enzyme", "mimic-iv/concepts_duckdb/measurement/enzyme.sql", "mimiciv_derived.enzyme"),
    Concept("gcs", "mimic-iv/concepts_duckdb/measurement/gcs.sql", "mimiciv_derived.gcs"),
    Concept("oxygen_delivery", "mimic-iv/concepts_duckdb/measurement/oxygen_delivery.sql", "mimiciv_derived.oxygen_delivery"),
    Concept("urine_output", "mimic-iv/concepts_duckdb/measurement/urine_output.sql", "mimiciv_derived.urine_output"),
    Concept("ventilator_setting", "mimic-iv/concepts_duckdb/measurement/ventilator_setting.sql", "mimiciv_derived.ventilator_setting"),
    Concept("vitalsign", "mimic-iv/concepts_duckdb/measurement/vitalsign.sql", "mimiciv_derived.vitalsign"),
    Concept("dobutamine", "mimic-iv/concepts_duckdb/medication/dobutamine.sql", "mimiciv_derived.dobutamine"),
    Concept("dopamine", "mimic-iv/concepts_duckdb/medication/dopamine.sql", "mimiciv_derived.dopamine"),
    Concept("epinephrine", "mimic-iv/concepts_duckdb/medication/epinephrine.sql", "mimiciv_derived.epinephrine"),
    Concept("norepinephrine", "mimic-iv/concepts_duckdb/medication/norepinephrine.sql", "mimiciv_derived.norepinephrine"),
    Concept("ventilation", "mimic-iv/concepts_duckdb/treatment/ventilation.sql", "mimiciv_derived.ventilation"),
)

SCORE_CONCEPT = Concept(
    "sofa_first_day_adapted",
    "sofa_first_day_adapted.sql",
    "mimiciv_derived.sofa_first_day_adapted",
)

EXPECTED_HEADERS = {
    "hosp/labevents.csv.gz": SAPSII_HEADERS["hosp/labevents.csv.gz"],
    "icu/chartevents.csv.gz": SAPSII_HEADERS["icu/chartevents.csv.gz"],
    "icu/icustays.csv.gz": SAPSII_HEADERS["icu/icustays.csv.gz"],
    "icu/inputevents.csv.gz": (
        "subject_id", "hadm_id", "stay_id", "caregiver_id", "starttime", "endtime",
        "storetime", "itemid", "amount", "amountuom", "rate", "rateuom", "orderid",
        "linkorderid", "ordercategoryname", "secondaryordercategoryname",
        "ordercomponenttypedescription", "ordercategorydescription", "patientweight",
        "totalamount", "totalamountuom", "isopenbag", "continueinnextdept",
        "statusdescription", "originalamount", "originalrate",
    ),
    "icu/outputevents.csv.gz": SAPSII_HEADERS["icu/outputevents.csv.gz"],
}


def load_itemid_manifest() -> dict[str, Any]:
    path = files("mimic_clinical_scores.scores.sofa_first_day_adapted").joinpath(
        "itemid_manifest.v1.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class SOFAFirstDayAdaptedSpecification:
    name: str = "sofa_first_day_adapted"
    mimic_code_release: str = MIMIC_CODE_RELEASE
    mimic_code_commit: str = MIMIC_CODE_COMMIT
    concepts: tuple[Concept, ...] = UPSTREAM_CONCEPTS
    score_concept: Concept = SCORE_CONCEPT
    component_columns: tuple[str, ...] = COMPONENT_COLUMNS
    required_raw_tables: tuple[str, ...] = tuple(EXPECTED_HEADERS)
    expected_headers: dict[str, tuple[str, ...]] = None  # type: ignore[assignment]
    score_columns: tuple[str, ...] = ("sofa_first_day_adapted",)
    probability_columns: tuple[str, ...] = ()
    score_table: str = "mimiciv_derived.sofa_first_day_adapted"
    provenance_label: str = (
        "MIT-LCP mimic-code v3.0.1 classic first-day SOFA with documented "
        "ventilated PaO2/FiO2 correction and equivalent narrow first-day aggregates"
    )
    item_manifest_version: str = "sofa-first-day-adapted-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected_headers", EXPECTED_HEADERS)

    def vendor_root(self, project_root: Path) -> Path:
        return project_root / "vendor" / "mimic-code"

    def score_root(self) -> Path:
        return Path(__file__).resolve().parent

    def score_vendor_root(self, project_root: Path) -> Path:
        return self.score_root()

    def item_ids(self, raw_table: str) -> frozenset[int]:
        return frozenset(
            int(item_id)
            for entry in load_itemid_manifest()["entries"]
            if entry["raw_table"] == raw_table
            for item_id in entry["item_ids"]
        )

    def full_context_item_ids(self, raw_table: str) -> frozenset[int]:
        if raw_table != "mimiciv_icu.chartevents":
            return frozenset()
        concepts = {
            "measurement/bg.sql",
            "measurement/gcs.sql",
            "measurement/oxygen_delivery.sql",
            "measurement/ventilator_setting.sql",
        }
        return frozenset(
            int(item_id)
            for entry in load_itemid_manifest()["entries"]
            if entry["raw_table"] == raw_table and entry["source_concept"] in concepts
            for item_id in entry["item_ids"]
        )

    def sql_hashes(self, project_root: Path) -> dict[str, str]:
        hashes = {
            concept.sql_relative_path: sha256_file(self.vendor_root(project_root) / concept.sql_relative_path)
            for concept in self.concepts
        }
        hashes[f"project:{self.score_concept.sql_relative_path}"] = sha256_file(
            self.score_root() / self.score_concept.sql_relative_path
        )
        return hashes

    def scores_projection_sql(self) -> str:
        from mimic_clinical_scores.scores.sofa_first_day_adapted.scoring import scores_projection_sql

        return scores_projection_sql()

    def missingness_projection_sql(self) -> str:
        from mimic_clinical_scores.scores.sofa_first_day_adapted.scoring import missingness_projection_sql

        return missingness_projection_sql()

    def staging_rules(self):
        from mimic_clinical_scores.scores.sofa_first_day_adapted.staging_rules import RULES

        return RULES


SOFA_FIRST_DAY_ADAPTED_SPEC = SOFAFirstDayAdaptedSpecification()
