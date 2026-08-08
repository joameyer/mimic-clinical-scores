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
from mimic_clinical_scores.common.specification import ScoreSpecification


RAW_TABLE_TO_FILE = {
    "mimiciv_hosp.admissions": "hosp/admissions.csv.gz",
    "mimiciv_hosp.diagnoses_icd": "hosp/diagnoses_icd.csv.gz",
    "mimiciv_hosp.labevents": "hosp/labevents.csv.gz",
    "mimiciv_hosp.patients": "hosp/patients.csv.gz",
    "mimiciv_hosp.services": "hosp/services.csv.gz",
    "mimiciv_hosp.transfers": "hosp/transfers.csv.gz",
    "mimiciv_hosp.procedures_icd": "hosp/procedures_icd.csv.gz",
    "mimiciv_icu.chartevents": "icu/chartevents.csv.gz",
    "mimiciv_icu.icustays": "icu/icustays.csv.gz",
    "mimiciv_icu.inputevents": "icu/inputevents.csv.gz",
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
    specification: ScoreSpecification = SAPSII_SPEC,
) -> dict[str, Any]:
    if mode not in {"dev100", "full"}:
        raise ValueError(f"Unknown run mode: {mode}")
    if specification.name == "saps_ii":
        vendor_hashes = validate_official_sources(project_root)
        item_manifest = load_itemid_manifest()
        graph = audit_dependency_graph(project_root, item_manifest)
        official_manifest_paths = ["mimic-iv/concepts/score/sapsii.sql", *graph["concept_dependency_order"]]
        sql_hashes = {path: vendor_hashes[path] for path in official_manifest_paths}
        discovered_files = {RAW_TABLE_TO_FILE[table] for table in graph["raw_tables"]}
        dependency_order = graph["concept_dependency_order"]
        raw_tables = graph["raw_tables"]
        source_manifest = None
    elif specification.name == "saps_iii_adapted":
        from mimic_clinical_scores.common.provenance import extract_item_ids
        from mimic_clinical_scores.scores.saps_iii_adapted.specification import load_itemid_manifest as load_saps3_items

        item_manifest = load_saps3_items()
        sql_hashes = dict(specification.sql_hashes(project_root))
        vendor_hashes = {}
        discovered_files = set(specification.required_raw_tables)
        dependency_order = [specification.score_concept.sql_relative_path]
        raw_tables = sorted(
            table for table, relative in RAW_TABLE_TO_FILE.items() if relative in discovered_files
        )
        source_path = project_root / "config" / "saps_iii_sources.json"
        if not source_path.is_file():
            raise ProvenanceError(f"SAPS III source manifest is missing: {source_path}")
        source_manifest = json.loads(source_path.read_text(encoding="utf-8"))
        sql_path = specification.vendor_root(project_root) / specification.score_concept.sql_relative_path
        observed_ids = extract_item_ids(sql_path.read_text(encoding="utf-8"))
        declared_ids = {int(entry["item_id"]) for entry in item_manifest["entries"]}
        if observed_ids != declared_ids:
            raise ProvenanceError(
                f"Adapted SAPS III item-ID audit failed: SQL={sorted(observed_ids)} "
                f"manifest={sorted(declared_ids)}"
            )
    elif specification.name in {
        "sofa_first_day_adapted", "sofa_hourly_14d", "sofa_hourly_reverse_7d"
    }:
        from mimic_clinical_scores.common.provenance import extract_item_ids, extract_table_references

        if specification.name == "sofa_first_day_adapted":
            from mimic_clinical_scores.scores.sofa_first_day_adapted.specification import load_itemid_manifest as load_sofa_items
            source_filename = "sofa_sources.json"
            expected_upstream_key = "official_first_day_sofa"
            expected_upstream_hash = "02736bd4faf9885fed67de777ec85852b50e93ac1ddc03bd6e5039216ce3d86e"
        elif specification.name == "sofa_hourly_14d":
            from mimic_clinical_scores.scores.sofa_hourly_14d.specification import load_itemid_manifest as load_sofa_items
            source_filename = "sofa_hourly_14d_sources.json"
            expected_upstream_key = "official_hourly_sofa"
            expected_upstream_hash = "5af9c75bdaeb9342138a0fbc8cbef33b132508689e3ac492ab574af1c7ff05b0"
        else:
            from mimic_clinical_scores.scores.sofa_hourly_reverse_7d.specification import load_itemid_manifest as load_sofa_items
            source_filename = "sofa_hourly_reverse_7d_sources.json"
            expected_upstream_key = "official_hourly_sofa"
            expected_upstream_hash = "5af9c75bdaeb9342138a0fbc8cbef33b132508689e3ac492ab574af1c7ff05b0"

        item_manifest = load_sofa_items()
        sql_hashes = dict(specification.sql_hashes(project_root))
        vendor_hashes = validate_official_sources(project_root)
        dependency_order = [
            *(concept.sql_relative_path for concept in specification.concepts),
            f"project:{specification.score_concept.sql_relative_path}",
        ]
        raw_tables = sorted(
            table for table, relative in RAW_TABLE_TO_FILE.items()
            if relative in specification.required_raw_tables
        )
        source_path = project_root / "config" / source_filename
        if not source_path.is_file():
            raise ProvenanceError(f"SOFA source manifest is missing: {source_path}")
        source_manifest = json.loads(source_path.read_text(encoding="utf-8"))
        available_derived: set[str] = set()
        graph_raw_tables: set[str] = set()
        graph_errors: list[str] = []
        graph_files = [
            *(
                (concept, specification.vendor_root(project_root) / concept.sql_relative_path)
                for concept in specification.concepts
            ),
            (
                specification.score_concept,
                specification.score_vendor_root(project_root)
                / specification.score_concept.sql_relative_path,
            ),
        ]
        for concept, sql_path in graph_files:
            references = extract_table_references(sql_path.read_text(encoding="utf-8"))
            own_table = concept.output_table.lower()
            for table in references - {own_table}:
                if table.startswith("mimiciv_derived.") and table not in available_derived:
                    graph_errors.append(
                        f"{concept.name} references unavailable derived dependency {table}"
                    )
                elif table.startswith(("mimiciv_hosp.", "mimiciv_icu.")):
                    graph_raw_tables.add(table)
            available_derived.add(own_table)
        if graph_errors:
            raise ProvenanceError("SOFA dependency-order audit failed: " + "; ".join(graph_errors))
        raw_tables = sorted(graph_raw_tables)
        discovered_files = {RAW_TABLE_TO_FILE[table] for table in raw_tables}
        declared_by_concept: dict[str, set[int]] = {}
        for entry in item_manifest["entries"]:
            declared_by_concept.setdefault(str(entry["source_concept"]), set()).update(
                int(value) for value in entry["item_ids"]
            )
        prefix = "mimic-iv/concepts_duckdb/"
        observed_by_concept: dict[str, set[int]] = {}
        observed_hash_by_concept: dict[str, str] = {}
        for concept in specification.concepts:
            short_name = concept.sql_relative_path.removeprefix(prefix)
            sql_path = specification.vendor_root(project_root) / concept.sql_relative_path
            observed_by_concept[short_name] = extract_item_ids(sql_path.read_text(encoding="utf-8"))
            observed_hash_by_concept[short_name] = sha256_file(sql_path)
        observed_by_concept = {key: value for key, value in observed_by_concept.items() if value}
        if observed_by_concept != declared_by_concept:
            raise ProvenanceError(
                "Adapted SOFA item-ID audit failed: "
                f"SQL={{{', '.join(f'{key}: {sorted(value)}' for key, value in observed_by_concept.items())}}}; "
                f"manifest={{{', '.join(f'{key}: {sorted(value)}' for key, value in declared_by_concept.items())}}}"
            )
        for entry in item_manifest["entries"]:
            concept_name = str(entry["source_concept"])
            if observed_hash_by_concept.get(concept_name) != entry["sql_sha256"]:
                raise ProvenanceError(
                    f"SOFA item manifest SQL hash mismatch for {concept_name}: "
                    f"{entry['sql_sha256']} != {observed_hash_by_concept.get(concept_name)}"
                )
        observed_upstream_hash = source_manifest[expected_upstream_key]["sha256"]
        if observed_upstream_hash != expected_upstream_hash:
            raise ProvenanceError(
                f"Unexpected pinned upstream SOFA hash: {observed_upstream_hash}"
            )
        upstream_relative = str(source_manifest[expected_upstream_key]["path"])
        vendored_upstream_hash = vendor_hashes.get(upstream_relative)
        if vendored_upstream_hash != expected_upstream_hash:
            raise ProvenanceError(
                "Pinned upstream SOFA source is not vendored at its declared hash: "
                f"{upstream_relative}={vendored_upstream_hash!r}, "
                f"expected={expected_upstream_hash}"
            )
    else:
        raise ProvenanceError(f"No preflight implementation for score {specification.name}")
    expected_files = set(specification.required_raw_tables)
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
        expected_header = specification.expected_headers[relative]
        if tuple(metadata["header"]) != expected_header:
            errors.append(
                f"header mismatch for {path}: observed={metadata['header']} "
                f"expected={list(expected_header)}"
            )
        sources[relative] = metadata
    if errors:
        raise ProvenanceError("Preflight failed: " + "; ".join(errors))

    return {
        "score_name": specification.name,
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
            "release": specification.mimic_code_release,
            "commit": specification.mimic_code_commit,
            "provenance_label": specification.provenance_label,
            "adaptation_source_manifest": source_manifest,
            "source_manifest_sha256": canonical_json_hash(
                source_manifest if source_manifest is not None else {
                    "release": specification.mimic_code_release,
                    "commit": specification.mimic_code_commit,
                }
            ),
            "sql_hashes": sql_hashes,
            "vendor_hashes": vendor_hashes,
            "dependency_order": dependency_order,
            "raw_tables": raw_tables,
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
        "score_name": preflight["score_name"],
        "mode": preflight["mode"],
        "cohort_fingerprint": preflight["cohort"]["fingerprint"],
        "ordered_cohort_id_hash": preflight["cohort"]["ordered_id_hash"],
        "mimic_version": mimic_version,
        "mimic_code_release": preflight["official"]["release"],
        "mimic_code_commit": preflight["official"]["commit"],
        "source_manifest_sha256": preflight["official"]["source_manifest_sha256"],
        "dependency_order": preflight["official"]["dependency_order"],
        "sql_hashes": preflight["official"]["sql_hashes"],
        "vendor_hashes": preflight["official"]["vendor_hashes"],
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
