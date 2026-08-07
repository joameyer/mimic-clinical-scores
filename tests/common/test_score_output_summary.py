from __future__ import annotations

import json
import importlib.util
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "summarize_score_outputs.py"
SPEC = importlib.util.spec_from_file_location("summarize_score_outputs", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SCORES = MODULE.SCORES
summarize_score = MODULE.summarize_score


def test_identifier_free_score_output_summary(tmp_path) -> None:
    for name, config in SCORES.items():
        output = tmp_path / name
        output.mkdir()
        scores: dict[str, list[object]] = {
            "stay_id": [1, 2],
            "stay_shorter_than_24h": [False, True],
            config["total"]: [10, 20],
        }
        for probability in config["probabilities"]:
            scores[probability] = [0.1, 0.2]
        for proxy in config["proxy_columns"]:
            scores[proxy] = [True, False]
        pq.write_table(pa.table(scores), output / "scores.parquet")
        pq.write_table(
            pa.table(
                {
                    "stay_id": [1, 2],
                    "example_score_missing": [False, True],
                    "number_of_missing_components": [0, 1],
                    "complete_components": [True, False],
                }
            ),
            output / "score_missingness.parquet",
        )
        (output / "run_manifest.json").write_text(
            json.dumps(
                {
                    "run_identity_hash": name,
                    "run_mode": "full",
                    "mimic_version": "synthetic",
                    "runtime_configuration": {"threads": 1, "memory_limit": "1GB"},
                    "start_timestamp_utc": "2026-01-01T00:00:00+00:00",
                    "completion_timestamp_utc": "2026-01-01T00:00:02+00:00",
                    "staging_statistics": {},
                }
            ),
            encoding="utf-8",
        )

        summary = summarize_score(output, name)
        assert summary["row_validation"] == {
            "rows": 2,
            "unique_stays": 2,
            "null_stay_ids": 0,
            "missingness_rows": 2,
            "score_missingness_membership_difference": 0,
        }
        assert summary["component_missingness"]["example_score"]["missing"]["count"] == 1
        assert summary["deployment"]["manifest_elapsed_seconds"] == 2.0
        assert '"stay_ids": [' not in json.dumps(summary)
