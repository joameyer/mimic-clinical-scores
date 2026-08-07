# SAPS III adapted for MIMIC-IV

## Status and provenance

This is **not an official SAPS III implementation**. MIT-LCP/mimic-code v3.0.1 has
SAPS II and APS III concepts, but no SAPS III concept. The project therefore preserves
the published 2005 SAPS III point cut-offs and probability equations while declaring
each MIMIC-specific approximation as adaptation `saps-iii-adapted-v1`.

`config/saps_iii_sources.json` pins the official score sheet and data-definition
supplement by URL and SHA-256, and cites the primary validation paper (DOI
`10.1007/s00134-005-2763-5`). Downloading the PDFs is optional for execution:

```zsh
./.venv/bin/python scripts/bootstrap_saps_iii_sources.py --project-root "$PWD" --download
```

The downloaded documents are verified and gitignored. The project-owned SQL and
item-ID manifest are hashed into every preflight report and run identity.

## Model and observation window

SAPS III is an admission score, not a first-24-hour score. It starts with 16 points and
has 20 component groups in three boxes: pre-ICU context, circumstances/reason for ICU
admission, and physiology. Physiology is selected from the inclusive interval
`[ICU intime - 1 hour, ICU intime + 1 hour]`. The fixed exported window is not capped
at ICU discharge. ICU duration fields remain in the output for cohort audit and
short-stay stratification, but do not alter SAPS III.

The total treats an unavailable component as its published reference category (zero
points), while the original null component remains null. Consequently, a total and
probability do not establish complete observation. Always inspect
`score_missingness.parquet` and the availability/proxy columns.

The two exported mortality estimates are:

- global: `logit = -32.6659 + 7.3068 × ln(score + 20.5958)`;
- North America: `logit = -18.8839 + 4.3979 × ln(score + 1)`.

They are named `saps_iii_prob_global_adapted` and
`saps_iii_prob_north_america_adapted`. They are model estimates, not observed outcomes
or calibrated guarantees for MIMIC-IV.

## Staging/source audit

Each required gzip CSV is streamed once. Persistent DuckDB tables remain normalized
and contain only cohort-relevant rows. Exact endpoints are retained.

| SAPS III group | MIMIC-IV source | Retained context | Adaptation/caveat |
|---|---|---|---|
| Age | `patients`, `icustays` | Cohort subjects/stays | Anchor-based MIMIC age at ICU year |
| Hospital LOS before ICU | `admissions`, `icustays` | Cohort admissions | Exact admission-to-ICU elapsed time |
| Location before ICU | `transfers` | Complete cohort-admission transfer history | Latest care unit strictly before ICU; name-mapped to OR, ED, intermediate/ICU, or other |
| Comorbidity | `diagnoses_icd` | All diagnoses for cohort admissions | High-specificity ICD proxies for AIDS, metastatic cancer, hematologic malignancy, cirrhosis; current-discharge coding is post-hoc. NYHA IV and cancer therapy are unavailable and score zero |
| Pre-ICU vasoactive therapy | `inputevents` IDs 221289, 221653, 221662, 221906 | Events overlapping the 24 h before ICU; at least 1 h overlap | ICU input records incompletely represent ward/ED therapy. Dopamine requires recorded rate ≥5 mcg/kg/min |
| Planned ICU | `admissions`, `services`, `transfers` | Complete admission/service/transfer context | Proxy: elective admission plus surgical service or OR predecessor. The original ≥12 h planned-decision fact is unavailable |
| Reason for ICU admission | `diagnoses_icd` | All cohort-admission diagnoses | High-specificity ICD proxy; when several groups occur, one priority/worst mapped category is selected. Diagnoses are post-hoc, not admission-time indications |
| Surgery status/site | `procedures_icd`, `admissions`, diagnoses | Procedures dated no later than ICU calendar day | Day-resolution and admission-type proxies cannot reproduce the original 24 h planning rule. Transplant, trauma, isolated CABG, and cerebrovascular neurosurgery mappings are versioned in SQL |
| Infection | diagnoses plus pre-ICU hospital LOS | All cohort-admission diagnoses | Respiratory infection is ICD-derived. Nosocomial status requires an infection/septic-shock proxy plus ≥2 pre-ICU hospital days; acquisition is not observed |
| GCS | `chartevents` 220739, 223900, 223901 | Inclusive ±1 h | Lowest complete eye+verbal+motor set sharing one chart time. Pre-sedation GCS remains unavailable |
| HR | `chartevents` 220045 | Inclusive ±1 h | Highest valid value |
| Systolic BP | `chartevents` 220050, 220179, 225309 | Inclusive ±1 h | Lowest valid invasive/non-invasive value |
| Temperature | `chartevents` 223761, 223762 | Inclusive ±1 h | Highest, Fahrenheit converted to Celsius |
| Bilirubin, creatinine, WBC, platelets, pH, PaO2 | `labevents` 50885, 50912, 51301, 51265, 50820, 50821 | Inclusive ±1 h for every selected stay sharing the admission | Published direction: highest bilirubin/creatinine and lowest leukocytes/platelets/pH/PaO2; multiple ICU stays in one admission use their own windows |
| FiO2 / ventilation | lab 50816; chart 223835 and audited ventilator-setting IDs | Gas ±1 h; chart FiO2 retains up to 2 h preceding a boundary gas | Same-time lab FiO2 preferred, otherwise most recent chart FiO2 in preceding 2 h. Any recorded ventilator setting in the admission window is the mechanical-ventilation proxy |

