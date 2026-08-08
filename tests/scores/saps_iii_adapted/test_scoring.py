from __future__ import annotations

import math
from datetime import datetime, timedelta

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from conftest import RAW_FILES, row, ts, write_raw
from mimic_clinical_scores.common.cohort import inspect_cohort
from mimic_clinical_scores.common.concepts import build_concepts
from mimic_clinical_scores.common.duckdb import DuckDBSettings, connect, ensure_run_identity
from mimic_clinical_scores.common.export import export_all, validate_exports
from mimic_clinical_scores.common.preflight import identity_payload, run_preflight
from mimic_clinical_scores.common.provenance import extract_item_ids
from mimic_clinical_scores.common.staging import build_staging
from mimic_clinical_scores.scores.saps_iii_adapted.reference import (
    physiology_points,
    proxy_total_and_unvalidated_probabilities,
)
from mimic_clinical_scores.scores.saps_iii_adapted.specification import SAPSIII_ADAPTED_SPEC, load_itemid_manifest


def test_adapted_item_manifest_is_complete_and_versioned(project_root) -> None:
    manifest = load_itemid_manifest()
    assert manifest["manifest_version"] == "saps-iii-adapted-v2"
    assert len(manifest["entries"]) == 29
    assert any(x["item_id"] == 52033 for x in manifest["entries"])
    assert any(x["item_id"] == 224642 for x in manifest["entries"])
    assert all({"raw_table", "item_id", "clinical_meaning", "time_context", "reason"} <= set(x) for x in manifest["entries"])
    sql = (
        SAPSIII_ADAPTED_SPEC.vendor_root(project_root)
        / SAPSIII_ADAPTED_SPEC.score_concept.sql_relative_path
    ).read_text(encoding="utf-8")
    assert extract_item_ids(sql) == {int(x["item_id"]) for x in manifest["entries"]}


def test_original_physiology_cutoffs_and_equations() -> None:
    points = physiology_points(
        gcs=4, heart_rate=160, systolic_bp=39, temperature_c=34.9,
        bilirubin_mg_dl=6, creatinine_mg_dl=3.5, wbc_highest_k_ul=15,
        platelets_k_ul=19, ph=7.25, mechanically_ventilated=True,
        pao2_mm_hg=50, pf_ratio=99,
    )
    assert points == {
        "gcs_score": 15, "hr_score": 7, "sysbp_score": 11, "temp_score": 7,
        "bilirubin_score": 5, "creatinine_score": 8, "wbc_score": 2,
        "platelet_score": 13, "ph_score": 3, "oxygenation_score": 11,
    }
    score, global_probability, na_probability = proxy_total_and_unvalidated_probabilities(points)
    assert score == 98
    assert 0 < global_probability < 1
    assert 0 < na_probability < 1

    base = dict(
        gcs=15, heart_rate=80, systolic_bp=120, temperature_c=37,
        bilirubin_mg_dl=1, creatinine_mg_dl=1, wbc_highest_k_ul=10,
        platelets_k_ul=150, ph=7.4, mechanically_ventilated=False,
        pao2_mm_hg=80, pf_ratio=None,
    )
    def values(field, cases, score_field):
        observed = []
        for value in cases:
            arguments = {**base, field: value}
            observed.append(physiology_points(**arguments)[score_field])
        return observed
    assert values("gcs", [3,4,5,6,7,12,13], "gcs_score") == [15,15,10,7,2,2,0]
    assert values("heart_rate", [119.9,120,159.9,160], "hr_score") == [0,5,5,7]
    assert values("systolic_bp", [39.9,40,69.9,70,119.9,120], "sysbp_score") == [11,8,8,3,3,0]
    assert values("temperature_c", [34.9,35], "temp_score") == [7,0]
    assert values("bilirubin_mg_dl", [1.9,2,5.9,6], "bilirubin_score") == [0,4,4,5]
    assert values("creatinine_mg_dl", [1.19,1.2,1.99,2,3.49,3.5], "creatinine_score") == [0,2,2,7,7,8]
    assert values("wbc_highest_k_ul", [14.9,15], "wbc_score") == [0,2]
    assert values("platelets_k_ul", [19.9,20,49.9,50,99.9,100], "platelet_score") == [13,8,8,5,5,0]
    assert values("ph", [7.25,7.251], "ph_score") == [3,0]
    assert values("pao2_mm_hg", [59.9,60], "oxygenation_score") == [5,0]
    missing = physiology_points(**{**base, **{
        "gcs": None, "heart_rate": None, "systolic_bp": None, "temperature_c": None,
        "bilirubin_mg_dl": None, "creatinine_mg_dl": None, "wbc_highest_k_ul": None,
        "platelets_k_ul": None, "ph": None, "pao2_mm_hg": None,
    }})
    assert set(missing.values()) == {None}
    assert proxy_total_and_unvalidated_probabilities(missing)[0] == 16


