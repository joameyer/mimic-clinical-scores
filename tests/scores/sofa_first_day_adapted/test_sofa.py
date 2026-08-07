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
from mimic_clinical_scores.scores.sofa_first_day_adapted.specification import (
    COMPONENT_COLUMNS,
    SOFA_FIRST_DAY_ADAPTED_SPEC,
    load_itemid_manifest,
)


def _build_score(connection, project_root, *, tracked_identity=None) -> None:
    if tracked_identity is None:
        execute_untracked(
            connection,
            concepts=SOFA_FIRST_DAY_ADAPTED_SPEC.concepts,
            vendor_root=SOFA_FIRST_DAY_ADAPTED_SPEC.vendor_root(project_root),
        )
        execute_untracked(
            connection,
            concepts=(SOFA_FIRST_DAY_ADAPTED_SPEC.score_concept,),
            vendor_root=SOFA_FIRST_DAY_ADAPTED_SPEC.score_vendor_root(project_root),
        )
    else:
        build_concepts(
            connection,
            concepts=SOFA_FIRST_DAY_ADAPTED_SPEC.concepts,
            vendor_root=SOFA_FIRST_DAY_ADAPTED_SPEC.vendor_root(project_root),
            identity_hash=tracked_identity,
        )
        build_concepts(
            connection,
            concepts=(SOFA_FIRST_DAY_ADAPTED_SPEC.score_concept,),
            vendor_root=SOFA_FIRST_DAY_ADAPTED_SPEC.score_vendor_root(project_root),
            identity_hash=tracked_identity,
        )


def _unfiltered_reference(root, project_root):
    connection = duckdb.connect()
    for schema in ("mimiciv_hosp", "mimiciv_icu", "mimiciv_derived"):
        connection.execute(f"CREATE SCHEMA {schema}")
    mapping = {
        "hosp/labevents.csv.gz": "mimiciv_hosp.labevents",
        "icu/chartevents.csv.gz": "mimiciv_icu.chartevents",
        "icu/icustays.csv.gz": "mimiciv_icu.icustays",
        "icu/inputevents.csv.gz": "mimiciv_icu.inputevents",
        "icu/outputevents.csv.gz": "mimiciv_icu.outputevents",
    }
    for relative, table in mapping.items():
        connection.execute(
            f"CREATE TABLE {table} AS SELECT * FROM {_scan(root / relative, RAW_SCHEMAS[relative])}"
        )
    _build_score(connection, project_root)
    return connection


def _optimized(root, cohort_file, project_root, temporary):
    preflight = run_preflight(
        project_root=project_root,
        mimic_root=root,
        cohort_file=cohort_file,
        mode="full",
        specification=SOFA_FIRST_DAY_ADAPTED_SPEC,
    )
    settings = DuckDBSettings(temporary / "sofa.duckdb", threads=1, memory_limit="1GB")
    connection = connect(settings)
    identity = ensure_run_identity(connection, identity_payload(preflight, mimic_version="synthetic"))
    build_staging(
        connection,
        mimic_root=root,
        cohort=inspect_cohort(cohort_file, mode="full"),
        identity_hash=identity,
        raw_metadata=preflight["raw_sources"],
        profile_directory=temporary / "profiles",
        specification=SOFA_FIRST_DAY_ADAPTED_SPEC,
    )
    _build_score(connection, project_root, tracked_identity=identity)
    return connection, identity, preflight, settings


def test_manifest_and_pinned_sql_dependencies_are_exact(project_root) -> None:
    manifest = load_itemid_manifest()
    assert manifest["manifest_version"] == "sofa-first-day-adapted-v1"
    assert {entry["raw_table"] for entry in manifest["entries"]} == {
        "mimiciv_hosp.labevents",
        "mimiciv_icu.chartevents",
        "mimiciv_icu.inputevents",
        "mimiciv_icu.outputevents",
    }
    hashes = SOFA_FIRST_DAY_ADAPTED_SPEC.sql_hashes(project_root)
    assert len(hashes) == len(SOFA_FIRST_DAY_ADAPTED_SPEC.concepts) + 1
    assert hashes["mimic-iv/concepts_duckdb/medication/norepinephrine.sql"] == (
        "4085001f87dcdeeccde5877eb0dd3cc9718c7a8aab2bb57c2a6ab41c063ffabd"
    )


def test_filtered_staging_is_null_safe_exact_unfiltered_reference(
    tmp_path, project_root, synthetic_mimic
) -> None:
    root = synthetic_mimic["root"]
    optimized, identity, preflight, settings = _optimized(
        root, synthetic_mimic["cohort_file"], project_root, tmp_path
    )
    reference = _unfiltered_reference(root, project_root)
    columns = (
        "subject_id,hadm_id,stay_id,sofa_first_day_adapted,"
        + ",".join(COMPONENT_COLUMNS)
        + ",pao2fio2_novent_min,pao2fio2_vent_min,platelet_min,bilirubin_max,"
        "mbp_min,rate_norepinephrine,rate_epinephrine,rate_dopamine,rate_dobutamine,"
        "gcs_min,creatinine_max,urineoutput"
    )
    expected = reference.execute(
        f"SELECT {columns} FROM mimiciv_derived.sofa_first_day_adapted ORDER BY stay_id"
    ).fetchall()
    actual = optimized.execute(
        f"SELECT {columns} FROM mimiciv_derived.sofa_first_day_adapted ORDER BY stay_id"
    ).fetchall()
    assert actual == expected

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
        specification=SOFA_FIRST_DAY_ADAPTED_SPEC,
    )
    assert validate_exports(optimized, output_directory=output, identity_hash=identity)["valid"]
    assert preflight["official"]["adaptation_source_manifest"]["adaptation_version"] == (
        "sofa-first-day-adapted-v1"
    )
    reference.close()
    optimized.close()


