from __future__ import annotations

import duckdb

from mimic_clinical_scores.common.cohort import inspect_cohort
from mimic_clinical_scores.common.concepts import build_concepts, execute_untracked
from mimic_clinical_scores.common.duckdb import DuckDBSettings, connect, ensure_run_identity
from mimic_clinical_scores.common.export import export_all, validate_exports
from mimic_clinical_scores.common.preflight import identity_payload, run_preflight
from mimic_clinical_scores.common.reference import _scan
from mimic_clinical_scores.common.staging import RAW_SCHEMAS, build_staging
from mimic_clinical_scores.scores.sofa_hourly_reverse_7d.specification import (
    SOFA_HOURLY_REVERSE_7D_SPEC,
    load_itemid_manifest,
)


RAW_MAPPING = {
    "hosp/admissions.csv.gz": "mimiciv_hosp.admissions",
    "hosp/labevents.csv.gz": "mimiciv_hosp.labevents",
    "icu/chartevents.csv.gz": "mimiciv_icu.chartevents",
    "icu/icustays.csv.gz": "mimiciv_icu.icustays",
    "icu/inputevents.csv.gz": "mimiciv_icu.inputevents",
    "icu/outputevents.csv.gz": "mimiciv_icu.outputevents",
}


def _build(connection, project_root, identity=None) -> None:
    runner = build_concepts if identity is not None else execute_untracked
    kwargs = {"identity_hash": identity} if identity is not None else {}
    runner(
        connection,
        concepts=SOFA_HOURLY_REVERSE_7D_SPEC.concepts,
        vendor_root=SOFA_HOURLY_REVERSE_7D_SPEC.vendor_root(project_root),
        **kwargs,
    )
    runner(
        connection,
        concepts=(SOFA_HOURLY_REVERSE_7D_SPEC.score_concept,),
        vendor_root=SOFA_HOURLY_REVERSE_7D_SPEC.score_vendor_root(project_root),
        **kwargs,
    )


def test_reverse_filtered_staging_matches_unfiltered_and_excludes_null_outtime(
    tmp_path, project_root, synthetic_mimic
) -> None:
    root = synthetic_mimic["root"]
    cohort_file = synthetic_mimic["cohort_file"]
    preflight = run_preflight(
        project_root=project_root,
        mimic_root=root,
        cohort_file=cohort_file,
        mode="full",
        specification=SOFA_HOURLY_REVERSE_7D_SPEC,
    )
    settings = DuckDBSettings(tmp_path / "reverse.duckdb", threads=1, memory_limit="1GB")
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
        specification=SOFA_HOURLY_REVERSE_7D_SPEC,
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
        "stay_id,hours_before_discharge,starttime,endtime,sofa_24hours,"
        "respiration_24hours_raw,coagulation_24hours_raw,liver_24hours_raw,"
        "cardiovascular_24hours_raw,cns_24hours_raw,renal_24hours_raw"
    )
    expected = reference.execute(
        f"SELECT {columns} FROM mimiciv_derived.sofa_hourly_reverse_7d "
        "WHERE stay_id IN (SELECT stay_id FROM read_parquet(?)) "
        "ORDER BY stay_id,hours_before_discharge",
        [str(cohort_file)],
    ).fetchall()
    actual = optimized.execute(
        f"SELECT {columns} FROM mimiciv_derived.sofa_hourly_reverse_7d "
        "ORDER BY stay_id,hours_before_discharge"
    ).fetchall()
    assert actual == expected
    assert len(actual) == 101
    assert optimized.execute(
        "SELECT COUNT(DISTINCT stay_id) FROM mimiciv_derived.sofa_hourly_reverse_7d"
    ).fetchone()[0] == 5
    assert optimized.execute(
        "SELECT MIN(hours_before_discharge),MAX(hours_before_discharge) "
        "FROM mimiciv_derived.sofa_hourly_reverse_7d WHERE stay_id=1001"
    ).fetchone() == (0, 29)

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
        specification=SOFA_HOURLY_REVERSE_7D_SPEC,
    )
    validation = validate_exports(
        optimized,
        output_directory=output,
        identity_hash=identity,
        specification=SOFA_HOURLY_REVERSE_7D_SPEC,
    )
    assert validation["valid"]
    assert validation["cohort_rows"] == 6
    assert validation["scored_stays"] == 5
    assert validation["excluded_stays"] == 1
    assert validation["score_rows"] == 101
    reference.close()
    optimized.close()


