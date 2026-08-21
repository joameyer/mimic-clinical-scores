"""Pinned declaration for complete-stay SOFA in eight-hour blocks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mimic_clinical_scores.common.provenance import sha256_file
from mimic_clinical_scores.common.specification import Concept
from mimic_clinical_scores.scores.saps_ii.specification import (
    MIMIC_CODE_COMMIT,
    MIMIC_CODE_RELEASE,
)
from mimic_clinical_scores.scores.sofa_hourly_14d.specification import (
    COMPONENT_COLUMNS,
    EXPECTED_HEADERS,
    UPSTREAM_CONCEPTS,
    load_itemid_manifest as load_hourly_itemid_manifest,
)


SCORE_CONCEPT = Concept(
    "sofa_8h_all_stay", "sofa_8h_all_stay.sql", "mimiciv_derived.sofa_8h_all_stay"
)


def load_itemid_manifest() -> dict[str, Any]:
    """Reuse the audited SOFA dependency set with complete-stay time contexts."""

    base = load_hourly_itemid_manifest()
    entries = [dict(entry) for entry in base["entries"]]
    full_context = {
        "measurement/bg.sql",
        "measurement/gcs.sql",
        "measurement/oxygen_delivery.sql",
        "measurement/ventilator_setting.sql",
        "demographics/weight_durations.sql",
        "measurement/urine_output_rate.sql",
    }
    for entry in entries:
        raw_table = entry["raw_table"]
        concept = entry["source_concept"]
        if raw_table == "mimiciv_icu.outputevents":
            entry["required_time_context"] = "all earlier selected-stay rows through outtime"
        elif raw_table == "mimiciv_icu.inputevents":
            entry["required_time_context"] = "episodes overlapping intime-24h through outtime"
        elif raw_table == "mimiciv_icu.chartevents" and concept in full_context:
            entry["required_time_context"] = "full selected-stay context"
        else:
            entry["required_time_context"] = "intime-24h through outtime"
    return {
        **base,
        "manifest_version": "sofa-8h-all-stay-v1",
        "entries": entries,
    }


@dataclass(frozen=True)
class SOFA8hAllStaySpecification:
    name: str = "sofa_8h_all_stay"
    mimic_code_release: str = MIMIC_CODE_RELEASE
    mimic_code_commit: str = MIMIC_CODE_COMMIT
    concepts: tuple[Concept, ...] = UPSTREAM_CONCEPTS
    score_concept: Concept = SCORE_CONCEPT
    component_columns: tuple[str, ...] = COMPONENT_COLUMNS
    required_raw_tables: tuple[str, ...] = tuple(EXPECTED_HEADERS)
    expected_headers: dict[str, tuple[str, ...]] = None  # type: ignore[assignment]
    score_columns: tuple[str, ...] = ("sofa_24hours",)
    probability_columns: tuple[str, ...] = ()
    score_table: str = "mimiciv_derived.sofa_8h_all_stay"
    provenance_label: str = (
        "MIT-LCP mimic-code v3.0.1 hourly SOFA adapted to non-overlapping "
        "ICU-relative eight-hour blocks across the complete recorded stay with "
        "exact trailing 24-hour windows and auditable composite inputs"
    )
    item_manifest_version: str = "sofa-8h-all-stay-v1"
    output_granularity: str = "stay_block"
    primary_key_columns: tuple[str, ...] = ("stay_id", "hr")
    requires_outtime: bool = True
    hour_index_column: str = "block_index"
    maximum_hour_index: int | None = None

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
        return frozenset(
            int(item_id)
            for entry in load_itemid_manifest()["entries"]
            if entry["raw_table"] == raw_table
            and entry["required_time_context"] == "full selected-stay context"
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
        from mimic_clinical_scores.scores.sofa_8h_all_stay.scoring import (
            scores_projection_sql,
        )

        return scores_projection_sql()

    def missingness_projection_sql(self) -> str:
        from mimic_clinical_scores.scores.sofa_8h_all_stay.scoring import (
            missingness_projection_sql,
        )

        return missingness_projection_sql()

    def staging_rules(self):
        from mimic_clinical_scores.scores.sofa_8h_all_stay.staging_rules import RULES

        return RULES


SOFA_8H_ALL_STAY_SPEC = SOFA8hAllStaySpecification()
