from __future__ import annotations

import random
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from mimic_clinical_scores.common.cohort import (
    CohortError,
    inspect_cohort,
    prepare_all_icu_cohort,
    prepare_development_cohort,
)
from mimic_clinical_scores.common.provenance import hash_ordered_ids


def test_development_sample_is_exact_and_reproducible(tmp_path: Path) -> None:
    source = tmp_path / "static.parquet"
    output = tmp_path / "cohort_dev100.parquet"
    manifest = tmp_path / "cohort_dev100_manifest.json"
    source_ids = list(range(1000, 1175)) + [1000, 1001]
    pq.write_table(pa.table({"stay_id": pa.array(source_ids, type=pa.int64())}), source)

    result = prepare_development_cohort(source, output, manifest)
    expected = random.Random(20260807).sample(sorted(set(source_ids)), 100)
    actual = inspect_cohort(output, mode="dev100")

    assert list(actual.stay_ids) == expected
    assert result["source_row_count"] == 177
    assert result["number_of_unique_stay_ids"] == 175
    assert result["ordered_selected_id_sha256"] == hash_ordered_ids(expected)
    assert output.stat().st_mode & 0o077 == 0
    assert manifest.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("values", [[1, 1], [1, None]])
def test_invalid_cohort_is_rejected(tmp_path: Path, values: list[int | None]) -> None:
    path = tmp_path / "bad.parquet"
    pq.write_table(pa.table({"stay_id": pa.array(values, type=pa.int64())}), path)
    with pytest.raises(CohortError):
        inspect_cohort(path)


def test_non_integer_cohort_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad_type.parquet"
    pq.write_table(pa.table({"stay_id": ["1", "2"]}), path)
    with pytest.raises(CohortError, match="integer"):
        inspect_cohort(path)


def test_all_icu_cohort_comes_from_raw_icustays(
    tmp_path: Path, synthetic_mimic: dict[str, object]
) -> None:
    output = tmp_path / "cohort_all_icu.parquet"
    manifest = tmp_path / "cohort_all_icu_manifest.json"

    result = prepare_all_icu_cohort(
        Path(synthetic_mimic["root"]), output, manifest
    )
    cohort = inspect_cohort(output, mode="full")

    assert cohort.stay_ids == (1001, 1002, 1003, 1004, 1005, 1006)
    assert result["cohort_kind"] == "all_raw_mimic_icu_stays"
    assert result["source_row_count"] == 6
    assert result["number_of_unique_stay_ids"] == 6
    assert result["sample_size"] is None
    assert result["random_seed"] is None
    assert result["ordered_selected_id_sha256"] == hash_ordered_ids(cohort.stay_ids)
    assert output.stat().st_mode & 0o077 == 0
    assert manifest.stat().st_mode & 0o077 == 0
