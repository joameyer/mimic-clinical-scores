#!/usr/bin/env bash
#SBATCH --job-name=sapsii-full
#SBATCH --chdir=/hpcwork/jrc_combine/joana/mimic-clinical-scores
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=00:30:00
#SBATCH --output=logs/full/slurm-%j.out
#SBATCH --error=logs/full/slurm-%j.err

set -euo pipefail

if [[ "${CONFIRM_FULL:-}" != "YES" ]]; then
  echo "Set CONFIRM_FULL=YES to submit the deliberate full-cohort run." >&2
  exit 2
fi
PROJECT_ROOT="${PROJECT_ROOT:-/hpcwork/jrc_combine/joana/mimic-clinical-scores}"
MIMIC_ROOT="${MIMIC_ROOT:-/hpcwork/jrc_combine/joana/mimic/data}"
DATABASE="${DATABASE:-$PROJECT_ROOT/work/full/saps_ii.duckdb}"
OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_ROOT/outputs/full/saps_ii}"
SPILL_DIRECTORY="${SPILL_DIRECTORY:-$PROJECT_ROOT/work/full/spill}"
DUCKDB_THREADS="${DUCKDB_THREADS:-${SLURM_CPUS_PER_TASK:-8}}"
DUCKDB_MEMORY_LIMIT="${DUCKDB_MEMORY_LIMIT:-48GB}"

mkdir -p "$PROJECT_ROOT/inputs" "$PROJECT_ROOT/logs/full" "$PROJECT_ROOT/work/full" "$OUTPUT_DIR" "$SPILL_DIRECTORY"
chmod 700 "$PROJECT_ROOT/inputs" "$PROJECT_ROOT/logs/full" "$PROJECT_ROOT/work/full" "$OUTPUT_DIR" "$SPILL_DIRECTORY"

if [[ -z "${FULL_COHORT_FILE:-}" ]]; then
  FULL_COHORT_FILE="$PROJECT_ROOT/inputs/cohort_all_icu.parquet"
  "$PROJECT_ROOT/.venv/bin/python" -m mimic_clinical_scores prepare-all-icu-cohort \
    --project-root "$PROJECT_ROOT" \
    --mimic-root "$MIMIC_ROOT" \
    --output "$FULL_COHORT_FILE" \
    --manifest-output "$PROJECT_ROOT/inputs/cohort_all_icu_manifest.json" \
    --allow-clinical-scan
fi

"$PROJECT_ROOT/.venv/bin/python" -m mimic_clinical_scores run-all \
  --mode full \
  --project-root "$PROJECT_ROOT" \
  --mimic-root "$MIMIC_ROOT" \
  --cohort-file "$FULL_COHORT_FILE" \
  --database "$DATABASE" \
  --output-dir "$OUTPUT_DIR" \
  --log-dir "$PROJECT_ROOT/logs/full" \
  --spill-directory "$SPILL_DIRECTORY" \
  --threads "$DUCKDB_THREADS" \
  --memory-limit "$DUCKDB_MEMORY_LIMIT" \
  --allow-clinical-scan \
  --confirm-full
