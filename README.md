# MIMIC clinical scores

This project computes clinical severity scores from raw MIMIC-IV with a protected
stay-ID allowlist, filtered DuckDB staging, immutable official SQL, resumable state,
and auditable exports. It implements official MIT-LCP SAPS II, an explicitly named
MIMIC-IV adaptation of SAPS III, and a documented classic first-day SOFA adaptation.
APS III and SOFA-2 are not implemented.

The default operational workflow is the 100-stay HPC integration run. Local work is
limited to metadata-only preflight, cohort sampling, and synthetic/demo tests. Never
run the real cohort locally.

## Architecture

The importable package is `mimic_clinical_scores` (Python package names cannot contain
hyphens):

```text
src/mimic_clinical_scores/
  common/
    cohort.py          protected allowlists and deterministic sampling
    preflight.py       metadata-only validation and run identity
    duckdb.py          configuration and transactional completion state
    staging.py         normalized one-pass filtered staging
    concepts.py        immutable SQL execution
    provenance.py      source, code, SQL, and item-ID audits
    export.py          atomic Parquet/CSV/JSON outputs
    validation.py      null-safe reference equality
  scores/saps_ii/
    specification.py   small score declaration interface implementation
    staging_rules.py   score-specific raw retention rules
    scoring.py         score output projection
    itemid_manifest.v1.json
  scores/saps_iii_adapted/
    specification.py   raw dependencies and adaptation identity
    staging_rules.py   inclusive admission-window retention rules
    saps_iii_adapted.sql
    reference.py       independent point/equation checks
    itemid_manifest.v1.json
  scores/sofa_first_day_adapted/
    specification.py   pinned classic SOFA dependencies and identity
    staging_rules.py   first-day and episode-context retention rules
    sofa_first_day_adapted.sql
    scoring.py         score, evidence, and missingness projections
    itemid_manifest.v1.json
```

The shared layer owns cohort validation, raw lookup, DuckDB settings, staging,
resumption, provenance, exports, coverage, missingness, and cluster mechanics. SAPS II
owns its official version, concepts, components, raw dependencies, item IDs, context
rules, and score documentation. A future score supplies the small `ScoreSpecification`
contract and its own staging declaration; it does not reimplement the shared pipeline.
Select it with `--score saps_ii` (the backward-compatible default) or
`--score saps_iii_adapted`, or `--score sofa_first_day_adapted`.

## Classic first-day SOFA adapted

`sofa_first_day_adapted` produces one classic SOFA row per ICU stay. It executes
pinned MIT-LCP measurement, ventilation, and vasopressor concepts and applies the
classic six 0–4 organ-component thresholds. It restores the ventilated P/F 200–399
branches missing from the legacy MIT first-day query and uses equivalent narrow
first-day aggregates rather than the broad general-purpose first-day lab table. The
adaptations, time windows, source mapping, missingness semantics, and cluster commands
are documented in the [SOFA audit](docs/scores/sofa_first_day_adapted.md). Classic
SOFA has no direct mortality-probability output.

## SAPS III adapted

MIT-LCP/mimic-code v3.0.1 contains no SAPS III concept, so this project does not call
the new result official. `saps_iii_adapted` retains the 2005 point cut-offs, global
mortality equation, and North American equation, but uses versioned, visible MIMIC
proxies for facts structured MIMIC-IV cannot recover exactly. These include planned
ICU admission, the stated admission reason, surgery planning, infection acquisition,
NYHA IV, recent cancer therapy, pre-sedation GCS, and complete pre-ICU vasoactive
therapy. See the full [source and adaptation audit](docs/scores/saps_iii_adapted.md).

SAPS III physiology uses the inclusive interval
`[intime - 1 hour, intime + 1 hour]`, not the SAPS II first-day window. It therefore
has separate filtered staging and cannot reuse a SAPS II DuckDB database.

No code or configuration is imported from the previous mortality project. The blocked
`static.parquet` is read only by `prepare-cohort`, and only its `stay_id` column is
loaded. `dynamic_8h.parquet` and `dynamic_15m.parquet` are never used: their aggregation
can hide extrema, omit variables, and destroy time pairing needed by SAPS II.

## Official SAPS II provenance

