# Complete-stay SOFA in eight-hour blocks

## Definition and indexing

`sofa_8h_all_stay` emits a standard trailing 24-hour SOFA trajectory at
non-overlapping eight-hour intervals across the complete recorded ICU stay. Blocks
are anchored to raw `icustays.intime`; they are not aligned to wall-clock shifts.

The primary key is `(stay_id, block_index)`:

- block 0 is `(intime, intime + 8 hours]`;
- block 1 is `(intime + 8 hours, intime + 16 hours]`;
- indexing continues without a day cap;
- the final block ends exactly at raw `outtime` and can be shorter than eight hours.

Every row reports the worst SOFA component values in the exact chronological interval
`(block_end - 24 hours, block_end]`. For a full block, this is the current block and
two preceding eight-hour blocks. A partial discharge block shifts the true window
start away from the ICU-intime-aligned grid, so the SQL separately evaluates the
omitted leading segment and merges its component maxima. Thus the last row always
covers 24 elapsed hours rather than merely three grid rows.

Pre-ICU evidence can contribute to early blocks, matching the pinned hourly SOFA
lookback behavior. Internal negative-index context is never exported.

## Eligibility and complete-stay meaning

There is no first-14-day or last-7-day restriction. Every positive-duration stay with
a non-null `outtime` contributes `ceil(icu_los_hours / 8)` rows. Null `outtime` and
nonpositive durations are excluded because the end of the complete stay is unknown or
invalid. `coverage.json` and export validation report scored and excluded stay counts.

The score uses MIT-LCP `mimic-code` v3.0.1 at commit
`c7e07560dc847e32cbb0b2890213e8e7cbd8bc7e`. The pinned upstream hourly SOFA SHA-256
is `5af9c75bdaeb9342138a0fbc8cbef33b132508689e3ac492ab574af1c7ff05b0`.
Component thresholds, arterial P/F logic, ventilation status at blood-gas time,
urine-rate acceptance, and missing-as-zero total behavior are unchanged.

## Output columns

`scores.parquet` reports `adaptation_version='sofa-8h-all-stay-v1'`. Important fields
include:

- `block_index`, `block_start`, `block_end`, `block_duration_hours`;
- `trailing_window_start`, `trailing_window_end`;
- `sofa_trailing_24h`, the standard SOFA total at `block_end`;
- `<component>_24hours`, the zero-filled rolling component used in the total;
- `<component>_24hours_raw`, the nullable rolling component before zero filling;
- `<component>`, the component calculated from the current eight-hour block only;
- GCS audit fields: `gcs_min`, `gcs_motor`, `gcs_verbal`, `gcs_eyes`, `gcs_unable`,
  `gcs_components_measured`, and `gcs_charttime`;
- P/F audit fields: PaO2, effective FiO2, FiO2 source, ratio, and gas time for the
  selected ventilated and non-ventilated observations;
- urine audit fields: `uo_24hr`, `urineoutput_24hr`, `uo_tm_24hr`, and
  `uo_24hr_charttime`;
- the existing individual vasoactive rates, MAP, bilirubin, creatinine, and platelet
  extrema.

Supporting measurements describe the observation selected within the current block.
The rolling component and total columns describe the trailing 24-hour window.

The pinned MIMIC GCS reconstruction can default missing components and handles an
endotracheal-tube verbal response specially. Therefore the three retained GCS
observations do not necessarily sum to `gcs_min`; `gcs_unable` and
`gcs_components_measured` make those cases visible.

## Missingness and staging

`score_missingness.parquet` distinguishes unavailable rolling components from observed
zero scores. Missing components still contribute zero to `sofa_trailing_24h`, as in
the pinned upstream query.

Ordinary score-driving chart and lab rows are retained from `intime - 24 hours`
through `outtime`. GCS, FiO2/SpO2, ventilation, weight, and heart-rate timing inputs
retain full selected-stay reconstruction context. Vasoactive episodes are retained by
interval overlap and must have a positive rate and a recorded duration of at least one
hour. All earlier audited urine rows through `outtime` are retained because the pinned
urine-rate concept uses `LAG(charttime)` and a nested 24-hour lookback.

## Cluster workflow

Run and validate dev100 first:

```zsh
cd /hpcwork/jrc_combine/joana/mimic-clinical-scores
mkdir -p logs/dev100
bash scripts/setup_venv.sh
./.venv/bin/python -m mimic_clinical_scores preflight \
  --score sofa_8h_all_stay \
  --mode dev100 \
  --project-root "$PWD" \
  --mimic-root /hpcwork/jrc_combine/joana/mimic/data
JOB_ID=$(sbatch --parsable --partition=c23ms slurm/run_sofa_8h_all_stay_dev100.slurm)
echo "$JOB_ID"
squeue -j "$JOB_ID"
```

After reviewing dev100, submit the full run:

```zsh
mkdir -p logs/full
JOB_ID=$(sbatch --parsable --partition=c23ms \
  --export=ALL,CONFIRM_FULL=YES slurm/run_sofa_8h_all_stay_full.slurm)
echo "$JOB_ID"
squeue -j "$JOB_ID"
```

The full script initially requests 8 CPUs, 48 GiB, and one hour. Complete-stay staging
can retain more rows than the capped 14-day score, so adjust resources after measuring
dev100 and the first production run.

## Limitations

- SOFA remains a trailing 24-hour score sampled every eight hours, not an instantaneous
  score and not a score based only on the current block.
- Missing components contribute zero and can bias totals downward.
- Respiratory scoring requires arterial blood-gas evidence; no SpO2/FiO2 substitute is
  introduced.
- Unknown `outtime` stays cannot be represented as complete stays and are excluded.
- Clinical measurements are treatment-driven and missingness is not random.
