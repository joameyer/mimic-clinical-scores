from __future__ import annotations

import csv
import gzip
from datetime import datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mimic_clinical_scores.common.staging import RAW_SCHEMAS


RAW_FILES = tuple(RAW_SCHEMAS)


def ts(base: datetime, **delta: float) -> str:
    return (base + timedelta(**delta)).strftime("%Y-%m-%d %H:%M:%S")


def row(relative: str, **values: object) -> dict[str, object]:
    return {name: values.get(name) for name, _ in RAW_SCHEMAS[relative]}


def write_raw(root: Path, rows: dict[str, list[dict[str, object]]]) -> None:
    for relative in RAW_FILES:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = [name for name, _ in RAW_SCHEMAS[relative]]
        with gzip.open(path, "wt", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows[relative])


@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def synthetic_mimic(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    root = tmp_path_factory.mktemp("synthetic_mimic")
    data: dict[str, list[dict[str, object]]] = {relative: [] for relative in RAW_FILES}
    bases = {
        1001: datetime(2100, 1, 1),
        1002: datetime(2100, 1, 3),
        1003: datetime(2100, 2, 1),
        1004: datetime(2100, 3, 1),
        1005: datetime(2100, 4, 1),
        1006: datetime(2100, 5, 1),
    }
    stays = (
        (1, 11, 1001, 30.0),
        (1, 11, 1002, 5.0),
        (2, 22, 1003, 24.0),
        (3, 33, 1004, 12.0),
        (4, 44, 1005, 30.0),
        (5, 55, 1006, None),
    )
    for subject, hadm, stay, hours in stays:
        base = bases[stay]
        data["icu/icustays.csv.gz"].append(
            row(
                "icu/icustays.csv.gz",
                subject_id=subject,
                hadm_id=hadm,
                stay_id=stay,
                first_careunit="MICU",
                last_careunit="MICU",
                intime=ts(base),
                outtime=ts(base, hours=hours) if hours is not None else None,
                los=hours / 24 if hours is not None else None,
            )
        )

    admissions = (
        (1, 11, "EMERGENCY"),
        (2, 22, "ELECTIVE"),
        (3, 33, "EMERGENCY"),
        (4, 44, "EMERGENCY"),
        (5, 55, "EMERGENCY"),
    )
    service = {11: "MED", 22: "CSURG", 33: "TSURG", 44: "MED", 55: "MED"}
    for subject, hadm, admission_type in admissions:
        base = next(bases[stay] for s, h, stay, _ in stays if h == hadm)
        data["hosp/admissions.csv.gz"].append(
            row(
                "hosp/admissions.csv.gz",
                subject_id=subject,
                hadm_id=hadm,
                admittime=ts(base, hours=-6),
                dischtime=ts(base, days=5),
                admission_type=admission_type,
                admission_location="EMERGENCY ROOM",
                discharge_location="HOME",
                insurance="Other",
                race="OTHER",
                hospital_expire_flag=0,
            )
        )
        data["hosp/services.csv.gz"].append(
            row(
                "hosp/services.csv.gz",
                subject_id=subject,
                hadm_id=hadm,
                transfertime=ts(base, hours=-5),
                curr_service=service[hadm],
            )
        )
        data["hosp/patients.csv.gz"].append(
            row(
                "hosp/patients.csv.gz",
                subject_id=subject,
                gender="F" if subject % 2 else "M",
                anchor_age=49 + subject,
                anchor_year=2100,
                anchor_year_group="2098 - 2102",
            )
        )

    for seq, (hadm, code, version) in enumerate(
        ((11, "B20", 10), (22, "C81", 10), (33, "C77", 10)), start=1
    ):
        subject = next(subject for subject, h, _ in admissions if h == hadm)
        data["hosp/diagnoses_icd.csv.gz"].append(
            row(
                "hosp/diagnoses_icd.csv.gz",
                subject_id=subject,
                hadm_id=hadm,
                seq_num=seq,
                icd_code=code,
                icd_version=version,
            )
        )

    chart_id = 0

    def chart(stay: int, itemid: int, hour: float, value: object, valuenum: float | None = None) -> None:
        nonlocal chart_id
        chart_id += 1
        subject, hadm = next((s, h) for s, h, st, _ in stays if st == stay)
        charttime = bases[stay] + timedelta(hours=hour)
        data["icu/chartevents.csv.gz"].append(
            row(
                "icu/chartevents.csv.gz",
                subject_id=subject,
                hadm_id=hadm,
                stay_id=stay,
                caregiver_id=9,
                charttime=charttime.strftime("%Y-%m-%d %H:%M:%S"),
                storetime=(charttime + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
                itemid=itemid,
                value=value,
                valuenum=valuenum,
                valueuom="",
                warning=0,
            )
        )

    chart(1001, 220045, -0.001, "45", 45)
    chart(1001, 220045, 0, "250", 250)
    chart(1001, 220045, 1, "35", 35)
    chart(1001, 220045, 2, "170", 170)
    chart(1001, 220045, 24, "80", 80)
    chart(1001, 220045, 24.001, "10", 10)
    chart(1001, 220179, 1, "60", 60)
    chart(1001, 223762, 24, "40", 40)
    chart(1001, 223901, -1, "6 Spontaneously", 6)
    chart(1001, 223900, -1, "4 Confused", 4)
    chart(1001, 220739, -1, "4 Spontaneously", 4)
    chart(1001, 223901, 1, "5 Localizes Pain", 5)
    chart(1001, 223900, 2, "5 Oriented", 5)
    chart(1001, 220739, 3, "4 Spontaneously", 4)
    chart(1001, 223849, -1, "CMV")
    chart(1001, 223849, 10, "CMV")
    chart(1001, 223849, 20, "CMV")
    chart(1001, 223835, 11, "50", 50)

    chart(1002, 220045, 1, "80", 80)
    chart(1002, 220179, 1, "110", 110)
    chart(1002, 223762, 1, "37", 37)
    chart(1002, 223901, 1, "6", 6)
    chart(1002, 223900, 1, "5", 5)
    chart(1002, 220739, 1, "4", 4)

    for stay in (1003, 1004):
        chart(stay, 220045, 1, "80", 80)
        chart(stay, 220179, 1, "110", 110)
        chart(stay, 223762, 1, "37", 37)
        chart(stay, 223901, 1, "6", 6)
        chart(stay, 223900, 1, "5", 5)
        chart(stay, 220739, 1, "4", 4)

    chart(1004, 226732, 2, "CPAP mask ")
    chart(1004, 223834, 2, "10", 10)
    chart(1004, 226732, 3, "CPAP mask ")
    chart(1004, 223834, 3, "10", 10)

    chart(1005, 223849, 23, "CMV")
    chart(1005, 223835, 23.5, "40", 40)
    chart(1005, 223849, 25, "CMV")
    chart(1005, 220045, 1, "90", 90)
    chart(1005, 220179, 1, "120", 120)
    chart(1005, 223762, 1, "36", 36)

    lab_id = 0

    def lab(
        subject: int,
        hadm: int,
        itemid: int,
        charttime: datetime,
        value: object,
        *,
        specimen_id: int | None = None,
        valuenum: float | None = None,
    ) -> None:
        nonlocal lab_id
        lab_id += 1
        data["hosp/labevents.csv.gz"].append(
            row(
                "hosp/labevents.csv.gz",
                labevent_id=lab_id,
                subject_id=subject,
                hadm_id=hadm,
                specimen_id=specimen_id or lab_id,
                itemid=itemid,
                charttime=charttime.strftime("%Y-%m-%d %H:%M:%S"),
                storetime=(charttime + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"),
                value=value,
                valuenum=valuenum,
                valueuom="",
                priority="ROUTINE",
            )
        )

    b1 = bases[1001]
    lab(1, 11, 51006, b1, 200, valuenum=200)
    lab(1, 11, 51006, b1 + timedelta(hours=1), 20, valuenum=20)
    lab(1, 11, 51006, b1 + timedelta(hours=2), 90, valuenum=90)
    lab(1, 11, 51006, b1 + timedelta(hours=3), 30, valuenum=30)
    lab(1, 11, 50971, b1 + timedelta(hours=1), 5.5, valuenum=5.5)
    lab(1, 11, 50983, b1 + timedelta(hours=24), 150, valuenum=150)
    lab(1, 11, 50971, b1 + timedelta(hours=24, seconds=1), 9, valuenum=9)
    lab(1, 11, 50882, b1 + timedelta(hours=1), 14, valuenum=14)
    lab(1, 11, 51301, b1 + timedelta(hours=1), 25, valuenum=25)
    lab(1, 11, 51301, b1 + timedelta(hours=2), 0.5, valuenum=0.5)
    lab(1, 11, 50885, b1 + timedelta(hours=1), 7, valuenum=7)
    gas_time = b1 + timedelta(hours=12)
    lab(1, 11, 52033, gas_time, "ART.", specimen_id=9001)
    lab(1, 11, 50821, gas_time, 80, specimen_id=9001, valuenum=80)

    b2 = bases[1002]
    lab(1, 11, 52033, b2 + timedelta(hours=2), "ART.", specimen_id=9002)
    lab(1, 11, 50821, b2 + timedelta(hours=2), 70, specimen_id=9002, valuenum=70)
    lab(1, 11, 51006, b2 + timedelta(hours=8), 90, valuenum=90)

    b4 = bases[1004]
    lab(3, 33, 52033, b4 + timedelta(hours=2, minutes=30), "ART.", specimen_id=9004)
    lab(3, 33, 50821, b4 + timedelta(hours=2, minutes=30), 90, specimen_id=9004, valuenum=90)
    lab(3, 33, 50816, b4 + timedelta(hours=2, minutes=30), 50, specimen_id=9004, valuenum=50)

    b5 = bases[1005]
    lab(4, 44, 52033, b5 + timedelta(hours=24), "ART.", specimen_id=9005)
    lab(4, 44, 50821, b5 + timedelta(hours=24), 60, specimen_id=9005, valuenum=60)

    output_id = 0

    def output(stay: int, itemid: int, hour: float, value: float) -> None:
        nonlocal output_id
        output_id += 1
        subject, hadm = next((s, h) for s, h, st, _ in stays if st == stay)
        when = bases[stay] + timedelta(hours=hour)
        data["icu/outputevents.csv.gz"].append(
            row(
                "icu/outputevents.csv.gz",
                subject_id=subject,
                hadm_id=hadm,
                stay_id=stay,
                caregiver_id=7,
                charttime=when.strftime("%Y-%m-%d %H:%M:%S"),
                storetime=(when + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S"),
                itemid=itemid,
                value=value,
                valueuom="ml",
            )
        )

    output(1001, 226559, 1, 200)
    output(1001, 226560, 2, 300)
    output(1001, 226559, 24, 0)
    output(1001, 226559, 24.001, 9999)
    output(1003, 226559, 1, 1200)

    write_raw(root, data)
    stay_ids = tuple(stay for _, _, stay, _ in stays)
    cohort_file = root / "cohort.parquet"
    pq.write_table(pa.table({"stay_id": pa.array(stay_ids, type=pa.int64())}), cohort_file)
    return {"root": root, "cohort_file": cohort_file, "stay_ids": stay_ids, "rows": data}