The exhaustive item declaration is
`src/mimic_clinical_scores/scores/saps_iii_adapted/itemid_manifest.v1.json`.
Preflight fails when score SQL references an item absent from that manifest or a raw
header/source is missing.

## What cannot be recovered exactly

Structured MIMIC-IV does not reliably encode the date/time an ICU decision was made,
the clinician-stated ICU admission reason, surgery planning lead time, infection
acquisition, NYHA class IV, recent cancer therapy, or a pre-sedation estimated GCS.
Input events also do not guarantee complete pre-ICU vasoactive capture. These are not
silently presented as exact variables: the score name, adaptation version, proxy
fields, source documentation, and run manifest keep the distinction visible.

Because these deviations can change both score and calibration, use this adapted score
for transparent research comparisons and sensitivity analysis—not as a drop-in
clinical SAPS III implementation. External validation/calibration is required before
interpreting its probabilities in a target population.

## Correctness tests

The independent Python reference checks every physiology threshold and both published
equations. A normalized synthetic raw-data pathway tests exact ±1-hour boundaries,
immediate exclusion, worst values, GCS, missing components, P/F with ventilation,
vasoactive overlap, context proxy scores, total, and both probabilities. These tests
establish implementation consistency, not equivalence of proxies to unavailable facts.

## Dev100 cluster run

After syncing code and the existing protected allowlist, run safe preflight:

```zsh
cd /hpcwork/jrc_combine/joana/mimic-clinical-scores
mkdir -p logs/dev100/saps_iii_adapted
./.venv/bin/python -m mimic_clinical_scores preflight \
  --score saps_iii_adapted \
  --mode dev100 \
  --project-root "$PWD" \
  --mimic-root /hpcwork/jrc_combine/joana/mimic/data \
  --log-dir "$PWD/logs/dev100/saps_iii_adapted"
```

Submit the 100-stay job:

```zsh
sbatch --partition=c23ms slurm/run_saps_iii_adapted_dev100.slurm
```

The script requests 4 CPUs, 24 GB, and 20 minutes. Work is dominated by one streaming
pass through each relevant compressed source, not by 100 downstream score rows. It
writes separate database, spill, log, and output paths from SAPS II. The completed HPC
integration took 3 minutes 6 seconds, used about 5.27 GiB peak resident memory, matched
all 100 stays, and passed output validation.

## Full ICU cohort

The full script reuses `inputs/cohort_all_icu.parquet` when it exists. If the default
allowlist is absent, it creates it from raw `icu/icustays.csv.gz` inside the submitted
job. A supplied custom `FULL_COHORT_FILE` must already exist. Full execution is gated
by both `CONFIRM_FULL=YES` in the script environment and the CLI's `--confirm-full`.

Create the SLURM output directory before submission, because SLURM opens its output
files before the script itself starts:

```zsh
cd /hpcwork/jrc_combine/joana/mimic-clinical-scores
mkdir -p logs/full/saps_iii_adapted
JOB_ID=$(sbatch --parsable --partition=c23ms \
  --export=ALL,CONFIRM_FULL=YES \
  slurm/run_saps_iii_adapted_full.slurm)
echo "$JOB_ID"
squeue -j "$JOB_ID"
```

The allocation is 8 CPUs, 32 GB, and 30 minutes, with DuckDB limited to 24 GB and a
dedicated spill directory. This leaves substantial margin over the dev100 integration
and the earlier full SAPS II run while avoiding the previous 64 GB reservation. It
writes `work/full/saps_iii_adapted.duckdb`, `outputs/full/saps_iii_adapted`, and
`logs/full/saps_iii_adapted` without touching SAPS II artifacts.

Progress is in the `.err` file after the job begins; final validation JSON is in
`.out`. Re-submitting with the same paths resumes verified artifacts. A different
cohort, SQL, item manifest, source metadata, or code identity is rejected rather than
silently reused.
