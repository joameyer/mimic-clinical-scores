from __future__ import annotations

from mimic_clinical_scores.common.provenance import (
    audit_dependency_graph,
    validate_official_sources,
)
from mimic_clinical_scores.scores.saps_ii.specification import load_itemid_manifest


def test_pinned_sources_and_recursive_graph(project_root) -> None:
    hashes = validate_official_sources(project_root)
    graph = audit_dependency_graph(project_root, load_itemid_manifest())

    assert len(hashes) == 14
    assert graph["concept_dependency_order"][-1].endswith("score/sapsii.sql")
    assert set(graph["raw_tables"]) == {
        "mimiciv_hosp.admissions",
        "mimiciv_hosp.diagnoses_icd",
        "mimiciv_hosp.labevents",
        "mimiciv_hosp.patients",
        "mimiciv_hosp.services",
        "mimiciv_icu.chartevents",
        "mimiciv_icu.icustays",
        "mimiciv_icu.outputevents",
    }


def test_item_manifest_records_required_audit_fields() -> None:
    manifest = load_itemid_manifest()
    assert manifest["manifest_version"] == "saps-ii-v1"
    assert manifest["entries"]
    required = {
        "source_concept", "sql_sha256", "raw_table", "item_id", "clinical_meaning",
        "required_time_context", "reason_for_retention",
    }
    assert all(required <= set(entry) for entry in manifest["entries"])
    assert all(entry["clinical_meaning"] != "unknown" for entry in manifest["entries"])

