"""Pinned declaration for ICU-relative hourly SOFA through day 14."""

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


COMPONENT_COLUMNS = tuple(
    f"{name}_24hours_raw"
    for name in ("respiration", "coagulation", "liver", "cardiovascular", "cns", "renal")
)

UPSTREAM_CONCEPTS = (
    Concept("bg", "mimic-iv/concepts_duckdb/measurement/bg.sql", "mimiciv_derived.bg"),
    Concept("chemistry", "mimic-iv/concepts_duckdb/measurement/chemistry.sql", "mimiciv_derived.chemistry"),
    Concept("complete_blood_count", "mimic-iv/concepts_duckdb/measurement/complete_blood_count.sql", "mimiciv_derived.complete_blood_count"),
    Concept("enzyme", "mimic-iv/concepts_duckdb/measurement/enzyme.sql", "mimiciv_derived.enzyme"),
    Concept("gcs", "mimic-iv/concepts_duckdb/measurement/gcs.sql", "mimiciv_derived.gcs"),
    Concept("oxygen_delivery", "mimic-iv/concepts_duckdb/measurement/oxygen_delivery.sql", "mimiciv_derived.oxygen_delivery"),
    Concept("urine_output", "mimic-iv/concepts_duckdb/measurement/urine_output.sql", "mimiciv_derived.urine_output"),
    Concept("weight_durations", "mimic-iv/concepts_duckdb/demographics/weight_durations.sql", "mimiciv_derived.weight_durations"),
    Concept("urine_output_rate", "mimic-iv/concepts_duckdb/measurement/urine_output_rate.sql", "mimiciv_derived.urine_output_rate"),
    Concept("ventilator_setting", "mimic-iv/concepts_duckdb/measurement/ventilator_setting.sql", "mimiciv_derived.ventilator_setting"),
    Concept("vitalsign", "mimic-iv/concepts_duckdb/measurement/vitalsign.sql", "mimiciv_derived.vitalsign"),
    Concept("dobutamine", "mimic-iv/concepts_duckdb/medication/dobutamine.sql", "mimiciv_derived.dobutamine"),
    Concept("dopamine", "mimic-iv/concepts_duckdb/medication/dopamine.sql", "mimiciv_derived.dopamine"),
    Concept("epinephrine", "mimic-iv/concepts_duckdb/medication/epinephrine.sql", "mimiciv_derived.epinephrine"),
    Concept("norepinephrine", "mimic-iv/concepts_duckdb/medication/norepinephrine.sql", "mimiciv_derived.norepinephrine"),
    Concept("ventilation", "mimic-iv/concepts_duckdb/treatment/ventilation.sql", "mimiciv_derived.ventilation"),
)

SCORE_CONCEPT = Concept(
    "sofa_hourly_14d", "sofa_hourly_14d.sql", "mimiciv_derived.sofa_hourly_14d"
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
    package = files("mimic_clinical_scores.scores.sofa_hourly_14d")
    overlay = json.loads(
        package.joinpath("itemid_manifest.v2.json").read_text(encoding="utf-8")
    )
    base = json.loads(package.joinpath(overlay["base_manifest"]).read_text(encoding="utf-8"))
    entries = [dict(entry) for entry in base["entries"]]
    for override in overlay["entry_overrides"]:
        matches = [entry for entry in entries if entry["source_concept"] == override["source_concept"]]
        if len(matches) != 1:
            raise ValueError(f"Manifest override must match once: {override['source_concept']}")
        matches[0].update({key: value for key, value in override.items() if key != "source_concept"})
    return {**base, "manifest_version": overlay["manifest_version"], "entries": entries}


@dataclass(frozen=True)
class SOFAHourly14dSpecification:
    name: str = "sofa_hourly_14d"
    mimic_code_release: str = MIMIC_CODE_RELEASE
    mimic_code_commit: str = MIMIC_CODE_COMMIT
    concepts: tuple[Concept, ...] = UPSTREAM_CONCEPTS
    score_concept: Concept = SCORE_CONCEPT
    component_columns: tuple[str, ...] = COMPONENT_COLUMNS
    required_raw_tables: tuple[str, ...] = tuple(EXPECTED_HEADERS)
    expected_headers: dict[str, tuple[str, ...]] = None  # type: ignore[assignment]
    score_columns: tuple[str, ...] = ("sofa_24hours",)
    probability_columns: tuple[str, ...] = ()
    score_table: str = "mimiciv_derived.sofa_hourly_14d"
    provenance_label: str = (
        "MIT-LCP mimic-code v3.0.1 hourly SOFA adapted to an ICU-intime-relative "
        "grid with exact elapsed 24-hour windows, at-least-one-hour vasoactive episodes, "
        "and auditable composite inputs"
    )
    item_manifest_version: str = "sofa-hourly-14d-v2"
    output_granularity: str = "stay_hour"
    primary_key_columns: tuple[str, ...] = ("stay_id", "hr")

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
        full = {
            "measurement/bg.sql", "measurement/gcs.sql", "measurement/oxygen_delivery.sql",
            "measurement/ventilator_setting.sql", "demographics/weight_durations.sql",
            "measurement/urine_output_rate.sql",
        }
        return frozenset(
            int(item_id)
            for entry in load_itemid_manifest()["entries"]
            if entry["raw_table"] == raw_table and entry["source_concept"] in full
            for item_id in entry["item_ids"]
        )

    def sql_hashes(self, project_root: Path) -> dict[str, str]:
        hashes = {
            concept.sql_relative_path: sha256_file(
                self.vendor_root(project_root) / concept.sql_relative_path
            )
            for concept in self.concepts
        }
        hashes["upstream:mimic-iv/concepts_duckdb/score/sofa.sql"] = (
            "5af9c75bdaeb9342138a0fbc8cbef33b132508689e3ac492ab574af1c7ff05b0"
        )
        hashes[f"project:{self.score_concept.sql_relative_path}"] = sha256_file(
            self.score_root() / self.score_concept.sql_relative_path
        )
        return hashes

    def scores_projection_sql(self) -> str:
        from mimic_clinical_scores.scores.sofa_hourly_14d.scoring import scores_projection_sql
        return scores_projection_sql()

    def missingness_projection_sql(self) -> str:
        from mimic_clinical_scores.scores.sofa_hourly_14d.scoring import missingness_projection_sql
        return missingness_projection_sql()

    def staging_rules(self):
        from mimic_clinical_scores.scores.sofa_hourly_14d.staging_rules import RULES
        return RULES


SOFA_HOURLY_14D_SPEC = SOFAHourly14dSpecification()
