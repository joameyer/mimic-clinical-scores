"""Execute immutable upstream concepts in their declared dependency order."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import duckdb

from mimic_clinical_scores.common.duckdb import execute_table_artifact, table_exists
from mimic_clinical_scores.common.provenance import sha256_file
from mimic_clinical_scores.common.specification import Concept
from mimic_clinical_scores.common.units import require_unit_validation


class ConceptError(RuntimeError):
    """Raised when an official concept dependency is unavailable."""


def build_concepts(
    connection: duckdb.DuckDBPyConnection,
    *,
    concepts: Iterable[Concept],
    vendor_root: Path,
    identity_hash: str,
) -> list[dict[str, object]]:
    require_unit_validation(connection, identity_hash=identity_hash)
    results: list[dict[str, object]] = []
    for concept in concepts:
        path = vendor_root / concept.sql_relative_path
        sql_hash = sha256_file(path)
        row_count, elapsed, resumed = execute_table_artifact(
            connection,
            artifact_name=f"concept:{concept.name}",
            artifact_type="concept",
            qualified_table=concept.output_table,
            identity_hash=identity_hash,
            artifact_hash=sql_hash,
            sql=path.read_text(encoding="utf-8"),
            details={"sql_path": concept.sql_relative_path, "sql_sha256": sql_hash},
        )
        results.append(
            {
                "concept": concept.name,
                "rows": row_count,
                "elapsed_seconds": elapsed,
                "resumed": resumed,
            }
        )
    return results


def require_tables(connection: duckdb.DuckDBPyConnection, tables: Iterable[str]) -> None:
    missing = [table for table in tables if not table_exists(connection, table)]
    if missing:
        raise ConceptError("Required tables are missing: " + ", ".join(missing))


def execute_untracked(
    connection: duckdb.DuckDBPyConnection,
    *,
    concepts: Iterable[Concept],
    vendor_root: Path,
) -> None:
    """Execute exact official SQL for isolated demo/synthetic reference tests."""

    for concept in concepts:
        connection.execute((vendor_root / concept.sql_relative_path).read_text(encoding="utf-8"))
