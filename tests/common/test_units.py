from __future__ import annotations

import pytest

from mimic_clinical_scores.common.duckdb import DuckDBSettings, connect
from mimic_clinical_scores.common.units import (
    UnitValidationError,
    build_unit_validation,
    normalize_unit,
    require_unit_validation,
    unit_rules_for,
    unit_validation_statistics,
)
from mimic_clinical_scores.scores.saps_ii.specification import SAPSII_SPEC
from mimic_clinical_scores.scores.saps_iii_adapted.specification import (
    SAPSIII_ADAPTED_SPEC,
)
from mimic_clinical_scores.scores.sofa_first_day_adapted.specification import (
    SOFA_FIRST_DAY_ADAPTED_SPEC,
)
from mimic_clinical_scores.scores.sofa_hourly_14d.specification import SOFA_HOURLY_14D_SPEC
from mimic_clinical_scores.scores.sofa_hourly_reverse_7d.specification import (
    SOFA_HOURLY_REVERSE_7D_SPEC,
)


def _unit_database(tmp_path, name):
    con = connect(DuckDBSettings(tmp_path / f"{name}.duckdb", threads=1, memory_limit="1GB"))
    con.execute(
        "CREATE TABLE mimiciv_icu.chartevents "
        "(itemid INTEGER, valuenum DOUBLE, valueuom VARCHAR)"
    )
    con.execute(
        "CREATE TABLE mimiciv_hosp.labevents "
        "(itemid INTEGER, valuenum DOUBLE, valueuom VARCHAR)"
    )
    con.execute(
        "CREATE TABLE mimiciv_icu.inputevents "
        "(itemid INTEGER, rate DOUBLE, rateuom VARCHAR)"
    )
    con.execute(
        "CREATE TABLE mimiciv_icu.outputevents "
        "(itemid INTEGER, value DOUBLE, valueuom VARCHAR)"
    )
    return con


def test_unit_normalization_changes_spelling_not_dimension() -> None:
    assert normalize_unit(" mm Hg ") == "mmhg"
    assert normalize_unit("Deg. C") == "degc"
    assert normalize_unit("µg / kg / min") == "ug/kg/min"
    assert normalize_unit("mg/dL") != normalize_unit("µmol/L")


def test_all_scores_declare_units_for_their_quantitative_inputs() -> None:
    assert len(unit_rules_for(SAPSII_SPEC)) == 16
    assert len(unit_rules_for(SAPSIII_ADAPTED_SPEC)) == 17
    assert len(unit_rules_for(SOFA_FIRST_DAY_ADAPTED_SPEC)) == 14
    assert len(unit_rules_for(SOFA_HOURLY_14D_SPEC)) == 14
    assert len(unit_rules_for(SOFA_HOURLY_REVERSE_7D_SPEC)) == 14
    specifications = (
        SAPSII_SPEC,
        SAPSIII_ADAPTED_SPEC,
        SOFA_FIRST_DAY_ADAPTED_SPEC,
        SOFA_HOURLY_14D_SPEC,
        SOFA_HOURLY_REVERSE_7D_SPEC,
    )
    for specification in specifications:
        rules = unit_rules_for(specification)
        assert all(rule.accepted_units for rule in rules)
        assert all(
            "" not in rule.accepted_units
            or rule.expected_dimension.startswith("dimensionless")
            or "fixed item ID" in rule.expected_dimension
            for rule in rules
        )
        for rule in rules:
            assert set(rule.item_ids) <= specification.item_ids(rule.table)

    urine_ids = {
        226559, 226560, 226561, 226584, 226563, 226564,
        226565, 226567, 226557, 226558, 227488, 227489,
    }
    expected = {
        "saps_ii": {
            "mimiciv_icu.chartevents": {
                220045, 220050, 220179, 225309, 223761, 223762,
                223835, 220277, 220739, 223900, 223901,
            },
            "mimiciv_hosp.labevents": {
                50816, 50821, 51006, 51301, 50971, 50983, 50882, 50885,
            },
            "mimiciv_icu.outputevents": urine_ids,
        },
        "saps_iii_adapted": {
            "mimiciv_icu.chartevents": {
                220045, 220050, 220179, 225309, 223761, 223762,
                223835, 220739, 223900, 223901,
            },
            "mimiciv_hosp.labevents": {
                50816, 50820, 50821, 50885, 50912, 51265, 51301,
            },
            "mimiciv_icu.inputevents": {221289, 221653, 221662, 221906},
        },
        "sofa_first_day_adapted": {
            "mimiciv_icu.chartevents": {
                220052, 220181, 225312, 223835, 220277,
                220739, 223900, 223901,
            },
            "mimiciv_hosp.labevents": {50816, 50821, 50885, 50912, 51265},
            "mimiciv_icu.inputevents": {221289, 221653, 221662, 221906},
            "mimiciv_icu.outputevents": urine_ids,
        },
    }
    expected["sofa_hourly_14d"] = expected["sofa_first_day_adapted"]
    expected["sofa_hourly_reverse_7d"] = expected["sofa_first_day_adapted"]
    for specification in specifications:
        actual: dict[str, set[int]] = {}
        for rule in unit_rules_for(specification):
            actual.setdefault(rule.table, set()).update(rule.item_ids)
        assert actual == expected[specification.name]