def test_corrected_ventilated_pf_branches_and_inclusive_boundaries(tmp_path, project_root) -> None:
    root = tmp_path / "raw"
    data = {relative: [] for relative in RAW_FILES}
    base = datetime(2100, 1, 1)
    for stay_id, pao2 in ((101, 100.0), (102, 140.0)):
        data["icu/icustays.csv.gz"].append(
            row(
                "icu/icustays.csv.gz", subject_id=stay_id, hadm_id=stay_id + 1000,
                stay_id=stay_id, first_careunit="MICU", last_careunit="MICU",
                intime=ts(base), outtime=ts(base, hours=12), los=0.5,
            )
        )
        for itemid, hour, value in (
            (223849, -6, "CMV"),
            (223849, 2, "CMV"),
            (223835, 0, "40"),
            (220052, -6, "80"),
            (220052, 24, "80"),
            (220052, 24.001, "10"),
        ):
            data["icu/chartevents.csv.gz"].append(
                row(
                    "icu/chartevents.csv.gz", subject_id=stay_id,
                    hadm_id=stay_id + 1000, stay_id=stay_id, caregiver_id=1,
                    charttime=ts(base, hours=hour), storetime=ts(base, hours=hour),
                    itemid=itemid, value=value,
                    valuenum=float(value) if value.replace(".", "", 1).isdigit() else None,
                )
            )
        if stay_id == 101:
            for itemid, value in ((220739, "1"), (223900, "1"), (223901, "3")):
                data["icu/chartevents.csv.gz"].append(
                    row(
                        "icu/chartevents.csv.gz", subject_id=stay_id,
                        hadm_id=stay_id + 1000, stay_id=stay_id, caregiver_id=1,
                        charttime=ts(base), storetime=ts(base), itemid=itemid,
                        value=value, valuenum=float(value),
                    )
                )
        specimen = 9000 + stay_id
        for itemid, value, valuenum in ((52033, "ART.", None), (50821, str(pao2), pao2)):
            data["hosp/labevents.csv.gz"].append(
                row(
                    "hosp/labevents.csv.gz", labevent_id=len(data["hosp/labevents.csv.gz"]) + 1,
                    subject_id=stay_id, hadm_id=stay_id + 1000, specimen_id=specimen,
                    itemid=itemid, charttime=ts(base, hours=1), storetime=ts(base, hours=1),
                    value=value, valuenum=valuenum,
                )
            )
        if stay_id == 101:
            for itemid, value in ((51265, 19.0), (50885, 12.0), (50912, 5.0)):
                data["hosp/labevents.csv.gz"].append(
                    row(
                        "hosp/labevents.csv.gz",
                        labevent_id=len(data["hosp/labevents.csv.gz"]) + 1,
                        subject_id=stay_id, hadm_id=stay_id + 1000,
                        specimen_id=10000 + itemid, itemid=itemid,
                        charttime=ts(base), storetime=ts(base), value=str(value),
                        valuenum=value,
                    )
                )
            data["icu/outputevents.csv.gz"].append(
                row(
                    "icu/outputevents.csv.gz", subject_id=stay_id,
                    hadm_id=stay_id + 1000, stay_id=stay_id, caregiver_id=1,
                    charttime=ts(base), storetime=ts(base), itemid=226559,
                    value=100.0, valueuom="ml",
                )
            )
            data["icu/inputevents.csv.gz"].append(
                row(
                    "icu/inputevents.csv.gz", subject_id=stay_id,
                    hadm_id=stay_id + 1000, stay_id=stay_id, caregiver_id=1,
                    starttime=ts(base), endtime=ts(base, hours=2),
                    storetime=ts(base), itemid=221906, amount=10.0,
                    rate=0.2, rateuom="mcg/kg/min", linkorderid=1,
                    patientweight=70.0,
                )
            )
    write_raw(root, data)
    cohort_file = tmp_path / "cohort.parquet"
    pq.write_table(pa.table({"stay_id": pa.array([101, 102], type=pa.int64())}), cohort_file)
    optimized, _, _, _ = _optimized(root, cohort_file, project_root, tmp_path)
    rows = optimized.execute(
        "SELECT stay_id,pao2fio2_vent_min,respiration_score,coagulation_score,"
        "liver_score,cardiovascular_score,cns_score,renal_score,sofa_first_day_adapted "
        "FROM mimiciv_derived.sofa_first_day_adapted ORDER BY stay_id"
    ).fetchall()
    assert rows == [
        (101, 250.0, 2, 4, 4, 4, 4, 4, 22),
        (102, 350.0, 1, None, None, 0, None, None, 1),
    ]
    assert optimized.execute(
        "SELECT COUNT(*) FROM mimiciv_icu.chartevents WHERE itemid=220052"
    ).fetchone()[0] == 4
    optimized.close()
