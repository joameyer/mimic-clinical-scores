"""Score registry kept deliberately small: a score declares, common code executes."""

from __future__ import annotations

from mimic_clinical_scores.common.specification import ScoreSpecification
from mimic_clinical_scores.scores.saps_ii.specification import SAPSII_SPEC
from mimic_clinical_scores.scores.saps_iii_adapted.specification import SAPSIII_ADAPTED_SPEC
from mimic_clinical_scores.scores.sofa_first_day_adapted.specification import SOFA_FIRST_DAY_ADAPTED_SPEC


SCORES: dict[str, ScoreSpecification] = {
    SAPSII_SPEC.name: SAPSII_SPEC,
    SAPSIII_ADAPTED_SPEC.name: SAPSIII_ADAPTED_SPEC,
    SOFA_FIRST_DAY_ADAPTED_SPEC.name: SOFA_FIRST_DAY_ADAPTED_SPEC,
}


def get_score(name: str) -> ScoreSpecification:
    try:
        return SCORES[name]
    except KeyError as error:
        raise ValueError(f"Unknown score {name!r}; choose one of {', '.join(sorted(SCORES))}") from error
