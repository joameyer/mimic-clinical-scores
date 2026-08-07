"""Small score declaration interface shared by all score implementations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol


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

    def vendor_root(self, project_root: Path) -> Path: ...

    def item_ids(self, raw_table: str) -> frozenset[int]: ...

    def full_context_item_ids(self, raw_table: str) -> frozenset[int]: ...

