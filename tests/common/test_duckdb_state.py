from pathlib import Path

import pytest

from mimic_clinical_scores.common.duckdb import (
    DuckDBSettings,
    PipelineStateError,
    connect,
    ensure_run_identity,
    execute_table_artifact,
)


def test_transactional_artifact_resumes_and_identity_changes_refuse(tmp_path: Path) -> None:
    connection = connect(
        DuckDBSettings(
            database=tmp_path / "state.duckdb",
            memory_limit="256MB",
            spill_directory=tmp_path / "spill",
        )
    )
    try:
        identity = ensure_run_identity(connection, {"cohort": "a", "sql": "one"})
        first = execute_table_artifact(
            connection,
            artifact_name="test",
            artifact_type="test",
            qualified_table="mimiciv_derived.test",
            identity_hash=identity,
            artifact_hash="abc",
            sql="CREATE TABLE mimiciv_derived.test AS SELECT 1 AS value",
        )
        resumed = execute_table_artifact(
            connection,
            artifact_name="test",
            artifact_type="test",
            qualified_table="mimiciv_derived.test",
            identity_hash=identity,
            artifact_hash="abc",
            sql="CREATE TABLE mimiciv_derived.test AS SELECT 2 AS value",
        )
        assert first[0] == 1 and first[2] is False
        assert resumed[0] == 1 and resumed[2] is True
        assert connection.execute("SELECT value FROM mimiciv_derived.test").fetchone()[0] == 1
        with pytest.raises(PipelineStateError, match="identity differs"):
            ensure_run_identity(connection, {"cohort": "b", "sql": "one"})
    finally:
        connection.close()


def test_untracked_existing_table_is_ambiguous(tmp_path: Path) -> None:
    connection = connect(
        DuckDBSettings(database=tmp_path / "ambiguous.duckdb", spill_directory=tmp_path / "spill")
    )
    try:
        identity = ensure_run_identity(connection, {"cohort": "a"})
        connection.execute("CREATE TABLE mimiciv_derived.stray AS SELECT 1")
        with pytest.raises(PipelineStateError, match="without completion state"):
            execute_table_artifact(
                connection,
                artifact_name="stray",
                artifact_type="test",
                qualified_table="mimiciv_derived.stray",
                identity_hash=identity,
                artifact_hash="abc",
                sql="CREATE TABLE mimiciv_derived.stray AS SELECT 2",
            )
    finally:
        connection.close()