def test_equivalent_unit_spellings_and_numeric_equivalents_are_accepted(tmp_path) -> None:
    con = _unit_database(tmp_path, "accepted")
    con.executemany(
        "INSERT INTO mimiciv_icu.chartevents VALUES (?, ?, ?)",
        [
            (220045, 80, " BPM "),
            (220179, 110, "mm Hg"),
            (223761, 98.6, "Deg. F"),
            (223762, 37, "°C"),
            (223835, 0.5, "%"),
            (220277, 98, "%"),
        ],
    )
    con.executemany(
        "INSERT INTO mimiciv_hosp.labevents VALUES (?, ?, ?)",
        [
            (50816, 50, "%"),
            (50821, 80, "mm Hg"),
            (51006, 20, "mg/dL"),
            (50885, 1, "mg/dL"),
            (51301, 10, "G/L"),
            (50971, 4, "mmol/L"),
            (50983, 140, "mmol/L"),
            (50882, 24, "mmol/L"),
        ],
    )
    con.execute("INSERT INTO mimiciv_icu.outputevents VALUES (226559, 200, 'mL')")
    result = build_unit_validation(
        con, specification=SAPSII_SPEC, identity_hash="accepted-identity"
    )
    stats = unit_validation_statistics(con)
    assert result["rules"] == 16
    assert stats["invalid_observation_rows"] == 0
    assert all(row["unit_accepted"] for row in stats["observations"])
    require_unit_validation(con, identity_hash="accepted-identity")
    con.close()


@pytest.mark.parametrize(
    ("table", "values", "message"),
    [
        ("mimiciv_icu.chartevents", (220045, 80, None), "heart_rate observed '<missing>'"),
        ("mimiciv_hosp.labevents", (50885, 18, "µmol/L"), "total_bilirubin observed 'umol/l'"),
        ("mimiciv_hosp.labevents", (50821, 10.7, "kPa"), "arterial_oxygen_pressure observed 'kpa'"),
    ],
)
def test_missing_or_dimensionally_different_units_fail_closed(
    tmp_path, table, values, message
) -> None:
    con = _unit_database(tmp_path, message.split()[0])
    con.execute(f"INSERT INTO {table} VALUES (?, ?, ?)", values)
    with pytest.raises(UnitValidationError, match=message):
        build_unit_validation(con, specification=SAPSII_SPEC, identity_hash="bad-identity")
    assert not con.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema='pipeline_meta' AND table_name='unit_validation'"
    ).fetchone()[0]
    con.close()


