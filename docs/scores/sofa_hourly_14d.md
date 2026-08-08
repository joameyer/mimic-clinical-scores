# ICU-relative hourly SOFA through day 14

## Definition and provenance

`sofa_hourly_14d` computes one SOFA trajectory per ICU stay from raw MIMIC-IV. It is based on MIT-LCP `mimic-code` release `v3.0.1`, commit `c7e07560dc847e32cbb0b2890213e8e7cbd8bc7e`. The upstream hourly table is `mimic-iv/concepts_duckdb/score/sofa.sql` (SHA-256 `5af9c75bdaeb9342138a0fbc8cbef33b132508689e3ac492ab574af1c7ff05b0`). The upstream file and every unchanged dependency are vendored and hash-checked before a run.

The component thresholds, arterial blood-gas rule, ventilation-at-blood-gas-time rule,
urine-output-rate acceptance rule, and missing-as-zero total follow the pinned query.
Five adaptations/corrections are explicit:

- The upstream wall-clock/chart-derived grid is replaced by a grid anchored exactly at raw `icustays.intime`.
- Output is capped at ICU discharge or 336 one-hour intervals, whichever occurs first.
- Nullable rolling component values are retained next to the official zero-filled components and total.
- A partial final interval receives the exact leading boundary segment needed to make
  its component window `(endtime-24h,endtime]` rather than a shortened row-count window.
- Vasoactive rates must be positive and come from a recorded episode lasting at least
  one hour. Any half-open overlap `[episode_start,episode_end)` with the current hour
  is eligible once the episode's total recorded duration is at least one hour.

This is an ICU-relative adaptation of the pinned MIT-LCP hourly SOFA implementation, not an independently validated clinical software product.

## Hour and window semantics

`hour_index=0` is `(intime, intime + 1 hour]`. The left boundary is open and the right boundary is closed, exactly matching the upstream hourly predicates. A final interval ends at exact raw `outtime`. A stay ending at 24 hours has rows 0–23; a stay lasting 24 hours and one minute has a partial row 24. The maximum is 336 rows, indexed 0–335, covering the first 14 elapsed ICU days.

The score is not based on only the current hour. Every exported row uses the exact
chronological interval `(endtime-24h,endtime]`. For a full current hour this is the
maximum severity across the current and 23 preceding hourly intervals. For a partial
final hour, the SQL also evaluates the omitted leading fraction of an hour and merges
its component maxima. The internal grid starts 24 hours before ICU `intime`; negative
rows supply lookback context but are not exported. Thus hour 0 can incorporate
qualifying pre-ICU evidence.

If `outtime` is null, a discharge boundary is unknowable and the capped 336-hour grid
is emitted. This is visible through null `outtime` and `icu_los_hours`. A non-null
`outtime` at or before `intime` is excluded rather than producing a nonpositive interval.

## Component/source audit

| Component | Pinned concepts | Raw sources | Retained context | Main caveat |
|---|---|---|---|---|
| Respiratory | `bg`, `oxygen_delivery`, `ventilator_setting`, `ventilation` | Audited blood-gas, FiO2, SpO2, delivery and ventilation IDs | Labs `intime-24h` to discharge/cap; full selected-stay FiO2/SpO2/ventilation context | Requires arterial PaO2 and usable FiO2; ventilation is classified at gas time. No SpO2/FiO2 substitute. |
| Coagulation | `complete_blood_count` | Audited CBC IDs including platelet 51265 | `intime-24h` to discharge/cap | Missing platelet leaves the nullable rolling component missing. |
| Liver | `enzyme` | Audited enzyme IDs including bilirubin 50885 | `intime-24h` to discharge/cap | Bilirubin is often not measured. |
| Cardiovascular | `vitalsign`; four pinned vasoactive concepts | Audited vital IDs; input IDs 221662, 221653, 221289, 221906 | Measurements from `intime-24h`; qualifying infusion episodes overlapping each interval | Positive rates only; recorded episode duration must be ≥1h; half-open interval overlap replaces the upstream active-at-hour-end sampling predicate. |
| CNS | `gcs` | 220739, 223900, 223901 | Full selected-stay GCS context | Official reconstruction is preserved; no new sedation adjustment. |
| Renal | `chemistry`, `urine_output`, `weight_durations`, `urine_output_rate` | Audited chemistry/urine IDs; weights 224639/226512; HR 220045 | Labs from `intime-24h`; all earlier selected-stay urine rows through discharge/cap; full weight/HR timing context | Full earlier urine retention preserves the predecessor used by `LAG`; a rate is accepted only for an effective 22–30-hour window and scaled to 24 hours. |

Exact IDs, concept hashes, clinical meanings, and retention reasons are resolved from
the preserved `itemid_manifest.v1.json` plus the versioned
`itemid_manifest.v2.json` context overlay. Preflight fails for undeclared raw/item
dependencies, changed hashes, or a changed recursive graph.

