from __future__ import annotations

import importlib.util
from pathlib import Path

import duckdb


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "audit_sofa_respiratory_missingness.py"
SPEC = importlib.util.spec_from_file_location("audit_sofa_respiratory_missingness", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_identifier_free_respiratory_cause_audit(tmp_path) -> None:
    database = tmp_path / "audit.duckdb"
    con = duckdb.connect(str(database))
    con.execute("CREATE SCHEMA mimiciv_icu; CREATE SCHEMA mimiciv_derived")
    con.execute(
        """
        CREATE TABLE mimiciv_icu.icustays(
          subject_id INTEGER, stay_id INTEGER, intime TIMESTAMP, outtime TIMESTAMP
        );
        INSERT INTO mimiciv_icu.icustays VALUES
          (1, 101, '2100-01-01', '2100-01-03'),
          (2, 102, '2100-01-01', '2100-01-01 12:00:00'),
          (3, 103, '2100-01-01', '2100-01-03'),
          (4, 104, '2100-01-01', NULL);
        CREATE TABLE mimiciv_derived.bg(
          subject_id INTEGER, charttime TIMESTAMP, po2 DOUBLE, fio2 DOUBLE,
          fio2_chartevents DOUBLE, pao2fio2ratio DOUBLE
        );
        INSERT INTO mimiciv_derived.bg VALUES
          (2, '2100-01-01 01:00:00', 80, NULL, NULL, NULL),
          (3, '2100-01-01 01:00:00', 80, NULL, 40, 200),
          (4, '2100-01-01 01:00:00', 100, 50, NULL, 200);
        CREATE TABLE mimiciv_derived.ventilation(
          stay_id INTEGER, starttime TIMESTAMP, endtime TIMESTAMP,
          ventilation_status VARCHAR
        );
        INSERT INTO mimiciv_derived.ventilation VALUES
          (103, '2100-01-01', '2100-01-01 02:00:00', 'InvasiveVent');
        CREATE TABLE mimiciv_derived.sofa_first_day_adapted(
          stay_id INTEGER, respiration_score INTEGER
        );
        INSERT INTO mimiciv_derived.sofa_first_day_adapted VALUES
          (101, NULL), (102, NULL), (103, 3), (104, 2);
        CREATE TABLE mimiciv_icu.chartevents(
          subject_id INTEGER, charttime TIMESTAMP, itemid INTEGER, valuenum DOUBLE
        );
        INSERT INTO mimiciv_icu.chartevents VALUES
          (3, '2100-01-01 00:30:00', 223835, 40);
        """
    )
    con.close()

    result = MODULE.audit(database)
    overall = result["sections"]["overall"]["all"]
    assert overall["cohort_rows"] == 4
    assert overall["respiration_missing"]["count"] == 2
    assert overall["missing_cause_among_missing"]["no_pao2_in_window"]["count"] == 1
    assert overall["missing_cause_among_missing"]["pao2_present_without_valid_fio2"]["count"] == 1
    assert overall["component_observed_without_valid_pf_internal_inconsistency"] == 0
    assert result["sections"]["invasive_ventilation_in_window"]["yes"][
        "respiration_observed"
    ]["count"] == 1
    age = result["charted_fio2_fallback_age"]
    assert age["matched_stay_gas_pairs"] == 1
    assert age["age_distribution"][0]["age_group"] == "15_to_60_minutes"
    assert result["privacy"].startswith("Aggregate audit only")
