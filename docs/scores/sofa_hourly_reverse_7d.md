# Discharge-relative hourly SOFA over the final seven ICU days

## Purpose and indexing

`sofa_hourly_reverse_7d` computes SOFA trajectories backward from raw ICU discharge for all eligible stays, regardless of survival. It is separate from the admission-relative 14-day trajectory.

The primary key is `(stay_id, hours_before_discharge)`:

- `0` is `(outtime - 1 hour, outtime]`;
- `1` is `(outtime - 2 hours, outtime - 1 hour]`;
- indexing continues backward through at most `167`;
- stays shorter than seven days contribute only their ICU duration, with a partial earliest interval when needed.

Every row reports the worst component score over the exact chronological interval
`(endtime-24h,endtime]`. Ordinarily this is the current interval and 23 preceding
hourly intervals. Where the earliest partial interval becomes the first of 24 output
rows, version 2 separately evaluates the omitted leading segment and merges its
component maxima. Internal context extends before the earliest exported interval but
is not exported. Thus the earliest row of a short stay can use qualifying pre-ICU
evidence.

Stays with null or non-positive `outtime-intime` are excluded because reverse alignment is undefined. Validation and `coverage.json` report their count explicitly.

The shared [unit assurance](unit_assurance.md) verifies all score-driving measurement,
urine, and infusion units before the pinned concepts execute. The audit is part of the
immutable run state and exported validation evidence.

## Death and survival fields

Scoring never filters on outcome. Raw `admissions.deathtime` and `hospital_expire_flag` are attached after constructing the grid. `scores.parquet` contains:

- `death_recorded_by_icu_discharge`: `deathtime <= ICU outtime`;
- `died_during_icu_stay`: `intime <= deathtime <= outtime`;
- `no_death_recorded_by_icu_discharge`: logical complement of
  `death_recorded_by_icu_discharge`; it does not assert adjudicated survival;
- `hospital_expire_flag`: death during the hospital admission.

An ICU stay can have no death recorded by its ICU discharge while its admission later
has `hospital_expire_flag=1`. Multiple ICU stays in one admission retain their own
ICU-discharge-relative classification. These fields describe recorded MIMIC timestamps
and do not establish adjudicated survival or a clinical cause of death.

## Provenance and staging

The score uses MIT-LCP `mimic-code` v3.0.1 at commit `c7e07560dc847e32cbb0b2890213e8e7cbd8bc7e`. The source hourly SOFA hash is `5af9c75bdaeb9342138a0fbc8cbef33b132508689e3ac492ab574af1c7ff05b0`.

Thresholds, arterial P/F handling, ventilation-at-gas-time classification,
urine-rate criteria, and missing-as-zero total follow the pinned query. Adaptations
include the reverse grid, seven-day cap, explicit outtime exclusion, exact correction
of the partial-boundary rolling window, outcome annotation, exposed nullable rolling
components, and the original SOFA requirement that scored positive vasoactive rates
come from recorded episodes lasting at least one hour. Episode/hour overlap is
half-open: `episode_start < hour_end` and `episode_end > hour_start`.

Ordinary chart and lab events are retained from 24 hours before
`max(intime, outtime-168h)` through `outtime`. All earlier audited urine rows for an
eligible selected stay are retained through `outtime`, because the pinned urine-rate
concept's `LAG(charttime)` makes the immediate predecessor necessary even when it lies
outside an arbitrary 48-hour lower bound. FiO2/SpO2, ventilation, GCS, weight, and
heart-rate timing IDs keep full selected-stay context. Infusions are retained by
interval overlap. Admissions are retained only for cohort `hadm_id` values.

Exact item IDs and hashes are resolved from the preserved `itemid_manifest.v1.json`
plus the `itemid_manifest.v2.json` context overlay. Preflight verifies the recursive
graph, six raw files, SQL hashes, headers, and cohort without scanning complete
clinical events.

Corrected outputs carry `adaptation_version='sofa-hourly-reverse-7d-v2'`.

## Missingness

`sofa_hourly_24h` preserves upstream `COALESCE(component,0)` behavior. Nullable `<component>_24hours_raw` columns and `score_missingness.parquet` must be used to distinguish an observed zero from an unavailable component. No new imputation is introduced.

Missingness summaries are calculated over stay-hour rows. They are not percentages of unique patients or stays.

## Cluster workflow

Run dev100 first:

```zsh
cd /hpcwork/jrc_combine/joana/mimic-clinical-scores
mkdir -p logs/dev100
bash scripts/setup_venv.sh
./.venv/bin/python -m mimic_clinical_scores preflight \
  --score sofa_hourly_reverse_7d \
  --mode dev100 \
  --project-root "$PWD" \
  --mimic-root /hpcwork/jrc_combine/joana/mimic/data
JOB_ID=$(sbatch --parsable --partition=c23ms slurm/run_sofa_hourly_reverse_7d_dev100.slurm)
echo "$JOB_ID"
squeue -j "$JOB_ID"
```

Monitor after it starts:

```zsh
tail -F "logs/dev100/sofa-reverse-7d-${JOB_ID}.out" "logs/dev100/sofa-reverse-7d-${JOB_ID}.err"
```

The deliberate full script requests 8 CPUs, 32 GiB, and 30 minutes, based on the measured admission-relative full job. Submit it only after dev100 validates:

```zsh
mkdir -p logs/full
JOB_ID=$(sbatch --parsable --partition=c23ms --export=ALL,CONFIRM_FULL=YES slurm/run_sofa_hourly_reverse_7d_full.slurm)
echo "$JOB_ID"
squeue -j "$JOB_ID"
```

## Limitations

- Reverse alignment requires a trustworthy ICU `outtime`; excluded stays are not missing at random by assumption.
- SOFA remains a trailing 24-hour score sampled hourly, not an instantaneous state.
- Missing components contribute zero to the total and can bias it downward.
- Respiratory scoring requires arterial blood-gas evidence; no SpO2/FiO2 substitute is added.
- Outcome flags are timestamp-derived and should not be interpreted as adjudicated ICU mortality.
- Synthetic tests include the 25h15m partial-boundary counterexample, exact death-time
  predicates, vasoactive duration/rate boundaries, and filtered-versus-unfiltered
  equivalence; real dev100 remains an HPC integration test.
