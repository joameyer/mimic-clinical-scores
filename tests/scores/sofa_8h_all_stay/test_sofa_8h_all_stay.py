from __future__ import annotations

from datetime import datetime

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from conftest import RAW_FILES, row, ts, write_raw

from mimic_clinical_scores.common.cohort import inspect_cohort
from mimic_clinical_scores.common.concepts import build_concepts, execute_untracked
from mimic_clinical_scores.common.duckdb import DuckDBSettings, connect, ensure_run_identity
from mimic_clinical_scores.common.export import export_all, validate_exports
from mimic_clinical_scores.common.preflight import identity_payload, run_preflight
from mimic_clinical_scores.common.reference import _scan
from mimic_clinical_scores.common.staging import RAW_SCHEMAS, build_staging
from mimic_clinical_scores.scores.sofa_8h_all_stay.specification import (
    SOFA_8H_ALL_STAY_SPEC,
    load_itemid_manifest,
)
from mimic_clinical_scores.scores.sofa_hourly_14d.specification import (
    SOFA_HOURLY_14D_SPEC,
)


RAW_MAPPING = {
    "hosp/labevents.csv.gz": "mimiciv_hosp.labevents",
    "icu/chartevents.csv.gz": "mimiciv_icu.chartevents",
    "icu/icustays.csv.gz": "mimiciv_icu.icustays",
    "icu/inputevents.csv.gz": "mimiciv_icu.inputevents",
    "icu/outputevents.csv.gz": "mimiciv_icu.outputevents",
}


def _build(connection, project_root, identity=None) -> None:
    build = execute_untracked if identity is None else build_concepts
    common = {
        "connection": connection,
        "concepts": SOFA_8H_ALL_STAY_SPEC.concepts,
        "vendor_root": SOFA_8H_ALL_STAY_SPEC.vendor_root(project_root),
    }
    if identity is not None:
        common["identity_hash"] = identity
    build(**common)
    score = {
        "connection": connection,
        "concepts": (SOFA_8H_ALL_STAY_SPEC.score_concept,),
        "vendor_root": SOFA_8H_ALL_STAY_SPEC.score_vendor_root(project_root),
    }
    if identity is not None:
        score["identity_hash"] = identity
    build(**score)


