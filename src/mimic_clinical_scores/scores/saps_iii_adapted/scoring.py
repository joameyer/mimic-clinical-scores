"""Export projections for SAPS III adapted; raw proxy/audit fields remain visible."""

from mimic_clinical_scores.scores.saps_iii_adapted.specification import COMPONENT_COLUMNS


TABLE = "mimiciv_derived.saps_iii_adapted"


def scores_projection_sql() -> str:
    return f"SELECT * FROM {TABLE} ORDER BY stay_id"


def missingness_projection_sql() -> str:
    indicators = ",\n            ".join(
        f"{column} IS NULL AS {column}_missing" for column in COMPONENT_COLUMNS
    )
    count = " + ".join(f"CAST({column} IS NULL AS INTEGER)" for column in COMPONENT_COLUMNS)
    return f"""
        SELECT stay_id,
            {indicators},
            {count} AS number_of_missing_components,
            ({count}) = 0 AS complete_components
        FROM {TABLE}
        ORDER BY stay_id
    """
