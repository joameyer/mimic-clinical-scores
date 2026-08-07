from __future__ import annotations

import duckdb
import pytest

from mimic_clinical_scores.common.staging import StagingError, _validate_icustays


def test_icustay_null_outtime_is_allowed_but_null_intime_is_rejected() -> None:
    connection = duckdb.connect(":memory:")
    connection.execute("CREATE SCHEMA mimiciv_icu")
    connection.execute(
        """
        CREATE TABLE mimiciv_icu.icustays (
            subject_id INTEGER,
            hadm_id INTEGER,
            stay_id INTEGER,
            intime TIMESTAMP,
            outtime TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        INSERT INTO mimiciv_icu.icustays VALUES
          (1, 10, 100, TIMESTAMP '2100-01-01 00:00:00', NULL)
        """
    )
    _validate_icustays(connection, 1)

    connection.execute("UPDATE mimiciv_icu.icustays SET intime = NULL")
    with pytest.raises(StagingError, match="intime"):
        _validate_icustays(connection, 1)
    connection.close()
