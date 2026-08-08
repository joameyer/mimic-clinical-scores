from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mimic_clinical_scores.common.concepts import build_concepts
from mimic_clinical_scores.common.cohort import inspect_cohort
from mimic_clinical_scores.common.duckdb import DuckDBSettings, connect, ensure_run_identity
from mimic_clinical_scores.common.export import ExportError, export_all, validate_exports
from mimic_clinical_scores.common.preflight import identity_payload, run_preflight
from mimic_clinical_scores.common.reference import build_unfiltered_reference
from mimic_clinical_scores.common.staging import build_staging, staging_statistics
from mimic_clinical_scores.common.validation import assert_reference_equivalent
from mimic_clinical_scores.scores.saps_ii.specification import COMPONENT_COLUMNS, SAPSII_SPEC


@pytest.fixture(scope="session")
def computed_pipeline(tmp_path_factory, project_root, synthetic_mimic):
    temporary = tmp_path_factory.mktemp("computed_pipeline")
    mimic_root = synthetic_mimic["root"]
    cohort_file = synthetic_mimic["cohort_file"]
    preflight = run_preflight(
        project_root=project_root,
        mimic_root=mimic_root,
        cohort_file=cohort_file,
        mode="full",
    )
    settings = DuckDBSettings(
        database=temporary / "optimized.duckdb",
        threads=2,
        memory_limit="1GB",
        spill_directory=temporary / "spill",
    )
    optimized = connect(settings)
    identity = ensure_run_identity(optimized, identity_payload(preflight, mimic_version="3.1"))
    cohort = inspect_cohort(cohort_file, mode="full")
    build_staging(
        optimized,
        mimic_root=mimic_root,
        cohort=cohort,
        identity_hash=identity,
        raw_metadata=preflight["raw_sources"],
        profile_directory=temporary / "profiles",
    )
    build_concepts(
        optimized,
        concepts=(*SAPSII_SPEC.concepts, SAPSII_SPEC.score_concept),
        vendor_root=SAPSII_SPEC.vendor_root(project_root),
        identity_hash=identity,
    )

    reference = duckdb.connect()
    build_unfiltered_reference(
        reference,
        mimic_root=mimic_root,
        vendor_root=SAPSII_SPEC.vendor_root(project_root),
    )
    output = temporary / "outputs"
    export_all(
        optimized,
        output_directory=output,
        identity_hash=identity,
        mode="full",
        mimic_version="3.1",
        cohort_manifest=None,
        preflight=preflight,
        runtime=settings,
        command_line=["pytest", "synthetic"],
    )
    yield {
        "optimized": optimized,
        "reference": reference,
        "identity": identity,
        "output": output,
        "settings": settings,
        "preflight": preflight,
    }
    reference.close()
    optimized.close()


def test_optimized_staging_is_null_safe_exact_reference(computed_pipeline, synthetic_mimic) -> None:
    assert_reference_equivalent(
        computed_pipeline["reference"],
        computed_pipeline["optimized"],
        synthetic_mimic["stay_ids"],
    )


def test_source_rows_are_audited_during_each_single_scan(
    computed_pipeline, synthetic_mimic
) -> None:
    statistics = staging_statistics(computed_pipeline["optimized"])
    source_rows = synthetic_mimic["rows"]
    table_to_source = {
        "mimiciv_hosp.admissions": "hosp/admissions.csv.gz",
        "mimiciv_hosp.diagnoses_icd": "hosp/diagnoses_icd.csv.gz",
        "mimiciv_hosp.labevents": "hosp/labevents.csv.gz",
        "mimiciv_hosp.patients": "hosp/patients.csv.gz",
        "mimiciv_hosp.services": "hosp/services.csv.gz",
        "mimiciv_icu.chartevents": "icu/chartevents.csv.gz",
        "mimiciv_icu.icustays": "icu/icustays.csv.gz",
        "mimiciv_icu.outputevents": "icu/outputevents.csv.gz",
    }
    assert set(statistics) == set(table_to_source)
    for table, relative in table_to_source.items():
        assert statistics[table]["source_row_count"] == len(source_rows[relative])
        assert statistics[table]["retained_row_count"] <= len(source_rows[relative])