def test_filtered_admission_window_and_score(tmp_path, project_root) -> None:
    root = tmp_path / "raw"
    data = {relative: [] for relative in RAW_FILES}
    base = datetime(2100, 1, 2, 0, 0)
    data["icu/icustays.csv.gz"] = [row("icu/icustays.csv.gz", subject_id=1, hadm_id=10, stay_id=100, first_careunit="MICU", last_careunit="MICU", intime=ts(base), outtime=ts(base, hours=12), los=.5)]
    data["hosp/admissions.csv.gz"] = [row("hosp/admissions.csv.gz", subject_id=1, hadm_id=10, admittime=ts(base, days=-15), dischtime=ts(base, days=2), admission_type="URGENT", admission_location="TRANSFER")]
    data["hosp/patients.csv.gz"] = [row("hosp/patients.csv.gz", subject_id=1, gender="F", anchor_age=65, anchor_year=2100, anchor_year_group="2098 - 2102")]
    data["hosp/services.csv.gz"] = [row("hosp/services.csv.gz", subject_id=1, hadm_id=10, transfertime=ts(base, hours=-2), curr_service="MED")]
    data["hosp/transfers.csv.gz"] = [row("hosp/transfers.csv.gz", subject_id=1, hadm_id=10, transfer_id=1, eventtype="transfer", careunit="Emergency Department", intime=ts(base, hours=-3), outtime=ts(base))]
    data["hosp/diagnoses_icd.csv.gz"] = [row("hosp/diagnoses_icd.csv.gz", subject_id=1, hadm_id=10, seq_num=1, icd_code="R6521", icd_version=10)]

    def chart(itemid, hour, value):
        data["icu/chartevents.csv.gz"].append(row(
            "icu/chartevents.csv.gz", subject_id=1, hadm_id=10, stay_id=100,
            caregiver_id=1, charttime=ts(base, hours=hour), storetime=ts(base, hours=hour),
            itemid=itemid, value=str(value),
            valuenum=value if isinstance(value, (int, float)) else None,
        ))
    chart(220045, -1, 160); chart(220045, 1, 159); chart(220045, 1.001, 300)
    chart(220179, 0, 69); chart(223762, 0, 34); chart(224642, 0, "Blood")
    chart(220739, 0, 1); chart(223900, 0, 1); chart(223901, 0, 2)
    chart(223849, 0, 1)
    chart(223835, -1, 50)

    def lab(itemid, value, specimen=1):
        data["hosp/labevents.csv.gz"].append(row("hosp/labevents.csv.gz", labevent_id=len(data["hosp/labevents.csv.gz"])+1, subject_id=1, hadm_id=10, specimen_id=specimen, itemid=itemid, charttime=ts(base, hours=1), storetime=ts(base, hours=1), value=str(value), valuenum=value))
    for itemid, value in ((50885,6),(50912,3.5),(51301,10),(51301,15),(51265,19),(50820,7.25),(50821,50),(50816,50)):
        lab(itemid, value)
    data["hosp/labevents.csv.gz"].append(row(
        "hosp/labevents.csv.gz", labevent_id=len(data["hosp/labevents.csv.gz"])+1,
        subject_id=1, hadm_id=10, specimen_id=1, itemid=52033,
        charttime=ts(base, hours=1), storetime=ts(base, hours=1), value="ART.", valuenum=None,
    ))
    data["icu/inputevents.csv.gz"] = [row("icu/inputevents.csv.gz", subject_id=1, hadm_id=10, stay_id=100, starttime=ts(base, hours=-3), endtime=ts(base, hours=-1), itemid=221906, rate=0.1, rateuom="mcg/kg/min")]
    write_raw(root, data)
    cohort_file = tmp_path / "cohort.parquet"
    pq.write_table(pa.table({"stay_id": pa.array([100], type=pa.int64())}), cohort_file)

    preflight = run_preflight(project_root=project_root, mimic_root=root, cohort_file=cohort_file, mode="full", specification=SAPSIII_ADAPTED_SPEC)
    settings = DuckDBSettings(tmp_path / "score.duckdb", threads=1, memory_limit="1GB")
    con = connect(settings)
    identity = ensure_run_identity(con, identity_payload(preflight, mimic_version="synthetic"))
    build_staging(con, mimic_root=root, cohort=inspect_cohort(cohort_file, mode="full"), identity_hash=identity, raw_metadata=preflight["raw_sources"], profile_directory=tmp_path / "profiles", specification=SAPSIII_ADAPTED_SPEC)
    build_concepts(con, concepts=(SAPSIII_ADAPTED_SPEC.score_concept,), vendor_root=SAPSIII_ADAPTED_SPEC.vendor_root(project_root), identity_hash=identity)

    assert con.execute("SELECT COUNT(*) FROM mimiciv_icu.chartevents WHERE itemid=220045").fetchone()[0] == 2
    result = con.execute("SELECT gcs_proxy_score,hr_score,sysbp_score,temp_score,bilirubin_score,creatinine_score,wbc_score,platelet_score,ph_score,oxygenation_score,vasoactive_proxy_score,saps_iii_proxy_total_unvalidated,saps_iii_prob_global_proxy_unvalidated,saps_iii_prob_north_america_proxy_unvalidated FROM mimiciv_derived.saps_iii_adapted").fetchone()
    reference = physiology_points(gcs=4, heart_rate=160, systolic_bp=69, temperature_c=34, bilirubin_mg_dl=6, creatinine_mg_dl=3.5, wbc_highest_k_ul=15, platelets_k_ul=19, ph=7.25, mechanically_ventilated=True, pao2_mm_hg=50, pf_ratio=100)
    assert result[:10] == tuple(reference.values())
    assert result[10] == 3
    unavailable = con.execute(
        "SELECT comorbidity_score,vasoactive_score,planned_icu_score,admission_reason_score,"
        "surgery_status_score,surgical_site_score,infection_score,gcs_score,"
        "saps_iii_complete_case_score FROM mimiciv_derived.saps_iii_adapted"
    ).fetchone()
    assert unavailable == (None,) * 9
    context = con.execute(
        "SELECT age_score,hospital_los_score,admission_location_score,comorbidity_proxy_score,"
        "vasoactive_proxy_score,planned_icu_proxy_score,admission_reason_proxy_score,surgery_status_proxy_score,"
        "surgical_site_proxy_score,infection_proxy_score FROM mimiciv_derived.saps_iii_adapted"
    ).fetchone()
    assert context == (9, 6, 5, 0, 3, 3, 5, 5, 0, 4)
    assert result[11] == 131
    expected_global = 1/(1+math.exp(-(-32.6659+7.3068*math.log(131+20.5958))))
    expected_na = 1/(1+math.exp(-(-18.8839+4.3979*math.log(132))))
    assert math.isclose(result[12], expected_global, rel_tol=1e-12)
    assert math.isclose(result[13], expected_na, rel_tol=1e-12)
    output = tmp_path / "outputs"
    export_all(
        con, output_directory=output, identity_hash=identity, mode="full",
        mimic_version="synthetic", cohort_manifest=None, preflight=preflight,
        runtime=settings, command_line=["pytest"], specification=SAPSIII_ADAPTED_SPEC,
    )
    assert validate_exports(con, output_directory=output, identity_hash=identity)["valid"]
    exported = con.execute(
        f"SELECT adaptation_version, diagnoses_are_posthoc_proxies, nyha_iv_available, "
        f"saps_iii_complete_case_score, complete_original_saps_iii_available "
        f"FROM read_parquet('{(output / 'scores.parquet').as_posix()}')"
    ).fetchone()
    assert exported == ("saps-iii-adapted-v2", True, False, None, False)
    con.close()


