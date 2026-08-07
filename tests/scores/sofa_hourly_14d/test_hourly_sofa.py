from __future__ import annotations

import duckdb
from datetime import datetime

from mimic_clinical_scores.common.cohort import inspect_cohort
from mimic_clinical_scores.common.concepts import build_concepts, execute_untracked
from mimic_clinical_scores.common.duckdb import DuckDBSettings, connect, ensure_run_identity
from mimic_clinical_scores.common.export import export_all, validate_exports
from mimic_clinical_scores.common.preflight import identity_payload, run_preflight
from mimic_clinical_scores.common.reference import _scan
from mimic_clinical_scores.common.staging import RAW_SCHEMAS, build_staging
from mimic_clinical_scores.scores.sofa_hourly_14d.specification import (
    SOFA_HOURLY_14D_SPEC,
    load_itemid_manifest,
)


RAW_MAPPING = {
    "hosp/labevents.csv.gz": "mimiciv_hosp.labevents",
    "icu/chartevents.csv.gz": "mimiciv_icu.chartevents",
    "icu/icustays.csv.gz": "mimiciv_icu.icustays",
    "icu/inputevents.csv.gz": "mimiciv_icu.inputevents",
    "icu/outputevents.csv.gz": "mimiciv_icu.outputevents",
}


def _build(connection, project_root, identity=None):
    if identity is None:
        execute_untracked(
            connection,
            concepts=SOFA_HOURLY_14D_SPEC.concepts,
            vendor_root=SOFA_HOURLY_14D_SPEC.vendor_root(project_root),
        )
        execute_untracked(
            connection,
            concepts=(SOFA_HOURLY_14D_SPEC.score_concept,),
            vendor_root=SOFA_HOURLY_14D_SPEC.score_vendor_root(project_root),
        )
    else:
        build_concepts(
            connection,
            concepts=SOFA_HOURLY_14D_SPEC.concepts,
            vendor_root=SOFA_HOURLY_14D_SPEC.vendor_root(project_root),
            identity_hash=identity,
        )
        build_concepts(
            connection,
            concepts=(SOFA_HOURLY_14D_SPEC.score_concept,),
            vendor_root=SOFA_HOURLY_14D_SPEC.score_vendor_root(project_root),
            identity_hash=identity,
        )


def test_hourly_filtered_staging_matches_unfiltered_and_grid_is_bounded(
    tmp_path, project_root, synthetic_mimic
) -> None:
    root = synthetic_mimic["root"]
    cohort_file = synthetic_mimic["cohort_file"]
    preflight = run_preflight(
        project_root=project_root,
        mimic_root=root,
        cohort_file=cohort_file,
        mode="full",
        specification=SOFA_HOURLY_14D_SPEC,
    )
    settings = DuckDBSettings(tmp_path / "hourly.duckdb", threads=1, memory_limit="1GB")
    optimized = connect(settings)
    identity = ensure_run_identity(
        optimized, identity_payload(preflight, mimic_version="synthetic")
    )
    build_staging(
        optimized,
        mimic_root=root,
        cohort=inspect_cohort(cohort_file, mode="full"),
        identity_hash=identity,
        raw_metadata=preflight["raw_sources"],
        profile_directory=tmp_path / "profiles",
        specification=SOFA_HOURLY_14D_SPEC,
    )
    _build(optimized, project_root, identity)

    reference = duckdb.connect()
    for schema in ("mimiciv_hosp", "mimiciv_icu", "mimiciv_derived"):
        reference.execute(f"CREATE SCHEMA {schema}")
    for relative, table in RAW_MAPPING.items():
        reference.execute(
            f"CREATE TABLE {table} AS SELECT * FROM {_scan(root / relative, RAW_SCHEMAS[relative])}"
        )
    _build(reference, project_root)

    columns = (
        "stay_id,hr,starttime,endtime,sofa_24hours,"
        "respiration_24hours_raw,coagulation_24hours_raw,liver_24hours_raw,"
        "cardiovascular_24hours_raw,cns_24hours_raw,renal_24hours_raw"
    )
    expected = reference.execute(
        f"SELECT {columns} FROM mimiciv_derived.sofa_hourly_14d "
        "WHERE stay_id IN (SELECT stay_id FROM read_parquet(?)) ORDER BY stay_id,hr",
        [str(cohort_file)],
    ).fetchall()
    actual = optimized.execute(
        f"SELECT {columns} FROM mimiciv_derived.sofa_hourly_14d ORDER BY stay_id,hr"
    ).fetchall()
    assert actual == expected
    assert len(actual) == 437
    assert optimized.execute(
        "SELECT MIN(hr),MAX(hr) FROM mimiciv_derived.sofa_hourly_14d WHERE stay_id=1001"
    ).fetchone() == (0, 29)
    assert optimized.execute(
        "SELECT MIN(hr),MAX(hr),COUNT(*) FROM mimiciv_derived.sofa_hourly_14d WHERE stay_id=1006"
    ).fetchone() == (0, 335, 336)

    output = tmp_path / "outputs"
    export_all(
        optimized,
        output_directory=output,
        identity_hash=identity,
        mode="full",
        mimic_version="synthetic",
        cohort_manifest=None,
        preflight=preflight,
        runtime=settings,
        command_line=["pytest"],
        specification=SOFA_HOURLY_14D_SPEC,
    )
    validation = validate_exports(
        optimized,
        output_directory=output,
        identity_hash=identity,
        specification=SOFA_HOURLY_14D_SPEC,
    )
    assert validation == {
        "valid": True,
        "cohort_rows": 6,
        "score_rows": 437,
        "maximum_hour_index": 335,
        "checked_outputs": [
            "scores.parquet", "score_missingness.parquet", "component_missingness.csv",
            "coverage.json", "staging_statistics.json", "run_manifest.json",
        ],
    }
    reference.close()
    optimized.close()