def test_filtered_staging_matches_unfiltered_and_exports_complete_stay_blocks(
    tmp_path, project_root, synthetic_mimic
) -> None:
    root = synthetic_mimic["root"]
    cohort_file = synthetic_mimic["cohort_file"]
    preflight = run_preflight(
        project_root=project_root,
        mimic_root=root,
        cohort_file=cohort_file,
        mode="full",
        specification=SOFA_8H_ALL_STAY_SPEC,
    )
    assert preflight["official"]["item_manifest_version"] == "sofa-8h-all-stay-v1"
    assert (
        preflight["official"]["adaptation_source_manifest"]["adaptation_version"]
        == "sofa-8h-all-stay-v1"
    )
    settings = DuckDBSettings(tmp_path / "blocks.duckdb", threads=1, memory_limit="1GB")
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
        specification=SOFA_8H_ALL_STAY_SPEC,
    )
    _build(optimized, project_root, identity)

    reference = duckdb.connect()
    for schema in ("mimiciv_hosp", "mimiciv_icu", "mimiciv_derived"):
        reference.execute(f"CREATE SCHEMA {schema}")
    for relative, table in RAW_MAPPING.items():
        reference.execute(
            f"CREATE TABLE {table} AS SELECT * FROM "
            f"{_scan(root / relative, RAW_SCHEMAS[relative])}"
        )
    _build(reference, project_root)

    columns = (
        "stay_id,hr,starttime,endtime,sofa_24hours,"
        "respiration_24hours_raw,coagulation_24hours_raw,liver_24hours_raw,"
        "cardiovascular_24hours_raw,cns_24hours_raw,renal_24hours_raw,"
        "pao2fio2ratio_novent,pao2fio2ratio_novent_charttime,pao2_novent,"
        "fio2_novent,fio2_source_novent,pao2fio2ratio_vent,"
        "pao2fio2ratio_vent_charttime,pao2_vent,fio2_vent,fio2_source_vent,"
        "gcs_min,gcs_charttime,gcs_motor,gcs_verbal,gcs_eyes,gcs_unable,"
        "gcs_components_measured,uo_24hr,uo_24hr_charttime,urineoutput_24hr,"
        "uo_tm_24hr"
    )
    expected = reference.execute(
        f"SELECT {columns} FROM mimiciv_derived.sofa_8h_all_stay "
        "WHERE stay_id IN (SELECT stay_id FROM read_parquet(?)) ORDER BY stay_id,hr",
        [str(cohort_file)],
    ).fetchall()
    actual = optimized.execute(
        f"SELECT {columns} FROM mimiciv_derived.sofa_8h_all_stay ORDER BY stay_id,hr"
    ).fetchall()
    assert actual == expected
    assert len(actual) == 14
    assert optimized.execute(
        "SELECT stay_id,MIN(hr),MAX(hr),COUNT(*) "
        "FROM mimiciv_derived.sofa_8h_all_stay GROUP BY stay_id ORDER BY stay_id"
    ).fetchall() == [
        (1001, 0, 3, 4),
        (1002, 0, 0, 1),
        (1003, 0, 2, 3),
        (1004, 0, 1, 2),
        (1005, 0, 3, 4),
    ]
    execute_untracked(
        optimized,
        concepts=(SOFA_HOURLY_14D_SPEC.score_concept,),
        vendor_root=SOFA_HOURLY_14D_SPEC.score_vendor_root(project_root),
    )
    assert optimized.execute(
        "SELECT COUNT(*) FROM mimiciv_derived.sofa_8h_all_stay b "
        "JOIN mimiciv_derived.sofa_hourly_14d h "
        "ON b.stay_id=h.stay_id AND b.endtime=h.endtime"
    ).fetchone()[0] == 14
    assert optimized.execute(
        "SELECT COUNT(*) FROM mimiciv_derived.sofa_8h_all_stay b "
        "JOIN mimiciv_derived.sofa_hourly_14d h "
        "ON b.stay_id=h.stay_id AND b.endtime=h.endtime "
        "WHERE b.sofa_24hours IS DISTINCT FROM h.sofa_24hours "
        "OR b.respiration_24hours_raw IS DISTINCT FROM h.respiration_24hours_raw "
        "OR b.coagulation_24hours_raw IS DISTINCT FROM h.coagulation_24hours_raw "
        "OR b.liver_24hours_raw IS DISTINCT FROM h.liver_24hours_raw "
        "OR b.cardiovascular_24hours_raw IS DISTINCT FROM h.cardiovascular_24hours_raw "
        "OR b.cns_24hours_raw IS DISTINCT FROM h.cns_24hours_raw "
        "OR b.renal_24hours_raw IS DISTINCT FROM h.renal_24hours_raw"
    ).fetchone()[0] == 0

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
        specification=SOFA_8H_ALL_STAY_SPEC,
    )
    validation = validate_exports(
        optimized,
        output_directory=output,
        identity_hash=identity,
        specification=SOFA_8H_ALL_STAY_SPEC,
    )
    assert validation == {
        "valid": True,
        "cohort_rows": 6,
        "score_rows": 14,
        "scored_stays": 5,
        "excluded_stays": 1,
        "maximum_block_index": 3,
        "checked_outputs": [
            "scores.parquet", "score_missingness.parquet", "component_missingness.csv",
            "coverage.json", "staging_statistics.json", "unit_validation.json",
            "run_manifest.json",
        ],
    }
    exported = optimized.execute(
        "SELECT block_index,block_start,block_end,block_duration_hours,"
        "trailing_window_start,trailing_window_end,adaptation_version "
        "FROM read_parquet(?) WHERE stay_id=1001 ORDER BY block_index",
        [str(output / "scores.parquet")],
    ).fetchall()
    assert exported[-1] == (
        3,
        datetime(2100, 1, 2, 0),
        datetime(2100, 1, 2, 6),
        6.0,
        datetime(2100, 1, 1, 6),
        datetime(2100, 1, 2, 6),
        "sofa-8h-all-stay-v1",
    )
    reference.close()
    optimized.close()