def test_only_norepinephrine_allows_the_pinned_milligram_rate_conversion(tmp_path) -> None:
    accepted = _unit_database(tmp_path, "norepi_mg")
    accepted.execute(
        "INSERT INTO mimiciv_icu.inputevents VALUES (221906, 0.0001, 'mg/kg/min')"
    )
    build_unit_validation(
        accepted,
        specification=SOFA_FIRST_DAY_ADAPTED_SPEC,
        identity_hash="norepi-identity",
    )
    assert unit_validation_statistics(accepted)["invalid_observation_rows"] == 0
    accepted.close()

    rejected = _unit_database(tmp_path, "dopamine_mg")
    rejected.execute(
        "INSERT INTO mimiciv_icu.inputevents VALUES (221662, 0.005, 'mg/kg/min')"
    )
    with pytest.raises(UnitValidationError, match="dopamine_rate"):
        build_unit_validation(
            rejected,
            specification=SOFA_FIRST_DAY_ADAPTED_SPEC,
            identity_hash="dopamine-identity",
        )
    rejected.close()


def test_tracked_execution_requires_a_passing_unit_audit(tmp_path) -> None:
    con = _unit_database(tmp_path, "missing_audit")
    with pytest.raises(UnitValidationError, match="Unit validation is missing"):
        require_unit_validation(con, identity_hash="identity")
    con.close()


def test_missing_unit_is_expected_for_dimensionless_gcs_and_ph(tmp_path) -> None:
    con = _unit_database(tmp_path, "dimensionless")
    con.execute("INSERT INTO mimiciv_icu.chartevents VALUES (220739, 4, NULL)")
    con.execute("INSERT INTO mimiciv_hosp.labevents VALUES (50820, 7.4, NULL)")
    build_unit_validation(
        con, specification=SAPSIII_ADAPTED_SPEC, identity_hash="dimensionless-identity"
    )
    rows = {
        row["rule_id"]: row
        for row in unit_validation_statistics(con)["observations"]
        if row["rule_id"] in {"glasgow_coma_scale", "blood_ph"}
    }
    assert rows["glasgow_coma_scale"]["observed_unit"] == "<missing>"
    assert rows["glasgow_coma_scale"]["unit_accepted"]
    assert rows["blood_ph"]["observed_unit"] == "<missing>"
    assert rows["blood_ph"]["unit_accepted"]
    con.close()


def test_blank_fio2_unit_uses_fixed_item_semantics_and_valid_numeric_domain(tmp_path) -> None:
    con = _unit_database(tmp_path, "fio2_item_semantics")
    con.executemany(
        "INSERT INTO mimiciv_icu.chartevents VALUES (223835, ?, NULL)",
        [(0.2,), (0.21,), (1,), (5,), (20,), (100,), (101,)],
    )
    con.executemany(
        "INSERT INTO mimiciv_hosp.labevents VALUES (50816, ?, NULL)",
        [(0.2,), (0.21,), (1,), (5,), (20,), (21,), (100,), (101,)],
    )
    build_unit_validation(
        con, specification=SAPSII_SPEC, identity_hash="fio2-item-identity"
    )
    rows = {
        row["rule_id"]: row
        for row in unit_validation_statistics(con)["observations"]
        if row["rule_id"] in {"charted_fio2", "blood_gas_fio2"}
    }
    # Chart 20 follows the pinned MIT chart-domain branch; lab 20 does not.
    assert rows["charted_fio2"]["observed_rows"] == 4
    assert rows["blood_gas_fio2"]["observed_rows"] == 4
    assert rows["charted_fio2"]["observed_unit"] == "<missing>"
    assert rows["blood_gas_fio2"]["observed_unit"] == "<missing>"
    assert rows["charted_fio2"]["unit_accepted"]
    assert rows["blood_gas_fio2"]["unit_accepted"]
    con.close()
