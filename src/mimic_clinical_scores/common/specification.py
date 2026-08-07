"""Small score declaration interface shared by all score implementations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class Concept:
    """One immutable upstream concept in dependency order."""

    name: str
    sql_relative_path: str
    output_table: str


class ScoreSpecification(Protocol):
    """Minimal contract needed by the shared pipeline.

    A future score declares data and concept dependencies here. Cohort handling,
    database state, staging, execution, and exports remain score-independent.
    """

    name: str
    mimic_code_release: str
    mimic_code_commit: str
    concepts: tuple[Concept, ...]
    score_concept: Concept
    component_columns: tuple[str, ...]
    required_raw_tables: tuple[str, ...]
    expected_headers: Mapping[str, tuple[str, ...]]
    score_columns: tuple[str, ...]
    probability_columns: tuple[str, ...]
    score_table: str
    provenance_label: str
    item_manifest_version: str
    output_granularity: str
    primary_key_columns: tuple[str, ...]

    def vendor_root(self, project_root: Path) -> Path: ...

    def score_vendor_root(self, project_root: Path) -> Path: ...

    def item_ids(self, raw_table: str) -> frozenset[int]: ...

    def full_context_item_ids(self, raw_table: str) -> frozenset[int]: ...

    def sql_hashes(self, project_root: Path) -> Mapping[str, str]: ...

    def scores_projection_sql(self) -> str: ...

    def missingness_projection_sql(self) -> str: ...

    def staging_rules(self) -> Mapping[str, Any]: ...