def test_exact_partial_window_intermediates_and_no_day_cap(project_root) -> None:
    con = duckdb.connect()
    con.execute("CREATE SCHEMA mimiciv_icu; CREATE SCHEMA mimiciv_derived")
    con.execute(
        "CREATE TABLE mimiciv_icu.icustays(subject_id INTEGER,hadm_id INTEGER,"
        "stay_id INTEGER,intime TIMESTAMP,outtime TIMESTAMP)"
    )
    con.execute(
        "INSERT INTO mimiciv_icu.icustays VALUES "
        "(1,10,100,TIMESTAMP '2100-01-01',TIMESTAMP '2100-01-01 18:00:00'),"
        "(2,20,200,TIMESTAMP '2100-02-01',TIMESTAMP '2100-02-17 16:00:00'),"
        "(3,30,300,TIMESTAMP '2100-03-01',NULL),"
        "(4,40,400,TIMESTAMP '2100-04-01',TIMESTAMP '2100-04-01')"
    )
    definitions = {
        "bg": (
            "subject_id INTEGER,charttime TIMESTAMP,po2 DOUBLE,fio2 DOUBLE,"
            "fio2_chartevents DOUBLE,pao2fio2ratio DOUBLE,specimen VARCHAR"
        ),
        "ventilation": (
            "stay_id INTEGER,starttime TIMESTAMP,endtime TIMESTAMP,"
            "ventilation_status VARCHAR"
        ),
        "vitalsign": "stay_id INTEGER,charttime TIMESTAMP,mbp DOUBLE",
        "gcs": (
            "stay_id INTEGER,charttime TIMESTAMP,gcs DOUBLE,gcs_motor DOUBLE,"
            "gcs_verbal DOUBLE,gcs_eyes DOUBLE,gcs_unable INTEGER"
        ),
        "enzyme": "hadm_id INTEGER,charttime TIMESTAMP,bilirubin_total DOUBLE",
        "chemistry": "hadm_id INTEGER,charttime TIMESTAMP,creatinine DOUBLE",
        "complete_blood_count": "hadm_id INTEGER,charttime TIMESTAMP,platelet DOUBLE",
        "urine_output_rate": (
            "stay_id INTEGER,charttime TIMESTAMP,uo_tm_24hr DOUBLE,"
            "urineoutput_24hr DOUBLE"
        ),
        "epinephrine": (
            "stay_id INTEGER,starttime TIMESTAMP,endtime TIMESTAMP,vaso_rate DOUBLE"
        ),
        "norepinephrine": (
            "stay_id INTEGER,starttime TIMESTAMP,endtime TIMESTAMP,vaso_rate DOUBLE"
        ),
        "dopamine": (
            "stay_id INTEGER,starttime TIMESTAMP,endtime TIMESTAMP,vaso_rate DOUBLE"
        ),
        "dobutamine": (
            "stay_id INTEGER,starttime TIMESTAMP,endtime TIMESTAMP,vaso_rate DOUBLE"
        ),
    }
    for table, columns in definitions.items():
        con.execute(f"CREATE TABLE mimiciv_derived.{table}({columns})")
    con.execute(
        "INSERT INTO mimiciv_derived.vitalsign VALUES "
        "(100,TIMESTAMP '2099-12-31 21:00:00',60)"
    )
    con.execute(
        "INSERT INTO mimiciv_derived.bg VALUES "
        "(1,TIMESTAMP '2100-01-01 07:00:00',80,40,50,200,'ART.')"
    )
    con.execute(
        "INSERT INTO mimiciv_derived.gcs VALUES "
        "(100,TIMESTAMP '2100-01-01 07:00:00',7,4,2,1,0)"
    )
    con.execute(
        "INSERT INTO mimiciv_derived.urine_output_rate VALUES "
        "(100,TIMESTAMP '2100-01-01 07:00:00',24,480)"
    )
    execute_untracked(
        con,
        concepts=(SOFA_8H_ALL_STAY_SPEC.score_concept,),
        vendor_root=SOFA_8H_ALL_STAY_SPEC.score_vendor_root(project_root),
    )

    assert con.execute(
        "SELECT hr,starttime,endtime,cardiovascular_24hours_raw "
        "FROM mimiciv_derived.sofa_8h_all_stay WHERE stay_id=100 ORDER BY hr"
    ).fetchall() == [
        (0, datetime(2100, 1, 1), datetime(2100, 1, 1, 8), 1),
        (1, datetime(2100, 1, 1, 8), datetime(2100, 1, 1, 16), 1),
        (2, datetime(2100, 1, 1, 16), datetime(2100, 1, 1, 18), 1),
    ]
    assert con.execute(
        "SELECT gcs_min,gcs_motor,gcs_verbal,gcs_eyes,"
        "pao2fio2ratio_novent,pao2_novent,fio2_novent,fio2_source_novent,"
        "uo_24hr,urineoutput_24hr,uo_tm_24hr "
        "FROM mimiciv_derived.sofa_8h_all_stay WHERE stay_id=100 AND hr=0"
    ).fetchone() == (
        7.0, 4.0, 2.0, 1.0, 200.0, 80.0, 40.0, "labevents",
        480.0, 480.0, 24.0,
    )
    assert con.execute(
        "SELECT MIN(hr),MAX(hr),COUNT(*) FROM mimiciv_derived.sofa_8h_all_stay "
        "WHERE stay_id=200"
    ).fetchone() == (0, 49, 50)
    assert con.execute(
        "SELECT COUNT(*) FROM mimiciv_derived.sofa_8h_all_stay "
        "WHERE stay_id IN (300,400)"
    ).fetchone()[0] == 0
    con.close()


