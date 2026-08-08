# SAPS III adapted for MIMIC-IV

## Status and provenance

This is **not an official SAPS III implementation**. MIT-LCP/mimic-code v3.0.1 has
SAPS II and APS III concepts, but no SAPS III concept. The project therefore preserves
the published 2005 SAPS III point cut-offs and probability equations while declaring
each MIMIC-specific approximation as adaptation `saps-iii-adapted-v2`.

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

Original variables that cannot be established from structured MIMIC-IV remain null.
Because at least one required original variable is unavailable,
`saps_iii_complete_case_score` is null. The project separately exports
`saps_iii_proxy_total_unvalidated`, which zero-fills unavailable physiology and uses
the explicitly named `*_proxy_score` context fields. That value is a sensitivity
calculation, not a published-model score.

The two exported mortality estimates are:

- global: `logit = -32.6659 + 7.3068 × ln(score + 20.5958)`;
- North America: `logit = -18.8839 + 4.3979 × ln(score + 1)`.

They are applied only to the proxy total and named
`saps_iii_prob_global_proxy_unvalidated` and
`saps_iii_prob_north_america_proxy_unvalidated`. Their names are part of the safety
contract: they are neither validated SAPS III estimates nor calibrated guarantees for
MIMIC-IV.

## Staging/source audit

Each required gzip CSV is streamed once. Persistent DuckDB tables remain normalized
and contain only cohort-relevant rows. Exact endpoints are retained.

| SAPS III group | MIMIC-IV source | Retained context | Adaptation/caveat |
|---|---|---|---|
| Age | `patients`, `icustays` | Cohort subjects/stays | Anchor-based MIMIC age at ICU year |
| Hospital LOS before ICU | `admissions`, `icustays` | Cohort admissions | Exact admission-to-ICU elapsed time |
| Location before ICU | `transfers` | Complete cohort-admission transfer history | Latest care unit strictly before ICU; name-mapped to OR, ED, intermediate/ICU, or other |
| Comorbidity | `diagnoses_icd` | All diagnoses for cohort admissions | ICD-derived `comorbidity_proxy_score` only. The original component is null because post-hoc coding, NYHA IV, and recent cancer therapy are unavailable |
| Pre-ICU vasoactive therapy | `inputevents` IDs 221289, 221653, 221662, 221906 | Positive-rate events overlapping the 24 h before ICU; at least 1 h overlap | The original component is null because ICU input records incompletely represent ward/ED therapy. Dopamine additionally requires recorded rate ≥5 mcg/kg/min; `vasoactive_proxy_score` is sensitivity-only |
| Planned ICU | `admissions`, `services`, `transfers` | Complete admission/service/transfer context | Original component is null. A separately named proxy uses elective admission plus surgical service or OR predecessor; it cannot establish the original ≥12 h decision |
| Reason for ICU admission | `diagnoses_icd` | All cohort-admission diagnoses | Original component is null. The sensitivity proxy is post-hoc and uses an explicit priority rule |
| Surgery status/site | `procedures_icd`, `admissions`, diagnoses | Procedures dated no later than ICU calendar day | Original components are null. Day-resolution proxy fields cannot reproduce the planning rule or reliably establish site at ICU admission |
| Infection | diagnoses plus pre-ICU hospital LOS | All cohort-admission diagnoses | Original component is null. Proxy site/acquisition is post-hoc and not observed at admission |
| GCS | `chartevents` 220739, 223900, 223901 | Inclusive ±1 h | Original pre-sedation component is null. `gcs_proxy_score` uses the lowest complete contemporaneous charted set |
| HR | `chartevents` 220045 | Inclusive ±1 h | Highest valid value |
| Systolic BP | `chartevents` 220050, 220179, 225309 | Inclusive ±1 h | Lowest valid invasive/non-invasive value |
| Temperature | `chartevents` 223761, 223762 and site 224642 | Inclusive ±1 h; site must share chart time | Central temperature is used directly; documented peripheral temperature receives +0.5°C. If any candidate temperature lacks a recognized site, the component is unavailable because the highest adjusted value cannot be established |
| Bilirubin, creatinine, WBC, platelets, pH | `labevents` 50885, 50912, 51301, 51265, 50820 | Current stay's inclusive ±1 h | Highest bilirubin, creatinine, and WBC; lowest platelets and pH. The original supplement explicitly specifies highest WBC despite the later one-page sheet's conflicting “lowest” label. It explicitly permits either arterial or venous pH, so pH is not specimen-filtered |
| PaO2 / FiO2 / ventilation | lab 50821, specimen 52033, FiO2 50816; chart 223835 and audited support IDs | Current stay arterial gas ±1 h; paired context at gas time | PaO2 requires same-specimen `ART.`. Same-specimen lab FiO2 is preferred, otherwise preceding chart FiO2 within 2 h. Support requires a nonempty setting at or within 1 h before that gas; future or unrelated window settings do not classify the gas |

The exhaustive item declaration is
`src/mimic_clinical_scores/scores/saps_iii_adapted/itemid_manifest.v2.json`.
Preflight fails when score SQL references an item absent from that manifest or a raw
header/source is missing.

## What cannot be recovered exactly

Structured MIMIC-IV does not reliably encode the date/time an ICU decision was made,
the clinician-stated ICU admission reason, surgery planning lead time, infection
acquisition, NYHA class IV, recent cancer therapy, or a pre-sedation estimated GCS.
Input events also do not guarantee complete pre-ICU vasoactive capture. These are not
silently presented as reference-category observations: original component fields are
null, proxy fields contain the sensitivity mappings, and the total/probability names
state `proxy` and `unvalidated`.

Because these deviations can change both score and calibration, the proxy output may
be used only as a clearly labelled sensitivity variable. It must not be used as a
drop-in SAPS III implementation, a validated mortality estimate, or evidence of
equivalence to published SAPS III. External validation and calibration would be
required before predictive interpretation.

## Correctness tests

The independent Python reference checks every physiology threshold and the exact two
published equation transcriptions. Normalized synthetic tests independently verify
highest-WBC selection, isolation of multiple ICU stays in one admission, arterial-only
oxygenation, exclusion of a future ventilation setting, gas-time support, temperature
site adjustment/unavailability, exact ±1-hour boundaries, proxy totals, and the null
complete-case score. These tests establish implementation consistency, not clinical
validity of proxy variables.

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