def test_reverse_grid_cap_partial_interval_and_death_annotation(project_root) -> None:
    con = duckdb.connect()
    con.execute("CREATE SCHEMA mimiciv_hosp; CREATE SCHEMA mimiciv_icu; CREATE SCHEMA mimiciv_derived")
    con.execute(
        "CREATE TABLE mimiciv_icu.icustays(subject_id INTEGER,hadm_id INTEGER,stay_id INTEGER,"
        "intime TIMESTAMP,outtime TIMESTAMP)"
    )
    con.execute(
        "INSERT INTO mimiciv_icu.icustays VALUES "
        "(1,10,100,TIMESTAMP '2100-01-01 00:00:00',TIMESTAMP '2100-01-01 02:15:00'),"
        "(2,20,200,TIMESTAMP '2100-01-01 00:00:00',TIMESTAMP '2100-01-09 08:00:00')"
    )
    con.execute(
        "CREATE TABLE mimiciv_hosp.admissions(hadm_id INTEGER,deathtime TIMESTAMP,"
        "hospital_expire_flag SMALLINT)"
    )
    con.execute(
        "INSERT INTO mimiciv_hosp.admissions VALUES "
        "(10,TIMESTAMP '2100-01-01 02:15:00',1),(20,NULL,0)"
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
    execute_untracked(
        con,
        concepts=(SOFA_HOURLY_REVERSE_7D_SPEC.score_concept,),
        vendor_root=SOFA_HOURLY_REVERSE_7D_SPEC.score_vendor_root(project_root),
    )
    assert con.execute(
        "SELECT hours_before_discharge,starttime,endtime FROM "
        "mimiciv_derived.sofa_hourly_reverse_7d WHERE stay_id=100 "
        "ORDER BY hours_before_discharge"
    ).fetchall() == [
        (0, duckdb.execute("SELECT TIMESTAMP '2100-01-01 01:15:00'").fetchone()[0],
         duckdb.execute("SELECT TIMESTAMP '2100-01-01 02:15:00'").fetchone()[0]),
        (1, duckdb.execute("SELECT TIMESTAMP '2100-01-01 00:15:00'").fetchone()[0],
         duckdb.execute("SELECT TIMESTAMP '2100-01-01 01:15:00'").fetchone()[0]),
        (2, duckdb.execute("SELECT TIMESTAMP '2100-01-01 00:00:00'").fetchone()[0],
         duckdb.execute("SELECT TIMESTAMP '2100-01-01 00:15:00'").fetchone()[0]),
    ]
    assert con.execute(
        "SELECT COUNT(*),MAX(hours_before_discharge) FROM "
        "mimiciv_derived.sofa_hourly_reverse_7d WHERE stay_id=200"
    ).fetchone() == (168, 167)
    mortality = con.execute(
        SOFA_HOURLY_REVERSE_7D_SPEC.scores_projection_sql().replace(
            "ORDER BY s.stay_id, s.hours_before_discharge", ""
        ) + " LIMIT 1"
    ).fetchone()
    names = [column[0] for column in con.description]
    record = dict(zip(names, mortality))
    assert record["died_during_icu_stay"] is True
    assert record["death_recorded_by_icu_discharge"] is True
    assert record["alive_at_icu_discharge"] is False
    con.close()


def test_reverse_manifest_is_versioned() -> None:
    manifest = load_itemid_manifest()
    assert manifest["manifest_version"] == "sofa-hourly-reverse-7d-v1"
