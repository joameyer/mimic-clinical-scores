# Quantitative unit assurance

## Enforcement

Every production score run performs a fail-closed unit audit after filtered staging
and before any pinned or project-owned concept SQL executes. The audit reads the raw
MIMIC `valueuom` or `rateuom` associated with every score-eligible numeric row. It
normalizes presentation only (case, whitespace, `µ`/`μ`, degree symbols, and periods),
then compares the result with the item-specific allowlist below. It does not infer a
missing physical unit and does not silently reinterpret a dimensionally different
unit. A blank unit is accepted only for explicitly declared dimensionless values (GCS
points and pH), or for the fixed FiO2 item IDs whose fraction/percent representation
is disambiguated by an explicit numeric domain.

The audit is a transactional pipeline artifact tied to the immutable run identity and
unit-rules hash. Tracked concept execution, export, and export validation all refuse to
continue if the artifact is absent, belongs to another run, or contains a rejected
observation. `unit_validation.json` records every rule, item ID, target dimension,
accepted spelling, observed normalized unit, row count, and pass/fail result. The same
payload is embedded in `run_manifest.json` and is reconciled during validation.

Only rows that can enter a score-driving numeric concept are checked. Text variables
(ventilator mode, specimen type, temperature site), identifiers, and timestamps are
outside the unit allowlist. GCS integer categories and pH are explicitly audited as
dimensionless. Numeric values that the pinned concepts reject as physiologically
impossible are likewise not treated as scored values.

## Accepted units and transformations

| Quantity | MIMIC field/item IDs | Accepted recorded units | Unit seen by score thresholds |
|---|---|---|---|
| Heart rate | `chartevents` 220045 | bpm | beats/min; unchanged |
| Systolic BP | `chartevents` 220050, 220179, 225309 | mmHg / `mm Hg` | mmHg; unchanged |
| Mean arterial pressure | `chartevents` 220052, 220181, 225312 | mmHg / `mm Hg` | mmHg; unchanged |
| Temperature | `chartevents` 223761 | F / deg F / °F | converted to °C as `(F-32)/1.8` by the pinned vitals concept and SAPS III adaptation |
| Temperature | `chartevents` 223762 | C / deg C / °C | °C; unchanged |
| FiO2 | `chartevents` 223835; `labevents` 50816 | `%` or blank event unit | The fixed item ID establishes FiO2 when `valueuom` is blank. For SAPS III, values in `(0.2,1]` are fractions and values in `(20,100]` are divided by 100. Pinned MIT blood-gas SQL performs its own normalization for SAPS II/SOFA: lab FiO2 uses the same bounds, while chart FiO2 accepts `[20,100]` as percent. Other values do not score. |
| Oxygen saturation | `chartevents` 220277 | `%` | percent; unchanged |
| GCS eye/verbal/motor | `chartevents` 220739 / 223900 / 223901 | blank or `points` | dimensionless integer points |
| PaO2 | `labevents` 50821 | mmHg / `mm Hg` | mmHg; unchanged; kPa is rejected |
| pH | `labevents` 50820 | blank or `units` | dimensionless; unchanged |
| BUN | `labevents` 51006 | mg/dL | mg/dL; unchanged |
| Total bilirubin | `labevents` 50885 | mg/dL | mg/dL; unchanged; µmol/L is rejected |
| Creatinine | `labevents` 50912 | mg/dL | mg/dL; unchanged; µmol/L is rejected |
| WBC / platelets | `labevents` 51301 / 51265 | K/uL, 10^3/uL, G/L, 10^9/L | 10^3/uL; the accepted forms are numerically equivalent |
| Potassium / sodium / bicarbonate | `labevents` 50971 / 50983 / 50882 | mEq/L or mmol/L | mEq/L; these monovalent-ion values are numerically equivalent |
| Urine output | 12 pinned `outputevents` urine IDs | mL | mL; unchanged. Hourly urine-rate concepts apply their documented elapsed-time calculations after this check. |
| Dobutamine, dopamine, epinephrine | `inputevents` 221653 / 221662 / 221289 | mcg/kg/min or the equivalent µg/kg/min spelling | mcg/kg/min; unchanged |
| Norepinephrine | `inputevents` 221906 | mcg/kg/min (including µg spelling) or mg/kg/min | mcg/kg/min; the pinned MIT concept multiplies mg/kg/min by 1000 (subject to its pinned weight sentinel handling) |

Age does not have a companion unit column: MIMIC `anchor_age` is defined in years and
the project preserves that scale while applying the anchor-year adjustment. Durations
are derived directly from timestamps with explicit conversions: SAPS III pre-ICU stay
is seconds divided by 86,400 (days), while ICU/hourly and infusion durations use
explicit hour or second differences. Mortality logits and probabilities are
dimensionless. These structural units are code-defined rather than inferred from a
free-text MIMIC unit field.

The score-specific subset is explicit in
[`common/units.py`](../../src/mimic_clinical_scores/common/units.py): SAPS II checks 16
quantity groups, SAPS III adapted checks 17, and every SOFA variant checks 14. SAPS
III's pH remains specimen-agnostic and dimensionless as specified by its original data
definition; only PaO2/PF requires an arterial specimen.

## Boundary and rejection tests

Synthetic tests cover accepted spelling variants, °F-to-°C source separation,
numerically equivalent electrolyte and cell-count units, missing units, PaO2 in kPa,
bilirubin in µmol/L, and the medication distinction where mg/kg/min is accepted only
for norepinephrine. SAPS III integration tests also prove that FiO2 values in the
ambiguous `1–20` domain are unavailable and cannot mask a valid prior charted FiO2.

This gate establishes dimensional compatibility of values that reach the scoring SQL.
It does not establish calibration, clinical validity, or completeness of measurement;
those limitations remain score-specific.
