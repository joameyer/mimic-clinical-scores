"""DuckDB configuration and transactional resumability primitives."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import duckdb

from mimic_clinical_scores.common.provenance import canonical_json_hash, utc_now


class PipelineStateError(RuntimeError):
    """Raised when existing database state cannot be resumed unambiguously."""


@dataclass(frozen=True)
class DuckDBSettings:
    database: Path
    threads: int = 4
    memory_limit: str = "48GB"
    spill_directory: Path | None = None

    def validate(self) -> None:
        if self.threads < 1:
            raise ValueError("DuckDB threads must be at least 1")
        if not re.fullmatch(r"\d+(?:\.\d+)?\s*(?:B|KB|MB|GB|TB)", self.memory_limit, re.I):
            raise ValueError(f"Invalid DuckDB memory limit: {self.memory_limit!r}")
        if self.database.suffix != ".duckdb":
            raise ValueError("Database path must end in .duckdb")


def remove_database_for_clean_rebuild(path: Path) -> None:
    """Remove only an explicit .duckdb target and its exact WAL companion."""

    resolved = path.resolve()
    if path.suffix != ".duckdb" or resolved == Path(resolved.anchor):
        raise PipelineStateError(f"Unsafe clean-rebuild target: {path}")
    if path.is_symlink():
        raise PipelineStateError(f"Refusing to clean-rebuild through symlink: {path}")
    for candidate in (path, Path(f"{path}.wal")):
        if candidate.exists():
            candidate.unlink()


def connect(settings: DuckDBSettings) -> duckdb.DuckDBPyConnection:
    settings.validate()
    settings.database.parent.mkdir(parents=True, exist_ok=True)
    settings.database.parent.chmod(0o700)
    spill = settings.spill_directory or settings.database.parent / "spill"
    spill.mkdir(parents=True, exist_ok=True)
    spill.chmod(0o700)
    connection = duckdb.connect(str(settings.database))
    connection.execute(f"SET threads = {settings.threads}")
    connection.execute("SET memory_limit = ?", [settings.memory_limit])
    connection.execute("SET temp_directory = ?", [str(spill.resolve())])
    connection.execute("SET enable_progress_bar = false")
    connection.execute("CREATE SCHEMA IF NOT EXISTS mimiciv_hosp")
    connection.execute("CREATE SCHEMA IF NOT EXISTS mimiciv_icu")
    connection.execute("CREATE SCHEMA IF NOT EXISTS mimiciv_derived")
    connection.execute("CREATE SCHEMA IF NOT EXISTS pipeline_meta")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_meta.run_identity (
            singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
            identity_hash VARCHAR NOT NULL,
            payload_json VARCHAR NOT NULL,
            created_at TIMESTAMPTZ NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_meta.artifacts (
            artifact_name VARCHAR PRIMARY KEY,
            artifact_type VARCHAR NOT NULL,
            identity_hash VARCHAR NOT NULL,
            artifact_hash VARCHAR NOT NULL,
            row_count BIGINT NOT NULL,
            completed_at TIMESTAMPTZ NOT NULL,
            elapsed_seconds DOUBLE NOT NULL,
            details_json VARCHAR NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS pipeline_meta.staging_statistics (
            table_name VARCHAR PRIMARY KEY,
            statistics_json VARCHAR NOT NULL
        )
        """
    )
    return connection


def ensure_run_identity(
    connection: duckdb.DuckDBPyConnection, payload: dict[str, Any]
) -> str:
    identity_hash = canonical_json_hash(payload)
    rows = connection.execute(
        "SELECT identity_hash, payload_json FROM pipeline_meta.run_identity WHERE singleton"
    ).fetchall()
    if not rows:
        connection.execute(
            "INSERT INTO pipeline_meta.run_identity VALUES (TRUE, ?, ?, ?)",
            [identity_hash, json.dumps(payload, sort_keys=True, default=str), utc_now()],
        )
        return identity_hash
    existing_hash, existing_payload = rows[0]
    if existing_hash != identity_hash:
        raise PipelineStateError(
            "Database identity differs from this run. Use a new --database path or pass "
            "--clean-rebuild explicitly. Existing identity: "
            f"{existing_hash}; requested identity: {identity_hash}; existing payload: {existing_payload}"
        )
    return identity_hash


def table_exists(connection: duckdb.DuckDBPyConnection, qualified_table: str) -> bool:
    schema, table = qualified_table.split(".", 1)
    return bool(
        connection.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = ? AND table_name = ?
            """,
            [schema, table],
        ).fetchone()
    )


def artifact_complete(
    connection: duckdb.DuckDBPyConnection,
    *,
    artifact_name: str,
    qualified_table: str,
    identity_hash: str,
    artifact_hash: str,
) -> bool:
    row = connection.execute(
        """
        SELECT identity_hash, artifact_hash
        FROM pipeline_meta.artifacts WHERE artifact_name = ?
        """,
        [artifact_name],
    ).fetchone()
    exists = table_exists(connection, qualified_table)
    if row is None and exists:
        raise PipelineStateError(
            f"Ambiguous partial database: {qualified_table} exists without completion state"
        )
    if row is not None and not exists:
        raise PipelineStateError(
            f"Ambiguous partial database: completion state exists but {qualified_table} is missing"
        )
    if row is None:
        return False
    if row[0] != identity_hash or row[1] != artifact_hash:
        raise PipelineStateError(
            f"Artifact {artifact_name} was built with different cohort/source/SQL state"
        )
    return True


def execute_table_artifact(
    connection: duckdb.DuckDBPyConnection,
    *,
    artifact_name: str,
    artifact_type: str,
    qualified_table: str,
    identity_hash: str,
    artifact_hash: str,
    sql: str,
    details: dict[str, Any] | None = None,
    after_create: Callable[[duckdb.DuckDBPyConnection], None] | None = None,
    after_count: Callable[[duckdb.DuckDBPyConnection, int, float], None] | None = None,
) -> tuple[int, float, bool]:
    """Create a table and its completion record in one transaction."""

    if artifact_complete(
        connection,
        artifact_name=artifact_name,
        qualified_table=qualified_table,
        identity_hash=identity_hash,
        artifact_hash=artifact_hash,
    ):
        row_count = connection.execute(f"SELECT COUNT(*) FROM {qualified_table}").fetchone()[0]
        return int(row_count), 0.0, True

    started = time.monotonic()
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(sql)
        if after_create is not None:
            after_create(connection)
        row_count = int(connection.execute(f"SELECT COUNT(*) FROM {qualified_table}").fetchone()[0])
        elapsed = time.monotonic() - started
        if after_count is not None:
            after_count(connection, row_count, elapsed)
        connection.execute(
            """
            INSERT INTO pipeline_meta.artifacts
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                artifact_name,
                artifact_type,
                identity_hash,
                artifact_hash,
                row_count,
                utc_now(),
                elapsed,
                json.dumps(details or {}, sort_keys=True, default=str),
            ],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return row_count, elapsed, False


def read_artifact_rows(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    cursor = connection.execute(
        """
        SELECT artifact_name, artifact_type, identity_hash, artifact_hash,
               row_count, CAST(completed_at AS VARCHAR), elapsed_seconds, details_json
        FROM pipeline_meta.artifacts ORDER BY artifact_name
        """
    )
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
