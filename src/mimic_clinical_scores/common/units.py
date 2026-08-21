"""Fail-closed unit assurance for quantitative score inputs.

MIMIC item IDs identify a clinical variable, but the scoring SQL compares the
numeric value against thresholds expressed in a particular unit.  This module
audits every staged value that can reach one of those comparisons.  Equivalent
spellings are normalized; dimensionally different units are never converted
implicitly.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Iterable

import duckdb

from mimic_clinical_scores.common.duckdb import execute_table_artifact, table_exists
from mimic_clinical_scores.common.provenance import canonical_json_hash
from mimic_clinical_scores.common.specification import ScoreSpecification


UNIT_RULESET_VERSION = "score-input-units-v1"


class UnitValidationError(RuntimeError):
    """Raised when a score-eligible value has a missing or incompatible unit."""


@dataclass(frozen=True)
class UnitRule:
    rule_id: str
    table: str
    item_ids: tuple[int, ...]
    value_column: str
    unit_column: str
    accepted_units: tuple[str, ...]
    expected_dimension: str
    value_predicate: str


def normalize_unit(value: str | None) -> str:
    """Normalize harmless presentation differences, without changing dimensions."""

    if value is None:
        return ""
    normalized = "".join(value.strip().lower().split())
    return (
        normalized.replace("µ", "u")
        .replace("μ", "u")
        .replace("°", "")
        .replace(".", "")
    )


def _rule(
    rule_id: str,
    table: str,
    item_ids: Iterable[int],
    value_column: str,
    unit_column: str,
    accepted_units: Iterable[str],
    expected_dimension: str,
    value_predicate: str,
    *,
    allow_missing: bool = False,
) -> UnitRule:
    normalized = tuple(sorted({normalize_unit(unit) for unit in accepted_units}))
    if not normalized or ("" in normalized and not allow_missing):
        raise ValueError(f"Unit rule {rule_id} must require an explicit unit")
    return UnitRule(
        rule_id=rule_id,
        table=table,
        item_ids=tuple(sorted(set(item_ids))),
        value_column=value_column,
        unit_column=unit_column,
        accepted_units=normalized,
        expected_dimension=expected_dimension,
        value_predicate=value_predicate,
    )


CHART = "mimiciv_icu.chartevents"
LAB = "mimiciv_hosp.labevents"
INPUT = "mimiciv_icu.inputevents"
OUTPUT = "mimiciv_icu.outputevents"

_HR = _rule(
    "heart_rate", CHART, (220045,), "valuenum", "valueuom", ("bpm",),
    "beats/min", "valuenum BETWEEN 0 AND 400",
)
_SBP = _rule(
    "systolic_blood_pressure", CHART, (220050, 220179, 225309),
    "valuenum", "valueuom", ("mmHg", "mm Hg"), "mmHg",
    "valuenum >= 0 AND valuenum < 400",
)
_MAP = _rule(
    "mean_arterial_pressure", CHART, (220052, 220181, 225312),
    "valuenum", "valueuom", ("mmHg", "mm Hg"), "mmHg",
    "valuenum > 0 AND valuenum < 300",
)
_TEMP_F = _rule(
    "temperature_fahrenheit", CHART, (223761,), "valuenum", "valueuom",
    ("F", "deg F", "deg. F", "°F"), "degrees Fahrenheit",
    "valuenum BETWEEN 70 AND 120",
)
_TEMP_C = _rule(
    "temperature_celsius", CHART, (223762,), "valuenum", "valueuom",
    ("C", "deg C", "deg. C", "°C"), "degrees Celsius",
    "valuenum BETWEEN 10 AND 50",
)
_CHART_FIO2 = _rule(
    "charted_fio2", CHART, (223835,), "valuenum", "valueuom", ("", "%"),
    "fraction or percent (fixed item ID plus value-domain normalization)",
    "(valuenum > 0.2 AND valuenum <= 1) OR (valuenum >= 20 AND valuenum <= 100)",
    allow_missing=True,
)
_SPO2 = _rule(
    "oxygen_saturation", CHART, (220277,), "valuenum", "valueuom", ("%",),
    "percent", "valuenum > 0 AND valuenum <= 100",
)
_GCS = _rule(
    "glasgow_coma_scale", CHART, (220739, 223900, 223901),
    "valuenum", "valueuom", ("", "points"), "dimensionless points",
    "valuenum BETWEEN 1 AND 6", allow_missing=True,
)
_LAB_FIO2 = _rule(
    "blood_gas_fio2", LAB, (50816,), "valuenum", "valueuom", ("", "%"),
    "fraction or percent (fixed item ID plus value-domain normalization)",
    "(valuenum > 0.2 AND valuenum <= 1) OR (valuenum > 20 AND valuenum <= 100)",
    allow_missing=True,
)
_PAO2 = _rule(
    "arterial_oxygen_pressure", LAB, (50821,), "valuenum", "valueuom",
    ("mmHg", "mm Hg"), "mmHg", "valuenum IS NOT NULL",
)
_PH = _rule(
    "blood_ph", LAB, (50820,), "valuenum", "valueuom", ("", "units"),
    "dimensionless", "valuenum BETWEEN 6 AND 8", allow_missing=True,
)
_BUN = _rule(
    "blood_urea_nitrogen", LAB, (51006,), "valuenum", "valueuom",
    ("mg/dL",), "mg/dL", "valuenum > 0 AND valuenum <= 300",
)
_BILIRUBIN = _rule(
    "total_bilirubin", LAB, (50885,), "valuenum", "valueuom",
    ("mg/dL",), "mg/dL", "valuenum >= 0",
)
_CREATININE = _rule(
    "creatinine", LAB, (50912,), "valuenum", "valueuom", ("mg/dL",),
    "mg/dL", "valuenum >= 0 AND valuenum <= 150",
)
_WBC = _rule(
    "white_blood_cells", LAB, (51301,), "valuenum", "valueuom",
    ("K/uL", "10^3/uL", "10*3/uL", "10³/uL", "G/L", "10^9/L"),
    "10^3/uL (numerically G/L)",
    "valuenum >= 0",
)
_PLATELET = _rule(
    "platelet_count", LAB, (51265,), "valuenum", "valueuom",
    ("K/uL", "10^3/uL", "10*3/uL", "10³/uL", "G/L", "10^9/L"),
    "10^3/uL (numerically G/L)",
    "valuenum >= 0",
)
_POTASSIUM = _rule(
    "potassium", LAB, (50971,), "valuenum", "valueuom",
    ("mEq/L", "mmol/L"), "mEq/L (numerically mmol/L for K+)",
    "valuenum > 0 AND valuenum <= 30",
)
_SODIUM = _rule(
    "sodium", LAB, (50983,), "valuenum", "valueuom",
    ("mEq/L", "mmol/L"), "mEq/L (numerically mmol/L for Na+)",
    "valuenum > 0 AND valuenum <= 200",
)
_BICARBONATE = _rule(
    "bicarbonate", LAB, (50882,), "valuenum", "valueuom",
    ("mEq/L", "mmol/L"), "mEq/L (numerically mmol/L for HCO3-)",
    "valuenum > 0 AND valuenum <= 10000",
)
_URINE = _rule(
    "urine_output", OUTPUT,
    (226559, 226560, 226561, 226584, 226563, 226564, 226565, 226567,
     226557, 226558, 227488, 227489),
    "value", "valueuom", ("mL",), "mL", "value IS NOT NULL",
)
_DOBUTAMINE = _rule(
    "dobutamine_rate", INPUT, (221653,), "rate", "rateuom",
    ("mcg/kg/min", "ug/kg/min", "µg/kg/min"), "mcg/kg/min", "rate > 0",
)
_DOPAMINE = _rule(
    "dopamine_rate", INPUT, (221662,), "rate", "rateuom",
    ("mcg/kg/min", "ug/kg/min", "µg/kg/min"), "mcg/kg/min", "rate > 0",
)
_EPINEPHRINE = _rule(
    "epinephrine_rate", INPUT, (221289,), "rate", "rateuom",
    ("mcg/kg/min", "ug/kg/min", "µg/kg/min"), "mcg/kg/min", "rate > 0",
)
_NOREPINEPHRINE = _rule(
    "norepinephrine_rate", INPUT, (221906,), "rate", "rateuom",
    ("mcg/kg/min", "ug/kg/min", "µg/kg/min", "mg/kg/min"),
    "mcg/kg/min (mg/kg/min is explicitly converted by the pinned concept)",
    "rate > 0",
)

_SAPS_II_RULES = (
    _HR, _SBP, _TEMP_F, _TEMP_C, _CHART_FIO2, _SPO2, _LAB_FIO2, _PAO2,
    _GCS, _BUN, _WBC, _POTASSIUM, _SODIUM, _BICARBONATE, _BILIRUBIN, _URINE,
)
_SAPS_III_RULES = (
    _HR, _SBP, _TEMP_F, _TEMP_C, _CHART_FIO2, _LAB_FIO2, _PAO2,
    _GCS, _PH, _BILIRUBIN, _CREATININE, _WBC, _PLATELET,
    _DOBUTAMINE, _DOPAMINE, _EPINEPHRINE, _NOREPINEPHRINE,
)
_SOFA_RULES = (
    _MAP, _CHART_FIO2, _SPO2, _LAB_FIO2, _PAO2, _GCS, _BILIRUBIN,
    _CREATININE, _PLATELET, _URINE,
    _DOBUTAMINE, _DOPAMINE, _EPINEPHRINE, _NOREPINEPHRINE,
)

_RULES_BY_SCORE = {
    "saps_ii": _SAPS_II_RULES,
    "saps_iii_adapted": _SAPS_III_RULES,
    "sofa_first_day_adapted": _SOFA_RULES,
    "sofa_8h_all_stay": _SOFA_RULES,
    "sofa_hourly_14d": _SOFA_RULES,
    "sofa_hourly_reverse_7d": _SOFA_RULES,
}


def unit_rules_for(specification: ScoreSpecification) -> tuple[UnitRule, ...]:
    try:
        return _RULES_BY_SCORE[specification.name]
    except KeyError as exc:
        raise UnitValidationError(
            f"No quantitative unit rules declared for score {specification.name!r}"
        ) from exc


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _normalized_unit_sql(column: str) -> str:
    return (
        "lower(replace(replace(replace(replace("
        f"regexp_replace(trim(coalesce({column}, '')), '[[:space:]]+', '', 'g'), "
        "'µ', 'u'), 'μ', 'u'), '°', ''), '.', ''))"
    )


def _rule_select(rule: UnitRule) -> str:
    item_ids = ", ".join(str(item_id) for item_id in rule.item_ids)
    accepted = ", ".join(_literal(unit) for unit in rule.accepted_units)
    normalized = _normalized_unit_sql(rule.unit_column)
    return f"""
        SELECT
          {_literal(rule.rule_id)} AS rule_id,
          {_literal(rule.table)} AS table_name,
          {_literal(json.dumps(rule.item_ids))} AS item_ids_json,
          {_literal(rule.expected_dimension)} AS expected_unit,
          {_literal(json.dumps(rule.accepted_units))} AS accepted_units_json,
          CASE WHEN COUNT(src.itemid) = 0 THEN '<no rows>'
               WHEN src.normalized_unit = '' THEN '<missing>'
               ELSE src.normalized_unit END AS observed_unit,
          COUNT(src.itemid)::BIGINT AS observed_rows,
          CASE WHEN COUNT(src.itemid) = 0 THEN TRUE
               ELSE src.normalized_unit IN ({accepted}) END AS unit_accepted
        FROM (SELECT 1) seed
        LEFT JOIN (
          SELECT itemid, {normalized} AS normalized_unit
          FROM {rule.table}
          WHERE itemid IN ({item_ids}) AND ({rule.value_predicate})
        ) src ON TRUE
        GROUP BY src.normalized_unit
    """


def unit_validation_sql(specification: ScoreSpecification) -> str:
    selects = [_rule_select(rule) for rule in unit_rules_for(specification)]
    return (
        "CREATE TABLE pipeline_meta.unit_validation AS\n"
        + "\nUNION ALL\n".join(selects)
    )


def _assert_unit_validation(connection: duckdb.DuckDBPyConnection) -> None:
    invalid = connection.execute(
        """
        SELECT rule_id, table_name, observed_unit, observed_rows, expected_unit
        FROM pipeline_meta.unit_validation
        WHERE NOT unit_accepted
        ORDER BY observed_rows DESC, rule_id, observed_unit
        """
    ).fetchall()
    if invalid:
        examples = "; ".join(
            f"{rule} observed {unit!r} on {count} row(s), expected {expected}"
            for rule, _table, unit, count, expected in invalid[:8]
        )
        raise UnitValidationError(
            "Score-eligible staged values have missing or incompatible units: " + examples
        )


def build_unit_validation(
    connection: duckdb.DuckDBPyConnection,
    *,
    specification: ScoreSpecification,
    identity_hash: str,
) -> dict[str, object]:
    """Create the immutable unit audit artifact and reject incompatible inputs."""

    rules = unit_rules_for(specification)
    rules_payload = {
        "version": UNIT_RULESET_VERSION,
        "score": specification.name,
        "rules": [asdict(rule) for rule in rules],
    }
    row_count, elapsed, resumed = execute_table_artifact(
        connection,
        artifact_name="validation:score_input_units",
        artifact_type="validation",
        qualified_table="pipeline_meta.unit_validation",
        identity_hash=identity_hash,
        artifact_hash=canonical_json_hash(rules_payload),
        sql=unit_validation_sql(specification),
        details=rules_payload,
        after_create=_assert_unit_validation,
    )
    # Recheck even on resume, so concepts cannot follow a corrupted audit table.
    _assert_unit_validation(connection)
    return {"rules": len(rules), "observations": row_count, "elapsed": elapsed, "resumed": resumed}


def require_unit_validation(
    connection: duckdb.DuckDBPyConnection, *, identity_hash: str
) -> None:
    """Refuse tracked concept execution without this run's passing unit audit."""

    if not table_exists(connection, "pipeline_meta.unit_validation"):
        raise UnitValidationError(
            "Unit validation is missing; run build-staging before score concepts"
        )
    row = connection.execute(
        """
        SELECT identity_hash FROM pipeline_meta.artifacts
        WHERE artifact_name = 'validation:score_input_units'
        """
    ).fetchone()
    if row is None or row[0] != identity_hash:
        raise UnitValidationError("Unit validation does not belong to this run identity")
    _assert_unit_validation(connection)


def unit_validation_statistics(connection: duckdb.DuckDBPyConnection) -> dict[str, object]:
    if not table_exists(connection, "pipeline_meta.unit_validation"):
        raise UnitValidationError("Unit validation audit table is missing")
    columns = (
        "rule_id", "table_name", "item_ids", "expected_unit", "accepted_units",
        "observed_unit", "observed_rows", "unit_accepted",
    )
    rows = connection.execute(
        """
        SELECT rule_id, table_name, item_ids_json, expected_unit,
               accepted_units_json, observed_unit, observed_rows, unit_accepted
        FROM pipeline_meta.unit_validation
        ORDER BY rule_id, observed_unit
        """
    ).fetchall()
    observations = []
    for row in rows:
        record = dict(zip(columns, row, strict=True))
        record["item_ids"] = json.loads(record["item_ids"])
        record["accepted_units"] = json.loads(record["accepted_units"])
        observations.append(record)
    return {
        "ruleset_version": UNIT_RULESET_VERSION,
        "invalid_observation_rows": sum(
            int(record["observed_rows"])
            for record in observations if not record["unit_accepted"]
        ),
        "observations": observations,
    }
