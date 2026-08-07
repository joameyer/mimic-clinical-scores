"""Metadata-only safety preflight; never scans complete clinical event contents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mimic_clinical_scores.common.cohort import CohortInfo, inspect_cohort
from mimic_clinical_scores.common.provenance import (
    ProvenanceError,
    audit_dependency_graph,
    canonical_json_hash,
    raw_source_metadata,
    sha256_file,
    validate_official_sources,
)
from mimic_clinical_scores.scores.saps_ii.specification import (
    SAPSII_SPEC,
    load_itemid_manifest,
)


RAW_TABLE_TO_FILE = {
    "mimiciv_hosp.admissions": "hosp/admissions.csv.gz",
    "mimiciv_hosp.diagnoses_icd": "hosp/diagnoses_icd.csv.gz",
    "mimiciv_hosp.labevents": "hosp/labevents.csv.gz",
    "mimiciv_hosp.patients": "hosp/patients.csv.gz",
    "mimiciv_hosp.services": "hosp/services.csv.gz",
    "mimiciv_icu.chartevents": "icu/chartevents.csv.gz",
    "mimiciv_icu.icustays": "icu/icustays.csv.gz",
    "mimiciv_icu.outputevents": "icu/outputevents.csv.gz",
}


def code_hashes(project_root: Path) -> dict[str, str]:
    paths = sorted((project_root / "src").rglob("*.py"))
    paths.extend(sorted((project_root / "scripts").glob("*.py")))
    return {str(path.relative_to(project_root)): sha256_file(path) for path in paths}


def run_preflight(
    *,
    project_root: Path,
    mimic_root: Path,
    cohort_file: Path,
    mode: str,
    verify_raw_checksums: bool = False,
) -> dict[str, Any]:
    if mode not in {"dev100", "full"}:
        raise ValueError(f"Unknown run mode: {mode}")
    vendor_hashes = validate_official_sources(project_root)
    item_manifest = load_itemid_manifest()
    graph = audit_dependency_graph(project_root, item_manifest)
    official_manifest_paths = [
        "mimic-iv/concepts/score/sapsii.sql",
        *graph["concept_dependency_order"],
    ]
    sql_hashes = {path: vendor_hashes[path] for path in official_manifest_paths}
    discovered_files = {RAW_TABLE_TO_FILE[table] for table in graph["raw_tables"]}
    expected_files = set(SAPSII_SPEC.required_raw_tables)
    if discovered_files != expected_files:
        raise ProvenanceError(
            f"Raw dependency mismatch: discovered={sorted(discovered_files)}, "
            f"declared={sorted(expected_files)}"
        )

    cohort = inspect_cohort(cohort_file, mode=mode)
    development_manifest: dict[str, Any] | None = None
    if mode == "dev100":
        manifest_path = cohort_file.with_name("cohort_dev100_manifest.json")
        if not manifest_path.is_file():
            raise ProvenanceError(f"Development cohort manifest is missing: {manifest_path}")
        development_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_values = {
            "sample_size": cohort.unique_stay_ids,
            "ordered_selected_id_sha256": cohort.ordered_id_hash,
        }
        for key, observed in expected_values.items():
            if development_manifest.get(key) != observed:
                raise ProvenanceError(
                    f"Development manifest {key} does not match cohort: "
                    f"{development_manifest.get(key)!r} != {observed!r}"
                )
    sources: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    for relative in sorted(discovered_files):
        path = mimic_root / relative
        if not path.is_file():
            errors.append(f"missing required source {path}")
            continue
        metadata = raw_source_metadata(
            mimic_root, relative, verify_checksum=verify_raw_checksums
        )
        expected_header = SAPSII_SPEC.expected_headers[relative]
        if tuple(metadata["header"]) != expected_header:
            errors.append(
                f"header mismatch for {path}: observed={metadata['header']} "
                f"expected={list(expected_header)}"
            )
        sources[relative] = metadata
    if errors:
        raise ProvenanceError("Preflight failed: " + "; ".join(errors))

    return {
        "safe_metadata_only": True,
        "mode": mode,
        "cohort": {
            "path": str(cohort.path),
            "rows": cohort.source_row_count,
            "unique_stay_ids": cohort.unique_stay_ids,
            "fingerprint": cohort.fingerprint,
            "ordered_id_hash": cohort.ordered_id_hash,
            "development_manifest": development_manifest,
        },
        "official": {
            "release": SAPSII_SPEC.mimic_code_release,
            "commit": SAPSII_SPEC.mimic_code_commit,
            "sql_hashes": sql_hashes,
            "vendor_hashes": vendor_hashes,
            "dependency_order": graph["concept_dependency_order"],
            "raw_tables": graph["raw_tables"],
            "item_manifest_version": item_manifest["manifest_version"],
            "item_manifest_sha256": canonical_json_hash(item_manifest),
        },
        "raw_sources": sources,
        "code_hashes": code_hashes(project_root),
    }


def identity_payload(
    preflight: dict[str, Any],
    *,
    mimic_version: str,
) -> dict[str, Any]:
    return {
        "mode": preflight["mode"],
        "cohort_fingerprint": preflight["cohort"]["fingerprint"],
        "ordered_cohort_id_hash": preflight["cohort"]["ordered_id_hash"],
        "mimic_version": mimic_version,
        "mimic_code_release": preflight["official"]["release"],
        "mimic_code_commit": preflight["official"]["commit"],
        "sql_hashes": preflight["official"]["sql_hashes"],
        "item_manifest_version": preflight["official"]["item_manifest_version"],
        "item_manifest_sha256": preflight["official"]["item_manifest_sha256"],
        "raw_source_fingerprints": {
            name: metadata["source_fingerprint"]
            for name, metadata in preflight["raw_sources"].items()
        },
        "code_hash": canonical_json_hash(preflight["code_hashes"]),
    }


def cohort_from_preflight(preflight: dict[str, Any]) -> CohortInfo:
    return inspect_cohort(Path(preflight["cohort"]["path"]), mode=preflight["mode"])
