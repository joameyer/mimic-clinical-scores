"""Null-safe exact reference-versus-optimized result comparison."""

from __future__ import annotations

from typing import Iterable

import duckdb

from mimic_clinical_scores.scores.saps_ii.specification import COMPONENT_COLUMNS


OFFICIAL_COMPARISON_COLUMNS = (
    "subject_id", "hadm_id", "stay_id", "starttime", "endtime", "sapsii", "sapsii_prob",
    *COMPONENT_COLUMNS,
)


def _rows(
    connection: duckdb.DuckDBPyConnection, stay_ids: Iterable[int]
) -> list[tuple[object, ...]]:
    values = sorted(set(int(value) for value in stay_ids))
    if not values:
        raise ValueError("No stay IDs supplied for equivalence comparison")
    ids = ", ".join(str(value) for value in values)
    columns = ", ".join(OFFICIAL_COMPARISON_COLUMNS)
    return connection.execute(
        f"SELECT {columns} FROM mimiciv_derived.sapsii WHERE stay_id IN ({ids}) ORDER BY stay_id"
    ).fetchall()


def assert_reference_equivalent(
    reference: duckdb.DuckDBPyConnection,
    optimized: duckdb.DuckDBPyConnection,
    stay_ids: Iterable[int],
) -> None:
    reference_rows = _rows(reference, stay_ids)
    optimized_rows = _rows(optimized, stay_ids)
    if len(reference_rows) != len(optimized_rows):
        raise AssertionError(
            f"Reference has {len(reference_rows)} rows; optimized has {len(optimized_rows)}"
        )
    for row_index, (expected, actual) in enumerate(zip(reference_rows, optimized_rows, strict=True)):
        for column_index, (left, right) in enumerate(zip(expected, actual, strict=True)):
            if left != right and not (left is None and right is None):
                column = OFFICIAL_COMPARISON_COLUMNS[column_index]
                raise AssertionError(
                    f"Mismatch at row {row_index}, column {column}: reference={left!r}, optimized={right!r}"
                )