def test_manifest_declares_complete_stay_context() -> None:
    manifest = load_itemid_manifest()
    assert manifest["manifest_version"] == "sofa-8h-all-stay-v1"
    urine = next(
        entry for entry in manifest["entries"]
        if entry["source_concept"] == "measurement/urine_output.sql"
    )
    assert urine["required_time_context"] == "all earlier selected-stay rows through outtime"


def test_staging_and_scoring_retain_measurements_after_day_fourteen(
    tmp_path, project_root
) -> None:
    root = tmp_path / "raw"
    data = {relative: [] for relative in RAW_FILES}
    intime = datetime(2100, 1, 1)
    data["icu/icustays.csv.gz"].append(
        row(
            "icu/icustays.csv.gz",
            subject_id=1,
            hadm_id=10,
            stay_id=100,
            first_careunit="MICU",
            last_careunit="MICU",
            intime=ts(intime),
            outtime=ts(intime, hours=400),
            los=400 / 24,
        )
    )
    data["icu/chartevents.csv.gz"].append(
        row(
            "icu/chartevents.csv.gz",
            subject_id=1,
            hadm_id=10,
            stay_id=100,
            caregiver_id=1,
            charttime=ts(intime, hours=350),
            storetime=ts(intime, hours=350, minutes=1),
            itemid=220052,
            value="60",
            valuenum=60.0,
        )
    )
    write_raw(root, data)
    cohort_file = tmp_path / "cohort.parquet"
    pq.write_table(pa.table({"stay_id": pa.array([100], type=pa.int64())}), cohort_file)
    preflight = run_preflight(
        project_root=project_root,
        mimic_root=root,
        cohort_file=cohort_file,
        mode="full",
        specification=SOFA_8H_ALL_STAY_SPEC,
    )
    settings = DuckDBSettings(tmp_path / "late.duckdb", threads=1, memory_limit="1GB")
    con = connect(settings)
    identity = ensure_run_identity(
        con, identity_payload(preflight, mimic_version="synthetic")
    )
    build_staging(
        con,
        mimic_root=root,
        cohort=inspect_cohort(cohort_file, mode="full"),
        identity_hash=identity,
        raw_metadata=preflight["raw_sources"],
        profile_directory=tmp_path / "profiles",
        specification=SOFA_8H_ALL_STAY_SPEC,
    )
    _build(con, project_root, identity)
    assert con.execute(
        "SELECT charttime FROM mimiciv_icu.chartevents WHERE itemid=220052"
    ).fetchone()[0] == datetime(2100, 1, 15, 14)
    assert con.execute(
        "SELECT meanbp_min,cardiovascular FROM mimiciv_derived.sofa_8h_all_stay "
        "WHERE stay_id=100 AND hr=43"
    ).fetchone() == (60.0, 1)
    assert con.execute(
        "SELECT MAX(hr),COUNT(*) FROM mimiciv_derived.sofa_8h_all_stay WHERE stay_id=100"
    ).fetchone() == (49, 50)
    con.close()