def test_hourly_manifest_audits_new_recursive_dependencies() -> None:
    manifest = load_itemid_manifest()
    assert manifest["manifest_version"] == "sofa-hourly-14d-v1"
    by_concept = {entry["source_concept"] for entry in manifest["entries"]}
    assert "demographics/weight_durations.sql" in by_concept
    assert "measurement/urine_output_rate.sql" in by_concept


def test_intime_relative_boundaries_partial_discharge_and_preicu_rolling(project_root) -> None:
    con = duckdb.connect()
    con.execute("CREATE SCHEMA mimiciv_icu; CREATE SCHEMA mimiciv_derived")
    con.execute(
        "CREATE TABLE mimiciv_icu.icustays(subject_id INTEGER,hadm_id INTEGER,"
        "stay_id INTEGER,intime TIMESTAMP,outtime TIMESTAMP)"
    )
    con.execute(
        "INSERT INTO mimiciv_icu.icustays VALUES "
        "(1,10,100,TIMESTAMP '2100-01-01 00:00:00',TIMESTAMP '2100-01-01 02:15:00')"
    )
    definitions = {
        "bg": "subject_id INTEGER,charttime TIMESTAMP,pao2fio2ratio DOUBLE,specimen VARCHAR",
        "ventilation": "stay_id INTEGER,starttime TIMESTAMP,endtime TIMESTAMP,ventilation_status VARCHAR",
        "vitalsign": "stay_id INTEGER,charttime TIMESTAMP,mbp DOUBLE",
        "gcs": "stay_id INTEGER,charttime TIMESTAMP,gcs DOUBLE",
        "enzyme": "hadm_id INTEGER,charttime TIMESTAMP,bilirubin_total DOUBLE",
        "chemistry": "hadm_id INTEGER,charttime TIMESTAMP,creatinine DOUBLE",
        "complete_blood_count": "hadm_id INTEGER,charttime TIMESTAMP,platelet DOUBLE",
        "urine_output_rate": "stay_id INTEGER,charttime TIMESTAMP,uo_tm_24hr DOUBLE,urineoutput_24hr DOUBLE",
        "epinephrine": "stay_id INTEGER,starttime TIMESTAMP,endtime TIMESTAMP,vaso_rate DOUBLE",
        "norepinephrine": "stay_id INTEGER,starttime TIMESTAMP,endtime TIMESTAMP,vaso_rate DOUBLE",
        "dopamine": "stay_id INTEGER,starttime TIMESTAMP,endtime TIMESTAMP,vaso_rate DOUBLE",
        "dobutamine": "stay_id INTEGER,starttime TIMESTAMP,endtime TIMESTAMP,vaso_rate DOUBLE",
    }
    for table, columns in definitions.items():
        con.execute(f"CREATE TABLE mimiciv_derived.{table}({columns})")
    con.execute(
        "INSERT INTO mimiciv_derived.vitalsign VALUES "
        "(100,TIMESTAMP '2100-01-01 00:00:00',60),"
        "(100,TIMESTAMP '2100-01-01 01:00:00',80),"
        "(100,TIMESTAMP '2100-01-01 01:00:01',60)"
    )
    execute_untracked(
        con,
        concepts=(SOFA_HOURLY_14D_SPEC.score_concept,),
        vendor_root=SOFA_HOURLY_14D_SPEC.score_vendor_root(project_root),
    )
    rows = con.execute(
        "SELECT hr,starttime,endtime,meanbp_min,cardiovascular,"
        "cardiovascular_24hours_raw,sofa_24hours "
        "FROM mimiciv_derived.sofa_hourly_14d ORDER BY hr"
    ).fetchall()
    assert rows == [
        (
            0, datetime(2100, 1, 1, 0), datetime(2100, 1, 1, 1),
            80.0, 0, 1, 1,
        ),
        (
            1, datetime(2100, 1, 1, 1), datetime(2100, 1, 1, 2),
            60.0, 1, 1, 1,
        ),
        (
            2, datetime(2100, 1, 1, 2), datetime(2100, 1, 1, 2, 15),
            None, None, 1, 1,
        ),
    ]
    con.close()
