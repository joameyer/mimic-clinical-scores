# SAPS II specification and source audit

## Pinned definition

SAPS II executes the official MIT-LCP DuckDB concepts from mimic-code `v3.0.1`, commit
`c7e07560dc847e32cbb0b2890213e8e7cbd8bc7e`. `config/official_sources.json` records the
canonical table-definition hash and all execution hashes. The recursive order is:

1. `demographics/age.sql`
2. `measurement/bg.sql`
3. `measurement/chemistry.sql`
4. `measurement/complete_blood_count.sql`
5. `measurement/enzyme.sql`
6. `measurement/gcs.sql`
7. `measurement/oxygen_delivery.sql`
8. `measurement/urine_output.sql`
9. `measurement/ventilator_setting.sql`
10. `measurement/vitalsign.sql`
11. `treatment/ventilation.sql`
12. `score/sapsii.sql`

Every SQL file is byte-for-byte upstream. Project wrappers provide schemas, filtered
raw tables, state, and output aliases; there are no SQL adaptations.

Before these files execute, the shared fail-closed
[unit assurance](unit_assurance.md) verifies that every score-eligible raw numeric row
uses the units assumed by the pinned thresholds. This wrapper validation does not edit
or relabel the official SQL.

## Component/source audit

The table lists the raw facts that can affect each SAPS II component. The unchanged
upstream concepts read additional columns/item IDs for their general-purpose outputs;
the exhaustive per-concept declaration is
`src/mimic_clinical_scores/scores/saps_ii/itemid_manifest.v1.json`.

| SAPS II component | Official concept | Raw source and clinically relevant item IDs | Official score window | Staged context and caveats |
|---|---|---|---|---|
| Age | `age` | `admissions`, `patients`; no item ID | Age at hospital admission | Cohort admissions/subjects; raw admission time and anchor fields |
| Heart rate | `vitalsign` | `chartevents`: 220045 | `charttime > intime`, `<= intime+24h` | Exact window; both low and high extrema retained |
| Systolic BP | `vitalsign` | `chartevents`: 220179, 220050, 225309 | Same | Exact window; invasive and non-invasive sources retained |
| Temperature | `vitalsign` | `chartevents`: 223761, 223762; site 224642 is read upstream | Same | Fahrenheit conversion remains upstream; exact boundary preserved |
| PaO2/FiO2 with support | `bg`, `ventilation`, direct CPAP CTE | `labevents`: 52033, 50816, 50821 (the full `bg` item list is staged); `chartevents`: 223835 FiO2, 220277 SpO2, 226732 oxygen device, 223834/227582/227287 flow, and ventilator IDs 224688, 224689, 224690, 224687, 224685, 224684, 224686, 224696, 220339, 224700, 223849, 229314, 223848, 224691 | Blood gas `> intime`, `<= intime+24h`; preceding SpO2 up to 2 h and FiO2 up to 4 h; ventilation episode must overlap gas time | All ventilation/oxygen-delivery events for selected stays are retained. This is deliberately broader than 24 h because a finite cut was not proven episode-equivalent. FiO2 is thereby also available for the official 4 h lookback. CPAP uses direct item 226732 and the upstream `>`/`<=` joins. |
| Urine output | `urine_output` | `outputevents`: 226559, 226560, 226561, 226584, 226563, 226564, 226565, 226567, 226557, 226558, 227488, 227489 | `charttime > intime`, `<= intime+24h` in SAPS II | All eligible events retained and summed; GU irrigant input sign handling remains upstream |
| BUN | `chemistry` | `labevents`: 51006 | Same | All measurements retained; upstream maximum determines score |
| WBC | `complete_blood_count` | `labevents`: 51301 | Same | All measurements retained; upstream low/high extrema, never a median |
| Potassium | `chemistry` | `labevents`: 50971 | Same | All measurements retained; low/high extrema |
| Sodium | `chemistry` | `labevents`: 50983 | Same | All measurements retained; low/high extrema |
| Bicarbonate | `chemistry` | `labevents`: 50882 | Same | All measurements retained; upstream minimum drives score |
| Bilirubin | `enzyme` | `labevents`: 50885 | Same | All measurements retained; upstream maximum drives score |
| GCS | `gcs` | `chartevents`: 223900 verbal, 223901 motor, 220739 eyes | Reconstructed GCS row `> intime`, `<= intime+24h` | All GCS rows for selected stays are retained. The upstream reconstruction joins the immediately preceding row with no finite time limit, including rows before ICU admission. Components recorded at different times and ETT verbal values remain unchanged. |
| Chronic disease | SAPS II inline comorbidity CTE | All `diagnoses_icd` rows for cohort `hadm_id`; no item ID | No first-day restriction | Full hospital diagnosis history retained for AIDS, hematologic malignancy, and metastatic cancer flags |
| Admission type | SAPS II inline surgical flag | `admissions.admission_type`, all `services` rows for cohort admission | Hospital/ICU context | All service history retained. Pinned SQL assigns `ROW_NUMBER` ordered by transfer time and uses its first row; no project reinterpretation is applied. |

