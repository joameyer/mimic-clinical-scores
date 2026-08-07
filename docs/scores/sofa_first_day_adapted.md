# Classic first-day SOFA adapted for MIMIC-IV

## Identity and provenance

`sofa_first_day_adapted` is a one-row-per-ICU-stay classic SOFA score. It is based on
MIT-LCP/mimic-code release `v3.0.1`, commit
`c7e07560dc847e32cbb0b2890213e8e7cbd8bc7e`, and its DuckDB first-day SOFA source
(SHA-256 `02736bd4faf9885fed67de777ec85852b50e93ac1ddc03bd6e5039216ce3d86e`).
It is deliberately not named “official” because the project applies two documented
adaptations:

1. The legacy upstream first-day query omits scores 2 and 1 for invasively ventilated
   P/F ratios from 200 through 399. This implementation restores the classic
   ventilated P/F `<300` and `<400` categories and selects the worse supported or
   unsupported respiratory category.
2. Instead of building MIT-LCP's broad `first_day_lab` table, it derives only the
   platelet, bilirubin, creatinine, MAP, GCS, and urine aggregates used by SOFA. It
   preserves the upstream joins and inclusive boundary predicates.

All prerequisite measurement, ventilation, and medication concepts execute unchanged
from the pinned MIT-LCP source. `config/sofa_sources.json`, the SQL hashes, and the
item-ID manifest are included in preflight and run identity.

This is classic SOFA, not the newer SOFA-2 consensus score.

## Components and output

The six nullable component scores range from 0 to 4:

| Output component | Input | Worst-value rule |
|---|---|---|
| `respiration_score` | PaO2/FiO2 and invasive ventilation | Minimum P/F, with support-specific thresholds |
| `coagulation_score` | Platelets | Minimum |
| `liver_score` | Total bilirubin | Maximum |
| `cardiovascular_score` | MAP and dopamine, dobutamine, epinephrine, norepinephrine dose | Worst qualifying MAP/drug-dose category |
| `cns_score` | Reconstructed GCS | Minimum |
| `renal_score` | Creatinine and cumulative urine output | Worse creatinine/urine category |

`sofa_first_day_adapted` is their sum, range 0–24. As in the upstream query, a null
component contributes zero through `COALESCE`, while its original component remains
null. Consequently, a non-null total does not imply complete measurements.

Classic SOFA defines no direct mortality probability. The project therefore exports
no probability column and marks probability coverage as not applicable.

The score Parquet also retains the component-driving extrema and drug rates, the
adaptation version, and `ventilated_pf_correction_applied=true` for audit.

## Source and staging audit

| Component | Pinned concept | Raw source and key item IDs | Retained context | Caveat |
|---|---|---|---|---|
| Respiratory | `bg`, `oxygen_delivery`, `ventilator_setting`, `ventilation` | `labevents` blood-gas set including 50821/50816/52033; `chartevents` 223835, 220277 and ventilation/oxygen items | Labs `[intime-6h,intime+24h]`; full selected-stay ventilation/FiO2 context | Upstream first-day join is by subject and time and does not add an arterial-specimen restriction; only `InvasiveVent` counts as support |
| Coagulation | `complete_blood_count` | `labevents` complete concept set; platelets 51265 | `[intime-6h,intime+24h]` | Platelets may be unmeasured |
| Liver | `enzyme` | `labevents` complete concept set; bilirubin 50885 | `[intime-6h,intime+24h]` | Bilirubin is commonly less complete than routine labs |
| Cardiovascular | `vitalsign`; four medication concepts | MAP chart IDs; input IDs 221653, 221662, 221289, 221906 | MAP `[intime-6h,intime+24h]`; drug `starttime` in the same interval | A drug beginning earlier but continuing into the interval is omitted by preserved upstream start-time semantics; pre-ICU ICU-input capture may be incomplete |
| CNS | `gcs` | 220739, 223900, 223901 | Full selected-stay components; score selects within `[intime-6h,intime+24h]` | Sedation/intubation and missing/asynchronous components can limit interpretability |
| Renal | `chemistry`, `urine_output` | creatinine 50912; 12 official urine IDs | Creatinine `[intime-6h,intime+24h]`; urine `[intime,intime+24h]` | Urine is not duration-normalized for short stays |