The repository pins MIT-LCP/mimic-code release `v3.0.1` at full commit
`c7e07560dc847e32cbb0b2890213e8e7cbd8bc7e`. It was the latest published release when
inspected on 2026-08-07. Pinning the release and commit avoids production drift from
`main`; this release also contains MIT-LCP's generated DuckDB concepts. See the
[official release](https://github.com/MIT-LCP/mimic-code/releases/tag/v3.0.1) and
[immutable commit](https://github.com/MIT-LCP/mimic-code/commit/c7e07560dc847e32cbb0b2890213e8e7cbd8bc7e).

The canonical SAPS II definition, the 12 executed DuckDB files, and the upstream MIT
license are vendored byte-for-byte under `vendor/mimic-code`. SHA-256 values live in
`config/official_sources.json`; preflight checks every value. The executable dependency
order is derived recursively and contains 11 prerequisites plus SAPS II, not the full
mimic-code concept collection.

There are no edits to upstream SQL. The unavoidable integration work is outside it:
normalized filtered tables are created under the expected `mimiciv_hosp` and
`mimiciv_icu` schemas, and project-owned exports add ICU-duration and missingness fields.
Verify the checked-in subset without network access:

```zsh
./.venv/bin/python scripts/bootstrap_mimic_code.py --project-root "$PWD"
```

Refresh only from the pinned tag, verify its full commit, and replace only declared
files:

```zsh
./.venv/bin/python scripts/bootstrap_mimic_code.py --project-root "$PWD" --refresh
```

## Cohorts

Development sampling uses exactly:

```python
random.Random(seed).sample(sorted(stay_ids), sample_size)
```

The defaults are 100 stays and seed `20260807`. Create the protected allowlist locally:

```zsh
./.venv/bin/python -m mimic_clinical_scores prepare-cohort \
  --project-root "$PWD" \
  --source /Users/joanameyer/repository/phase-aware-icu-mortality-prediction/data/mimic-iv-blocked/full/static.parquet
```

This writes gitignored `inputs/cohort_dev100.parquet` and
`inputs/cohort_dev100_manifest.json`. The manifest records the source fingerprint and
row counts, unique stays, seed, ordered selected-ID hash, creation time, and software
versions. It contains no blocked features.

Full mode accepts any Parquet file with one unique, non-null integer `stay_id` column.
For the all-MIMIC run, `prepare-all-icu-cohort` reads every `stay_id` directly from raw
`icu/icustays.csv.gz` on the cluster and writes protected, gitignored
`inputs/cohort_all_icu.parquet` plus its provenance manifest. The full SLURM script does
this automatically when `FULL_COHORT_FILE` is not supplied. A custom future allowlist
can still be supplied without implementation changes. Full raw staging requires
`--confirm-full`. Cohort membership and size are never hardcoded. A database identity
includes the content fingerprint and ordered-ID hash, so state cannot be reused for a
different cohort.

## Staging and time semantics

Preflight discovers these raw dependencies from the SQL and fails if any is absent:

```text
hosp/admissions.csv.gz
hosp/diagnoses_icd.csv.gz
hosp/labevents.csv.gz
hosp/patients.csv.gz
hosp/services.csv.gz
icu/chartevents.csv.gz
icu/icustays.csv.gz
icu/outputevents.csv.gz
```

Each gzip CSV is streamed exactly once per successful staging build. DuckDB profiles
that scan to record the source row count, while the persistent table receives only
cohort-, item-, and time-relevant rows. Tables remain normalized; no wide event frame is
created. This preserves official grouping, minima, maxima, sums, and timestamp joins.

The main retention rules are:

- `icustays`: selected stays only; raw subject, admission, intime, and outtime are
  authoritative and must map exactly once.
- `admissions`, `patients`: cohort admission and subject identifiers only.
- `services`: all history for cohort admissions, preserving both the record before ICU
  and the first service used by the pinned SQL.
- `diagnoses_icd`: every diagnosis for cohort admissions, without a 24-hour filter.
- `chartevents`: selected stays and audited item IDs. Ordinary first-day values use
  `charttime > intime AND charttime <= intime + 24 hours`; SpO2 keeps the official
  two-hour blood-gas lookback. All GCS and ventilation/oxygen-delivery item events for
  selected stays are retained because finite context is not equivalent to the upstream
  row/episode logic.
- `labevents`: cohort admissions and audited IDs in the union of exact score windows,
  using `charttime > intime AND charttime <= intime + 24 hours`.
- `outputevents`: selected stays, audited urine IDs, and that same exact window.

The upstream score does not cap its window at ICU discharge. A hospital lab after
`outtime` but no later than `intime + 24 hours` remains eligible. No `>` predicate was
changed to `>=`, or vice versa. Details and the component/source mapping are in
[docs/scores/saps_ii.md](docs/scores/saps_ii.md).

### Short ICU stays

Stays shorter than 24 hours are not excluded. The score follows the pinned SQL's fixed
`(intime, intime + 24h]` window even if `outtime` occurs earlier. Output metadata is
calculated from raw ICU timestamps:

- `icu_los_hours = (outtime - intime)` in hours;
- `available_first_day_hours = min(24, max(0, icu_los_hours))`;
- `stay_shorter_than_24h = icu_los_hours < 24`.

These fields describe ICU observation availability; they do not modify the official
score window. Coverage and component missingness are reported separately for short and
non-short stays. Raw `subject_id`, `hadm_id`, and `intime` are required for every
selected stay. A null raw `outtime` is preserved rather than excluding the stay;
duration fields and `stay_shorter_than_24h` remain null, and coverage reports the stay
under `unknown_length`.

## Resumable CLI

Available stages are `preflight`, `prepare-cohort`, `prepare-all-icu-cohort`,
`build-staging`, `build-concepts`, `compute`, `export`, `validate`, and `run-all`.

Every staging table and concept is created in a transaction with a completion record.
Resume skips only artifacts whose cohort, raw metadata, source code, and SQL hashes
match. A table without state, state without a table, or changed identity is an error.
The pipeline never deletes a database implicitly. Choose a new `--database`, or use the
deliberate `--clean-rebuild` acknowledgement.

DuckDB threads, memory, database, output, logs, and spill paths are configurable. Raw
scanning commands require `--allow-clinical-scan`; the SLURM scripts supply it. Output
writes use same-directory temporary files plus atomic rename.

## Safe local validation

Install the pinned environment:

```zsh
bash scripts/setup_venv.sh
```

Run metadata-only preflight against local MIMIC-IV. It opens gzip files only far enough
to read headers and reads the cohort Parquet metadata/ID column; it does not calculate a
score or scan complete event contents:

```zsh
./.venv/bin/python -m mimic_clinical_scores preflight \
  --mode dev100 \
  --project-root "$PWD" \
  --mimic-root /Users/joanameyer/data/mimic-iv/mimic-iv-3.1
```

`--verify-raw-checksums` is optional and intentionally expensive: it streams each raw
file only to hash bytes, still without parsing or scoring clinical values.

For another score, add `--score saps_iii_adapted` or
`--score sofa_first_day_adapted`. The latter validates five raw files, the pinned
MIT-LCP SQL subset, adaptation manifest, project SQL, and item-ID audit without
scanning clinical contents.

Run correctness tests entirely on synthetic data:

```zsh
./.venv/bin/pytest -q
```

The reference pathway imports all synthetic/demo-sized rows and executes the exact
official concepts. The optimized pathway executes those same files after filtered
staging. The comparison is null-safe and exact for subject/admission/stay IDs, start and
end, total, probability, and every component. The optional official demo test is
described in the SAPS II documentation; no demo data is downloaded automatically. The
public MIMIC-IV demo v2.2 pathway was executed successfully during project validation.

## Cluster setup and development run

Defaults are:

```text
MIMIC_ROOT=/hpcwork/jrc_combine/joana/mimic/data
PROJECT_ROOT=/hpcwork/jrc_combine/joana/mimic-clinical-scores
```

From the local repository, sync code with Apple-compatible rsync options, then sync the
protected allowlist separately:

```zsh
rsync -av --progress --partial \
  --exclude '.git/' --exclude '.venv/' --exclude '.pytest_cache/' --exclude '__pycache__/' \
  --exclude '*.egg-info/' --exclude 'inputs/*.parquet' --exclude 'inputs/*_manifest.json' \
  --exclude 'work/' --exclude 'outputs/' --exclude 'logs/' \
  ./ am861154@login23-1.hpc.itc.rwth-aachen.de:/hpcwork/jrc_combine/joana/mimic-clinical-scores/
rsync -av --progress --partial \
  inputs/cohort_dev100.parquet inputs/cohort_dev100_manifest.json \
  am861154@login23-1.hpc.itc.rwth-aachen.de:/hpcwork/jrc_combine/joana/mimic-clinical-scores/inputs/
```

On the login node, create the environment and required pre-submission log directories:

```zsh
cd /hpcwork/jrc_combine/joana/mimic-clinical-scores
mkdir -p logs/dev100 logs/full
bash scripts/setup_venv.sh
./.venv/bin/python -m mimic_clinical_scores preflight \
  --mode dev100 \
  --project-root "$PWD" \
  --mimic-root /hpcwork/jrc_combine/joana/mimic/data
```

Submit the default development job:

```zsh
cd /hpcwork/jrc_combine/joana/mimic-clinical-scores
sbatch slurm/run_dev100.slurm
```

After deploying the adapted SAPS III code, its separate 100-stay integration is:

```zsh
cd /hpcwork/jrc_combine/joana/mimic-clinical-scores
mkdir -p logs/dev100/saps_iii_adapted
./.venv/bin/python -m mimic_clinical_scores preflight \
  --score saps_iii_adapted \
  --mode dev100 \
  --project-root "$PWD" \
  --mimic-root /hpcwork/jrc_combine/joana/mimic/data \
  --log-dir "$PWD/logs/dev100/saps_iii_adapted"
sbatch --partition=c23ms slurm/run_saps_iii_adapted_dev100.slurm
```

That job has independent `work/dev100/saps_iii_adapted.duckdb`,
`outputs/dev100/saps_iii_adapted`, and `logs/dev100/saps_iii_adapted` paths. Its
20-minute limit includes ample margin over the earlier four-minute SAPS II dev100 raw
scan while adding transfers, procedures, and input events. The limit is not an
expected duration.

After the validated dev100 run, submit SAPS III adapted for every ICU stay with its
separate, deliberately gated full script:

```zsh
cd /hpcwork/jrc_combine/joana/mimic-clinical-scores
mkdir -p logs/full/saps_iii_adapted
JOB_ID=$(sbatch --parsable --partition=c23ms \
  --export=ALL,CONFIRM_FULL=YES \
  slurm/run_saps_iii_adapted_full.slurm)
echo "$JOB_ID"
squeue -j "$JOB_ID"
```

It reuses the protected all-ICU allowlist when present, requests 8 CPUs, 32 GB and 30
minutes, and writes only under the SAPS III adapted full paths. See the
[SAPS III adapted audit](docs/scores/saps_iii_adapted.md) for monitoring and resume
details.

The validated full-cohort definitions, missingness summaries, deployment records, and
interpretation limitations for both scores are consolidated in the
[full-cohort score report](docs/scores/full_cohort_score_report.md).

The development script requests 4 CPUs, 24 GB RAM, and a two-hour maximum wall
time, with DuckDB limited to 12 GB. The cohort contains only 100 ICU stays, but the
CSV.GZ sources have no stay index, so the required raw files must each be streamed
once before only matching rows are retained. The requested wall time is a scheduler
limit, not an expected runtime.

No account or partition is assumed. If the site requires them, supply both to `sbatch`:

```zsh
ACCOUNT=your_account
PARTITION=your_partition
sbatch --account="$ACCOUNT" --partition="$PARTITION" slurm/run_dev100.slurm
```

Monitor and inspect logs:

```zsh
squeue -u "$USER"
tail -F logs/dev100/slurm-JOB_ID.out
```

`tail -F` is an optional foreground log viewer. It deliberately waits for new output
until interrupted with `Ctrl-C`; stopping it does not stop the SLURM job. Environment
setup and safe preflight run once on the login node, while all clinical-data scanning
and score computation run inside the submitted SLURM job.

To resume, submit the same script with the same cohort/database. Completed artifacts
are verified and skipped. To preserve an old run while rebuilding, override the
database and output paths at submission:

```zsh
sbatch --export=ALL,DATABASE=/hpcwork/jrc_combine/joana/mimic-clinical-scores/work/dev100/saps_ii_retry.duckdb,OUTPUT_DIR=/hpcwork/jrc_combine/joana/mimic-clinical-scores/outputs/dev100/saps_ii_retry slurm/run_dev100.slurm
```

## Deliberate full run

Do this only after the dev100 integration and validation complete. With no custom
cohort, the full script derives the protected allowlist from raw `icustays.csv.gz`
inside the SLURM job and therefore scores every MIMIC ICU stay:

```zsh
cd /hpcwork/jrc_combine/joana/mimic-clinical-scores
mkdir -p logs/full
sbatch --export=ALL,CONFIRM_FULL=YES slurm/run_full.slurm
```

SAPS II is an ICU-stay-level score. A patient with multiple ICU stays receives one row
per stay. To use a custom future cohort instead, explicitly provide its protected
allowlist:

```zsh
sbatch --export=ALL,CONFIRM_FULL=YES,FULL_COHORT_FILE=/protected/path/cohort_full.parquet slurm/run_full.slurm
```

The default full allocation is 8 CPUs, 64 GB RAM, and a 30-minute maximum wall time,
with DuckDB limited to 48 GB and spill enabled. The pipeline is resumable if the first
full run reaches the limit.

Command-line `--cpus-per-task`, `--mem`, and `--time` override the conservative script
defaults. `DUCKDB_THREADS`, `DUCKDB_MEMORY_LIMIT`, `DATABASE`, `OUTPUT_DIR`, and
`SPILL_DIRECTORY` can be set with `sbatch --export`.

## Outputs and missingness

Development outputs are under `outputs/dev100/saps_ii`; full outputs are under
`outputs/full/saps_ii`:

- `scores.parquet`: one deterministic row per stay with raw identifiers/timestamps,
  ICU availability fields, official total/probability/windows, and all 15 components;
- `score_missingness.parquet`: patient-level Boolean component indicators, missing
  count, and complete-components flag;
- `component_missingness.csv`: component counts/percentages overall and by short-stay
  stratum;
- `coverage.json`: cohort matching, score/probability, and component coverage overall
  and by duration stratum;
- `staging_statistics.json`: raw fingerprints/sizes/scan rows, retained rows/fractions,
  filters, and elapsed times;
- `run_manifest.json`: complete run identity, provenance, runtime, SLURM metadata,
  artifacts, statistics, coverage, timestamps, and output paths.

The official SQL computes the total using `COALESCE(component_score, 0)`. A non-null
SAPS II total therefore does not mean the inputs were complete. Original null component
scores are retained; the project adds no imputation and exposes missingness explicitly.

Fetch protected results with compatible rsync flags:

```zsh
rsync -av --progress --partial \
  am861154@login23-1.hpc.itc.rwth-aachen.de:/hpcwork/jrc_combine/joana/mimic-clinical-scores/outputs/dev100/saps_ii/ \
  outputs/dev100/saps_ii/
```

## Privacy, runtime, and limitations

Cohort IDs, databases, profiles, logs, and outputs are protected MIMIC-derived data.
They are gitignored and created with restrictive local permissions. Raw MIMIC data is
never copied into this repository. Review destination permissions before syncing.

Even the 100-stay run must stream the long compressed event files once because CSV.GZ
has no stay index. The successful HPC development run completed in 3 minutes 59
seconds; its chart and laboratory scans took 115 and 86 seconds, respectively, and its
peak resident memory was 15.18 GB. This time must not be multiplied by the number of
stays: those full-file scans are largely fixed costs, while retained staging rows and
downstream concept work scale with cohort size and are not guaranteed to scale
linearly. The first all-ICU HPC run processed 94,458 stays and completed the logged
pipeline in approximately 2 minutes 22 seconds. Its repeat-run ceiling is 30 minutes,
leaving substantial room for filesystem contention without reserving hours. Disk is
dominated by the filtered DuckDB database and spill; use `staging_statistics.json` and
SLURM accounting to tune later runs. Defaults request 24 GB/2 h for dev100 and 64 GB/30
min for all ICU stays; actual storage and runtime depend on filesystem throughput and
cohort composition.

Python logging writes INFO records to stderr by default, so normal timestamped pipeline
progress appears in SLURM's `.err` file. A non-empty `.err` file is not itself a failed
job; use the final validation JSON, `sacct` state, and exit code to determine success.

Known limitations: the demo is MIMIC-IV v2.2 because no v3.1 demo is published; the
real-data integrations were run only on the HPC cluster, never locally; and v3.0.1's clinical
choices—including a fixed 24-hour window beyond ICU discharge and its service
ordering—are preserved even when they may be surprising. SAPS III adapted has no
MIT-LCP reference SQL and cannot reconstruct several original admission-time facts;
its proxy flags and documentation must accompany analysis. APS III is not implemented.