The general concepts also read these score-irrelevant-but-SQL-declared groups, all
captured in the versioned manifest:

- blood gas: 25 laboratory item IDs;
- chemistry: 12 laboratory item IDs;
- complete blood count: 10 laboratory item IDs;
- enzyme: 11 laboratory item IDs;
- vital sign: 19 chart item IDs;
- ventilation/oxygen delivery: 19 chart item IDs before overlap;
- urine output: 12 output item IDs.

Preflight parses raw/derived table references recursively and extracts every `itemid =`
and `itemid IN (...)` declaration. Tests fail if observed tables or IDs differ from the
versioned manifest, or if an SQL hash changes.

## Boundary and short-stay behavior

The SAPS II CTE sets `starttime = intime` and `endtime = intime + 24 hours`. For vitals,
labs, urine, GCS, and blood gas, the score generally uses `starttime < charttime` and
`endtime >= charttime`: an event exactly at ICU admission is not scored; an event
exactly at hour 24 is scored. Direct CPAP uses the same exclusive/inclusive form.

Upstream concepts are not uniformly first-day concepts. GCS can consult the preceding
row. `bg` can consult preceding oxygen values. `ventilation` builds episodes from event
sequences and a 14-hour gap rule. Consequently, naïvely filtering every event to hours
0–24 is not equivalent.

ICU `outtime` is not part of the official score filters. For a five-hour ICU stay, a
hospital lab at hour eight is eligible. This project preserves that behavior and reports
`available_first_day_hours` independently as the ICU-overlap duration capped to 24 h.
An exactly 24-hour stay is not marked short. If raw `outtime` is null, the stay remains
eligible because the official window depends on `intime`; ICU LOS, available hours, and
the short-stay flag remain null and aggregate coverage places it in `unknown_length`.

## Missingness semantics

Every original component score is selected into the output without imputation. The
official total is nevertheless always calculated with `COALESCE(component_score, 0)`.
The probability is calculated from that total. Interpret a populated total/probability
together with `score_missingness.parquet`, not as evidence of complete physiologic
observation. In particular, the pinned SQL leaves PaO2/FiO2 null when no qualifying
ventilation/CPAP blood gas exists; this can represent a non-applicable oxygenation
component rather than a staging omission. Bilirubin is also commonly unmeasured. The
strict complete-component metric intentionally reports both as null.

## Correctness pathways

Synthetic tests cover exact/intime/hour-24 boundaries and adjacent events, early and
exact-24-hour discharge, a post-discharge lab, multiple ICU stays per admission,
low/high heart rate, low BP, worst rather than median labs, blood gases with/without
FiO2, CPAP, ventilation spanning admission and hour 24, asynchronous/lookback/missing
GCS, summed/missing urine, three comorbidity/admission types, and sparse components.

The reference database imports all fixture rows. The optimized database runs the
production filters. Both execute the identical official files, then compare every
official field with null-safe exact equality.

An official MIMIC-IV demo can be tested when separately downloaded and exposed as
`MIMIC_DEMO_ROOT`; the directory must contain the same eight `hosp/` and `icu/` files.
No demo or protected data is downloaded by this repository. The public v2.2 demo was
checksum-verified and passed exact reference equivalence during project validation;
the test skips by default when `MIMIC_DEMO_ROOT` is absent. Synthetic reference
equivalence remains mandatory in every default test run. MIMIC-IV v3.1 restored its
laboratory item IDs to consistency with v2.2 according to the
[v3.1 release notes](https://physionet.org/content/mimiciv/3.1/), but no v3.1 demo is
currently published.

Run the optional demo pathway explicitly:

```zsh
MIMIC_DEMO_ROOT=/path/containing/mimic-iv-demo
./.venv/bin/pytest -q -m demo
./.venv/bin/python scripts/compare_demo.py \
  --demo-root "$MIMIC_DEMO_ROOT" \
  --project-root "$PWD"
```

## Output field definitions

`scores.parquet` keeps official `starttime`/`endtime` as `score_window_start` and
`score_window_end`; `sapsii` and `sapsii_prob` become `sapsii_official` and
`sapsii_prob_official`. Components retain their upstream names:

```text
age_score hr_score sysbp_score temp_score pao2fio2_score uo_score
bun_score wbc_score potassium_score sodium_score bicarbonate_score
bilirubin_score gcs_score comorbidity_score admissiontype_score
```
