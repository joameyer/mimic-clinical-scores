"""Independent, small reference implementation of the original point cut-offs."""

from __future__ import annotations

import math
from typing import Mapping


def physiology_points(
    *, gcs: float | None, heart_rate: float | None, systolic_bp: float | None,
    temperature_c: float | None, bilirubin_mg_dl: float | None,
    creatinine_mg_dl: float | None, wbc_highest_k_ul: float | None,
    platelets_k_ul: float | None, ph: float | None, mechanically_ventilated: bool,
    pao2_mm_hg: float | None, pf_ratio: float | None,
) -> dict[str, int | None]:
    return {
        "gcs_score": None if gcs is None else 15 if gcs <= 4 else 10 if gcs == 5 else 7 if gcs == 6 else 2 if gcs <= 12 else 0,
        "hr_score": None if heart_rate is None else 0 if heart_rate < 120 else 5 if heart_rate < 160 else 7,
        "sysbp_score": None if systolic_bp is None else 11 if systolic_bp < 40 else 8 if systolic_bp < 70 else 3 if systolic_bp < 120 else 0,
        "temp_score": None if temperature_c is None else 7 if temperature_c < 35 else 0,
        "bilirubin_score": None if bilirubin_mg_dl is None else 0 if bilirubin_mg_dl < 2 else 4 if bilirubin_mg_dl < 6 else 5,
        "creatinine_score": None if creatinine_mg_dl is None else 0 if creatinine_mg_dl < 1.2 else 2 if creatinine_mg_dl < 2 else 7 if creatinine_mg_dl < 3.5 else 8,
        "wbc_score": None if wbc_highest_k_ul is None else 0 if wbc_highest_k_ul < 15 else 2,
        "platelet_score": None if platelets_k_ul is None else 13 if platelets_k_ul < 20 else 8 if platelets_k_ul < 50 else 5 if platelets_k_ul < 100 else 0,
        "ph_score": None if ph is None else 3 if ph <= 7.25 else 0,
        "oxygenation_score": (
            (None if pf_ratio is None else 11 if pf_ratio < 100 else 7)
            if mechanically_ventilated
            else (None if pao2_mm_hg is None else 5 if pao2_mm_hg < 60 else 0)
        ),
    }


def proxy_total_and_unvalidated_probabilities(
    components: Mapping[str, int | None],
) -> tuple[int, float, float]:
    """Apply published equations to a proxy-filled sensitivity total.

    This validates equation transcription only. It does not make proxy components
    equivalent to the original SAPS III variables or produce a validated risk estimate.
    """
    score = 16 + sum(value or 0 for value in components.values())
    global_logit = -32.6659 + 7.3068 * math.log(score + 20.5958)
    north_america_logit = -18.8839 + 4.3979 * math.log(score + 1)
    logistic = lambda value: 1.0 / (1.0 + math.exp(-value))
    return score, logistic(global_logit), logistic(north_america_logit)
