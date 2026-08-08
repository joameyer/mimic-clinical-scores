"""Declaration for the explicitly adapted SAPS III admission score."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from mimic_clinical_scores.common.provenance import canonical_json_hash, sha256_file
from mimic_clinical_scores.common.specification import Concept
from mimic_clinical_scores.scores.saps_ii.specification import EXPECTED_HEADERS as SAPSII_HEADERS


COMPONENT_COLUMNS = (
    "age_score", "hospital_los_score", "admission_location_score",
    "comorbidity_score", "vasoactive_score", "planned_icu_score",
    "admission_reason_score", "surgery_status_score", "surgical_site_score",
    "infection_score", "gcs_score", "hr_score", "sysbp_score", "temp_score",
    "bilirubin_score", "creatinine_score", "wbc_score", "platelet_score",
    "ph_score", "oxygenation_score",
)

EXPECTED_HEADERS = {
    key: SAPSII_HEADERS[key]
    for key in (
        "hosp/admissions.csv.gz", "hosp/diagnoses_icd.csv.gz",
        "hosp/labevents.csv.gz", "hosp/patients.csv.gz", "hosp/services.csv.gz",
        "icu/chartevents.csv.gz", "icu/icustays.csv.gz",
    )
}
EXPECTED_HEADERS.update({
    "hosp/transfers.csv.gz": (
        "subject_id", "hadm_id", "transfer_id", "eventtype", "careunit", "intime", "outtime",
    ),
    "hosp/procedures_icd.csv.gz": (
        "subject_id", "hadm_id", "seq_num", "chartdate", "icd_code", "icd_version",
    ),
    "icu/inputevents.csv.gz": (
        "subject_id", "hadm_id", "stay_id", "caregiver_id", "starttime", "endtime",
        "storetime", "itemid", "amount", "amountuom", "rate", "rateuom", "orderid",
        "linkorderid", "ordercategoryname", "secondaryordercategoryname",
        "ordercomponenttypedescription", "ordercategorydescription", "patientweight",
        "totalamount", "totalamountuom", "isopenbag", "continueinnextdept",
        "statusdescription", "originalamount", "originalrate",
    ),
})


def load_itemid_manifest() -> dict[str, Any]:
    path = files("mimic_clinical_scores.scores.saps_iii_adapted").joinpath("itemid_manifest.v2.json")
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class SAPSIIIAdaptedSpecification:
    name: str = "saps_iii_adapted"
    mimic_code_release: str = "not-applicable"
    mimic_code_commit: str = "not-applicable-no-MIT-LCP-SAPS-III"
    concepts: tuple[Concept, ...] = ()
    score_concept: Concept = Concept(
        "saps_iii_adapted", "saps_iii_adapted.sql", "mimiciv_derived.saps_iii_adapted"
    )
    component_columns: tuple[str, ...] = COMPONENT_COLUMNS
    required_raw_tables: tuple[str, ...] = tuple(EXPECTED_HEADERS)
    expected_headers: dict[str, tuple[str, ...]] = None  # type: ignore[assignment]
    score_columns: tuple[str, ...] = ("saps_iii_proxy_total_unvalidated",)
    probability_columns: tuple[str, ...] = (
        "saps_iii_prob_global_proxy_unvalidated",
        "saps_iii_prob_north_america_proxy_unvalidated",
    )
    score_table: str = "mimiciv_derived.saps_iii_adapted"
    provenance_label: str = "Unvalidated SAPS III 2005 proxy sensitivity calculation for MIMIC-IV"
    item_manifest_version: str = "saps-iii-adapted-v2"

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected_headers", EXPECTED_HEADERS)

    def vendor_root(self, project_root: Path) -> Path:
        return Path(__file__).resolve().parent

    def score_vendor_root(self, project_root: Path) -> Path:
        return self.vendor_root(project_root)

    def item_ids(self, raw_table: str) -> frozenset[int]:
        return frozenset(
            int(entry["item_id"])
            for entry in load_itemid_manifest()["entries"]
            if entry["raw_table"] == raw_table
        )

    def full_context_item_ids(self, raw_table: str) -> frozenset[int]:
        return frozenset()

    def sql_hashes(self, project_root: Path) -> dict[str, str]:
        path = self.vendor_root(project_root) / self.score_concept.sql_relative_path
        return {f"project:{self.score_concept.sql_relative_path}": sha256_file(path)}

    def source_identity(self, project_root: Path) -> str:
        source_manifest = json.loads(
            (project_root / "config" / "saps_iii_sources.json").read_text(encoding="utf-8")
        )
        return canonical_json_hash(source_manifest)

    def scores_projection_sql(self) -> str:
        from mimic_clinical_scores.scores.saps_iii_adapted.scoring import scores_projection_sql

        return scores_projection_sql()

    def missingness_projection_sql(self) -> str:
        from mimic_clinical_scores.scores.saps_iii_adapted.scoring import missingness_projection_sql

        return missingness_projection_sql()

    def staging_rules(self):
        from mimic_clinical_scores.scores.saps_iii_adapted.staging_rules import RULES

        return RULES


SAPSIII_ADAPTED_SPEC = SAPSIIIAdaptedSpecification()