def test_worst_wbc_stay_local_labs_arterial_gases_and_timed_support(
    tmp_path, project_root
) -> None:
    root = tmp_path / "raw"
    data = {relative: [] for relative in RAW_FILES}
    first = datetime(2100, 1, 2)
    second = datetime(2100, 1, 5)
    supported = datetime(2100, 1, 8)
    stay_rows = (
        (1, 10, 100, first),
        (1, 10, 101, second),
        (2, 20, 102, supported),
    )
    for subject_id, hadm_id, stay_id, intime in stay_rows:
        data["icu/icustays.csv.gz"].append(row(
            "icu/icustays.csv.gz", subject_id=subject_id, hadm_id=hadm_id,
            stay_id=stay_id, first_careunit="MICU", last_careunit="MICU",
            intime=ts(intime), outtime=ts(intime, hours=12), los=0.5,
        ))
    for subject_id, hadm_id, intime in ((1, 10, first), (2, 20, supported)):
        data["hosp/admissions.csv.gz"].append(row(
            "hosp/admissions.csv.gz", subject_id=subject_id, hadm_id=hadm_id,
            admittime=ts(intime, hours=-3), dischtime=ts(intime, days=10),
            admission_type="EMERGENCY", admission_location="TRANSFER",
        ))
        data["hosp/patients.csv.gz"].append(row(
            "hosp/patients.csv.gz", subject_id=subject_id, gender="F", anchor_age=60,
            anchor_year=2100, anchor_year_group="2098 - 2102",
        ))

    next_lab_id = 0

    def lab(subject_id, hadm_id, when, specimen_id, itemid, value, valuenum=None):
        nonlocal next_lab_id
        next_lab_id += 1
        data["hosp/labevents.csv.gz"].append(row(
            "hosp/labevents.csv.gz", labevent_id=next_lab_id, subject_id=subject_id,
            hadm_id=hadm_id, specimen_id=specimen_id, itemid=itemid,
            charttime=ts(when), storetime=ts(when), value=str(value),
            valuenum=value if valuenum is None and isinstance(value, (int, float)) else valuenum,
        ))

    # The two WBC values share a hospital admission but belong to different ICU windows.
    lab(1, 10, first, 1, 51301, 10)
    lab(1, 10, first, 2, 51301, 14)
    lab(1, 10, second, 3, 51301, 20)
    # Stay 100 has only a venous gas and must not receive an oxygenation score.
    lab(1, 10, first, 10, 50821, 50)
    lab(1, 10, first, 10, 50820, 7.2)
    lab(1, 10, first, 10, 52033, "VEN.")
    # Stay 101 has an arterial gas, but its only ventilator setting is in the future.
    lab(1, 10, second, 11, 50821, 80)
    lab(1, 10, second, 11, 50816, 50)
    lab(1, 10, second, 11, 52033, "ART.")
    # Stay 102 has support documented before its arterial gas.
    lab(2, 20, supported, 12, 50821, 80)
    lab(2, 20, supported, 12, 50816, 50)
    lab(2, 20, supported, 12, 52033, "ART.")

    def chart(subject_id, hadm_id, stay_id, when, itemid, value, valuenum=None):
        data["icu/chartevents.csv.gz"].append(row(
            "icu/chartevents.csv.gz", subject_id=subject_id, hadm_id=hadm_id,
            stay_id=stay_id, caregiver_id=1, charttime=ts(when), storetime=ts(when),
            itemid=itemid, value=str(value), valuenum=valuenum,
        ))

    chart(1, 10, 100, first, 223762, 34.6, 34.6)  # unknown site makes extrema unavailable
    chart(1, 10, 100, first + timedelta(minutes=30), 223762, 34.0, 34.0)
    chart(1, 10, 100, first + timedelta(minutes=30), 224642, "Oral")
    chart(1, 10, 101, second, 223762, 34.6, 34.6)
    chart(1, 10, 101, second, 224642, "Oral")       # adjusted to 35.1 C
    chart(1, 10, 101, second + timedelta(minutes=30), 223849, "CMV")
    chart(2, 20, 102, supported - timedelta(minutes=30), 223849, "CMV")
    data["icu/inputevents.csv.gz"].append(row(
        "icu/inputevents.csv.gz", subject_id=2, hadm_id=20, stay_id=102,
        starttime=ts(supported, hours=-3), endtime=ts(supported, hours=-1),
        itemid=221906, rate=0, rateuom="mcg/kg/min",
    ))

    write_raw(root, data)
    cohort_file = tmp_path / "cohort.parquet"
    pq.write_table(
        pa.table({"stay_id": pa.array([100, 101, 102], type=pa.int64())}),
        cohort_file,
    )
    preflight = run_preflight(
        project_root=project_root, mimic_root=root, cohort_file=cohort_file,
        mode="full", specification=SAPSIII_ADAPTED_SPEC,
    )
    settings = DuckDBSettings(tmp_path / "score.duckdb", threads=1, memory_limit="1GB")
    con = connect(settings)
    identity = ensure_run_identity(con, identity_payload(preflight, mimic_version="synthetic"))
    build_staging(
        con, mimic_root=root, cohort=inspect_cohort(cohort_file, mode="full"),
        identity_hash=identity, raw_metadata=preflight["raw_sources"],
        profile_directory=tmp_path / "profiles", specification=SAPSIII_ADAPTED_SPEC,
    )
    build_concepts(
        con, concepts=(SAPSIII_ADAPTED_SPEC.score_concept,),
        vendor_root=SAPSIII_ADAPTED_SPEC.vendor_root(project_root), identity_hash=identity,
    )
    observed = con.execute(
        "SELECT stay_id,wbc_max,wbc_score,temp_max,temp_score,ph_min,ph_score,pao2_min,pf_min,"
        "mechanical_ventilation_at_gas_proxy,oxygenation_score,vasoactive_proxy_score "
        "FROM mimiciv_derived.saps_iii_adapted ORDER BY stay_id"
    ).fetchall()
    assert observed == [
        (100, 14.0, 0, None, None, 7.2, 3, None, None, False, None, 0),
        (101, 20.0, 2, 35.1, 0, None, None, 80.0, None, False, 0, 0),
        (102, None, None, None, None, None, None, 80.0, 160.0, True, 7, 0),
    ]
    con.close()