def test_official_worst_values_components_and_coalesce_semantics(computed_pipeline) -> None:
    connection = computed_pipeline["optimized"]
    columns = ", ".join((*COMPONENT_COLUMNS, "sapsii"))
    values = connection.execute(
        f"SELECT {columns} FROM mimiciv_derived.sapsii WHERE stay_id = 1001"
    ).fetchone()
    assert values == (7, 11, 13, 3, 9, 4, 10, 12, 3, 1, 6, 9, 5, 17, 6, 116)

    sparse = connection.execute(
        "SELECT sapsii, age_score, admissiontype_score FROM mimiciv_derived.sapsii WHERE stay_id = 1006"
    ).fetchone()
    assert sparse[0] is not None
    assert sparse[1:] == (7, 6)
    missing_physiology = connection.execute(
        """
        SELECT hr_score, sysbp_score, temp_score, pao2fio2_score, uo_score,
               bun_score, wbc_score, potassium_score, sodium_score,
               bicarbonate_score, bilirubin_score, gcs_score
        FROM mimiciv_derived.sapsii WHERE stay_id = 1006
        """
    ).fetchone()
    assert missing_physiology == (None,) * 12


def test_boundaries_lookbacks_short_stays_and_context(computed_pipeline) -> None:
    connection = computed_pipeline["optimized"]
    heart_rates = connection.execute(
        """
        SELECT valuenum FROM mimiciv_icu.chartevents
        WHERE stay_id = 1001 AND itemid = 220045 ORDER BY charttime
        """
    ).fetchall()
    assert heart_rates == [(35.0,), (170.0,), (80.0,)]

    assert connection.execute(
        "SELECT COUNT(*) FROM mimiciv_hosp.labevents WHERE itemid = 51006 AND charttime = TIMESTAMP '2100-01-01 00:00:00'"
    ).fetchone()[0] == 0
    assert connection.execute(
        "SELECT COUNT(*) FROM mimiciv_hosp.labevents WHERE itemid = 50983 AND charttime = TIMESTAMP '2100-01-02 00:00:00'"
    ).fetchone()[0] == 1
    assert connection.execute(
        "SELECT COUNT(*) FROM mimiciv_hosp.labevents WHERE itemid = 50971 AND charttime > TIMESTAMP '2100-01-02 00:00:00'"
    ).fetchone()[0] == 0

    assert connection.execute(
        "SELECT COUNT(*) FROM mimiciv_icu.chartevents WHERE stay_id = 1001 AND itemid IN (223900,223901,220739) AND charttime < TIMESTAMP '2100-01-01 00:00:00'"
    ).fetchone()[0] == 3
    assert connection.execute(
        "SELECT gcs_score FROM mimiciv_derived.sapsii WHERE stay_id = 1001"
    ).fetchone()[0] == 5

    assert connection.execute(
        "SELECT bun_score FROM mimiciv_derived.sapsii WHERE stay_id = 1002"
    ).fetchone()[0] == 10
    assert connection.execute(
        "SELECT uo_score FROM mimiciv_derived.sapsii WHERE stay_id = 1002"
    ).fetchone()[0] is None
    assert connection.execute(
        "SELECT pao2fio2_score FROM mimiciv_derived.sapsii WHERE stay_id = 1002"
    ).fetchone()[0] is None

    assert connection.execute(
        "SELECT pao2fio2_score FROM mimiciv_derived.sapsii WHERE stay_id = 1005"
    ).fetchone()[0] == 9
    assert connection.execute(
        "SELECT pao2fio2_score FROM mimiciv_derived.sapsii WHERE stay_id = 1004"
    ).fetchone()[0] == 9
    assert connection.execute(
        "SELECT COUNT(*) FROM mimiciv_derived.ventilation WHERE stay_id = 1005 AND starttime < TIMESTAMP '2100-04-02 00:00:00' AND endtime >= TIMESTAMP '2100-04-02 00:00:00'"
    ).fetchone()[0] == 1


def test_comorbidity_and_admission_classification(computed_pipeline) -> None:
    rows = computed_pipeline["optimized"].execute(
        """
        SELECT stay_id, comorbidity_score, admissiontype_score
        FROM mimiciv_derived.sapsii WHERE stay_id IN (1001,1003,1004) ORDER BY stay_id
        """
    ).fetchall()
    assert rows == [(1001, 17, 6), (1003, 10, 0), (1004, 9, 8)]
    assert computed_pipeline["optimized"].execute(
        "SELECT COUNT(*) FROM mimiciv_icu.icustays WHERE hadm_id = 11"
    ).fetchone()[0] == 2