Every source gzip is streamed once during a new staging build. Staging keeps only the
cohort, declared item IDs, and safe score context. The tables remain normalized.
`itemid_manifest.v1.json` declares the complete official item sets and preflight fails
if the pinned item-bearing SQL and manifest disagree.

## Window and short-stay interpretation

The general score window is inclusive from `ICU intime - 6 hours` through
`ICU intime + 24 hours`. Urine output begins exactly at ICU intime. It is not capped at
ICU outtime. Therefore hospital labs after ICU discharge but before hour 24 can
contribute, and a short stay supplies less than 24 hours of urine observation.

The standard duration fields remain descriptive:

- `icu_los_hours`: raw `outtime - intime`;
- `available_first_day_hours`: `min(24, max(0, icu_los_hours))`;
- `stay_shorter_than_24h`: whether ICU LOS is below 24 hours.

They do not shorten the score window. Null outtime produces null duration metadata and
the `unknown_length` coverage stratum.

## Missingness and limitations

Use `score_missingness.parquet`, `component_missingness.csv`, and `coverage.json` with
the score. Expected gaps include absent ABG/FiO2 pairs, bilirubin, GCS, or urine output.
The total's zero contribution for a missing component can systematically lower scores;
no additional imputation is performed.

Other preserved limitations are subject/time rather than admission matching for
first-day laboratory concepts, incomplete pre-ICU vasopressor capture, infusion
selection by start time instead of overlap, GCS confounding, and fixed first-day urine
thresholds for stays observed less than 24 hours. This is a research derivation and
requires outcome validation/calibration in its intended population.

## Correctness tests

Synthetic tests execute the same pinned prerequisite concepts twice: once against
unfiltered fixture tables and once against cohort/item/time-filtered staging. All IDs,
components, totals, and retained extrema are compared with null-safe exact equality.
Separate cases verify ventilated P/F ratios of 250 and 350 score 2 and 1, inclusive
`intime-6h` and `intime+24h` boundaries, short stays, missing components, exports, and
provenance. These tests establish implementation/staging equivalence, not clinical
validation.

## Development deployment

After syncing the repository and protected dev100 allowlist:

```zsh
cd /hpcwork/jrc_combine/joana/mimic-clinical-scores
mkdir -p logs/dev100/sofa_first_day_adapted
./.venv/bin/python -m mimic_clinical_scores preflight \
  --score sofa_first_day_adapted \
  --mode dev100 \
  --project-root "$PWD" \
  --mimic-root /hpcwork/jrc_combine/joana/mimic/data \
  --log-dir "$PWD/logs/dev100/sofa_first_day_adapted"
JOB_ID=$(sbatch --parsable --partition=c23ms \
  slurm/run_sofa_first_day_adapted_dev100.slurm)
echo "$JOB_ID"
squeue -j "$JOB_ID"
```

The job requests 4 CPUs, 24 GB, and 20 minutes and writes independent SOFA paths. The
100-score calculation is fast, but each compressed event source still requires one
streaming pass because CSV.GZ has no stay index.

After validation, full execution is deliberately gated:

```zsh
cd /hpcwork/jrc_combine/joana/mimic-clinical-scores
mkdir -p logs/full/sofa_first_day_adapted
JOB_ID=$(sbatch --parsable --partition=c23ms \
  --export=ALL,CONFIRM_FULL=YES \
  slurm/run_sofa_first_day_adapted_full.slurm)
echo "$JOB_ID"
squeue -j "$JOB_ID"
```

Supply another partition or account directly to `sbatch` when required. Re-submission
with unchanged inputs resumes verified artifacts. A changed cohort, raw metadata, SQL,
manifest, or code identity is refused; rebuilding requires a new database/output path
or explicit clean rebuild.