## Outputs and missingness

`scores.parquet` has primary key `(stay_id, hour_index)` and deterministic ordering.
Version 2 outputs report `adaptation_version='sofa-hourly-14d-v2'`. Key columns are:

- `hour_start`, `hour_end`: current interval.
- `trailing_window_start`, `trailing_window_end`: effective component window.
- `sofa_hourly_24h`: official-style total.
- `<component>_24hours`: zero-filled rolling component used in the total.
- `<component>_24hours_raw`: nullable rolling maximum before zero filling.
- `<component>`: raw score from the current one-hour interval.
- Supporting extrema/rates such as P/F ratios, MAP, GCS, creatinine and vasopressor rates.

`score_missingness.parquet` has one row per stay-hour. Missingness means no qualifying value for that component in its trailing component window, not merely no value in the current hour. The total is non-null because the pinned SQL treats unavailable components as zero; it does not prove complete observation. No further imputation is performed.

`component_missingness.csv` summarizes stay-hour observations and calls its denominator
`row_count`. `coverage.json` separately reports `cohort_stays` and `score_rows`.

## Optimized staging and testing

Each required compressed source is scanned once. Only cohort identifiers, audited item
IDs, and required temporal context are retained in normalized MIMIC-compatible tables.
For urine output, all earlier audited rows for a selected stay are necessary context:
the pinned rate concept uses `LAG(charttime)`, so an arbitrary fixed lower bound can
change the first retained duration. Synthetic tests run identical concepts and adapted
SQL against unfiltered fixtures and filtered staging, then require null-safe exact
equality across every stay-hour. Separate tests cover the 2h15m partial-window
counterexample, a predecessor outside the old 48-hour bound, nonpositive LOS,
zero/sub-one-hour vasoactive episodes, discharge truncation, the cap, unknown
`outtime`, contiguous keys, and missingness.

This validates the optimized extraction against the same adapted SQL on fixtures. It cannot claim equality to the unmodified upstream grid because ICU-relative anchoring and the 14-day cap are deliberate differences.

## Cluster deployment

SLURM opens its output file before the script begins, so create the log parent first:

```zsh
cd /hpcwork/jrc_combine/joana/mimic-clinical-scores
mkdir -p logs/dev100
bash scripts/setup_venv.sh
./.venv/bin/python -m mimic_clinical_scores preflight \
  --score sofa_hourly_14d \
  --mode dev100 \
  --project-root "$PWD" \
  --mimic-root /hpcwork/jrc_combine/joana/mimic/data
JOB_ID=$(sbatch --parsable --partition=c23ms slurm/run_sofa_hourly_14d_dev100.slurm)
echo "$JOB_ID"
squeue -j "$JOB_ID"
```

After it starts:

```zsh
tail -F "logs/dev100/sofa-hourly-14d-${JOB_ID}.out" "logs/dev100/sofa-hourly-14d-${JOB_ID}.err"
```

Validate without rescanning clinical events:

```zsh
./.venv/bin/python -m mimic_clinical_scores validate \
  --score sofa_hourly_14d \
  --mode dev100 \
  --project-root "$PWD" \
  --mimic-root /hpcwork/jrc_combine/joana/mimic/data
```

Only after reviewing dev100, submit the deliberate full run:

```zsh
mkdir -p logs/full
JOB_ID=$(sbatch --parsable --partition=c23ms --export=ALL,CONFIRM_FULL=YES slurm/run_sofa_hourly_14d_full.slurm)
echo "$JOB_ID"
squeue -j "$JOB_ID"
```

The development request is 4 CPUs, 24 GiB and 30 minutes. The initial full request is 8 CPUs, 48 GiB and one hour because longitudinal staging and score tables are substantially larger than first-day SOFA. These are conservative initial, not measured final, requirements. Tune them after dev100 and `sacct`. Scripts do not assume a partition or account.

The pipeline resumes in the same database only when cohort, sources, SQL, code and manifest identity are unchanged. It never silently deletes a database; use a new path or explicit `--clean-rebuild` for an intentional rebuild.

## Limitations

- This is a trailing 24-hour trajectory sampled hourly, not an instantaneous organ-failure state.
- Hour 0 can include pre-ICU evidence because the upstream lookback is preserved.
- Missing components contribute zero and can bias totals downward.
- Respiratory scoring requires arterial blood-gas evidence.
- The final partial interval is shorter than one hour; its missing leading fraction is
  evaluated separately so the rolling window still covers exactly 24 elapsed hours.
- Null `outtime` cannot be discharge-truncated and is capped at 14 days.
- Clinical charting is treatment-driven; missingness is not random.
- Synthetic exact-equivalence tests are correctness evidence; dev100 is an integration test, not correctness proof.