def test_short_stay_metadata_exports_and_missingness(computed_pipeline) -> None:
    connection = computed_pipeline["optimized"]
    output = computed_pipeline["output"]
    check = validate_exports(
        connection,
        output_directory=output,
        identity_hash=computed_pipeline["identity"],
    )
    assert check["cohort_rows"] == 6
    short = connection.execute(
        f"""
        SELECT available_first_day_hours, stay_shorter_than_24h
        FROM read_parquet('{(output / 'scores.parquet').as_posix()}') WHERE stay_id = 1002
        """
    ).fetchone()
    exact = connection.execute(
        f"""
        SELECT available_first_day_hours, stay_shorter_than_24h
        FROM read_parquet('{(output / 'scores.parquet').as_posix()}') WHERE stay_id = 1003
        """
    ).fetchone()
    unknown = connection.execute(
        f"""
        SELECT available_first_day_hours, stay_shorter_than_24h
        FROM read_parquet('{(output / 'scores.parquet').as_posix()}') WHERE stay_id = 1006
        """
    ).fetchone()
    assert short == (5.0, True)
    assert exact == (24.0, False)
    assert unknown == (None, None)
    missingness_columns = {
        row[0]
        for row in connection.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{(output / 'score_missingness.parquet').as_posix()}')"
        ).fetchall()
    }
    assert {f"{component}_missing" for component in COMPONENT_COLUMNS} <= missingness_columns
    coverage = json.loads((output / "coverage.json").read_text())
    assert coverage["cohort_rows"] == 6
    assert coverage["stratified"]["shorter_than_24h"]["cohort_rows"] == 2
    assert coverage["stratified"]["unknown_length"]["cohort_rows"] == 1

    component_rows = (output / "component_missingness.csv").read_text(encoding="utf-8")
    assert component_rows.splitlines()[0].startswith(
        "component,short_stay_stratum,cohort_size,"
    )
    assert ",unknown_length," in component_rows


def _protected_output_copy(source: Path, destination: Path) -> Path:
    shutil.copytree(source, destination)
    destination.chmod(0o700)
    for path in destination.iterdir():
        if path.is_file():
            path.chmod(0o600)
    return destination


def test_validation_rejects_changed_score_content(computed_pipeline, tmp_path) -> None:
    output = _protected_output_copy(computed_pipeline["output"], tmp_path / "changed-score")
    score_path = output / "scores.parquet"
    table = pq.read_table(score_path)
    column_index = table.schema.get_field_index("sapsii_official")
    values = table.column(column_index).to_pylist()
    values[0] = int(values[0]) + 1
    pq.write_table(
        table.set_column(column_index, "sapsii_official", pa.array(values, type=pa.int64())),
        score_path,
    )
    os.chmod(score_path, 0o600)
    with pytest.raises(ExportError, match="differs from its declared projection"):
        validate_exports(
            computed_pipeline["optimized"],
            output_directory=output,
            identity_hash=computed_pipeline["identity"],
        )


def test_validation_rejects_inconsistent_summary(computed_pipeline, tmp_path) -> None:
    output = _protected_output_copy(computed_pipeline["output"], tmp_path / "changed-summary")
    coverage_path = output / "coverage.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    coverage["cohort_rows"] += 1
    coverage_path.write_text(json.dumps(coverage), encoding="utf-8")
    coverage_path.chmod(0o600)
    with pytest.raises(ExportError, match="coverage.json differs"):
        validate_exports(
            computed_pipeline["optimized"],
            output_directory=output,
            identity_hash=computed_pipeline["identity"],
        )


def test_validation_rejects_changed_missingness_key(computed_pipeline, tmp_path) -> None:
    output = _protected_output_copy(computed_pipeline["output"], tmp_path / "changed-missingness")
    missingness_path = output / "score_missingness.parquet"
    table = pq.read_table(missingness_path)
    column_index = table.schema.get_field_index("stay_id")
    values = table.column(column_index).to_pylist()
    values[0] = max(int(value) for value in values) + 1
    pq.write_table(
        table.set_column(column_index, "stay_id", pa.array(values, type=pa.int64())),
        missingness_path,
    )
    os.chmod(missingness_path, 0o600)
    with pytest.raises(ExportError, match="differs from its declared projection"):
        validate_exports(
            computed_pipeline["optimized"],
            output_directory=output,
            identity_hash=computed_pipeline["identity"],
        )
