#!/usr/bin/env bash
#SBATCH --job-name=sofa-r7-full
#SBATCH --chdir=/hpcwork/jrc_combine/joana/mimic-clinical-scores
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=00:30:00
#SBATCH --output=logs/full/sofa-reverse-7d-%j.out
#SBATCH --error=logs/full/sofa-reverse-7d-%j.err

set -euo pipefail

if [[ "${CONFIRM_FULL:-}" != "YES" ]]; then
  echo "Set CONFIRM_FULL=YES to run reverse hourly SOFA for the full ICU cohort." >&2
  exit 2
fi

PROJECT_ROOT="${PROJECT_ROOT:-/hpcwork/jrc_combine/joana/mimic-clinical-scores}"
MIMIC_ROOT="${MIMIC_ROOT:-/hpcwork/jrc_combine/joana/mimic/data}"
FULL_COHORT_FILE="${FULL_COHORT_FILE:-$PROJECT_ROOT/inputs/cohort_all_icu.parquet}"
DATABASE="${DATABASE:-$PROJECT_ROOT/work/full/sofa_hourly_reverse_7d.duckdb}"
OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_ROOT/outputs/full/sofa_hourly_reverse_7d}"
LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/logs/full/sofa_hourly_reverse_7d}"
SPILL_DIRECTORY="${SPILL_DIRECTORY:-$PROJECT_ROOT/work/full/sofa_hourly_reverse_7d_spill}"
DUCKDB_THREADS="${DUCKDB_THREADS:-${SLURM_CPUS_PER_TASK:-8}}"
DUCKDB_MEMORY_LIMIT="${DUCKDB_MEMORY_LIMIT:-26GB}"

if [[ ! -f "$FULL_COHORT_FILE" ]]; then
  echo "Full ICU cohort allowlist does not exist: $FULL_COHORT_FILE" >&2
  exit 2
fi

mkdir -p "$LOG_DIR" "$PROJECT_ROOT/work/full" "$OUTPUT_DIR" "$SPILL_DIRECTORY"
chmod 700 "$LOG_DIR" "$PROJECT_ROOT/work/full" "$OUTPUT_DIR" "$SPILL_DIRECTORY"

"$PROJECT_ROOT/.venv/bin/python" -m mimic_clinical_scores run-all \
  --score sofa_hourly_reverse_7d \
  --mode full \
  --project-root "$PROJECT_ROOT" \
  --mimic-root "$MIMIC_ROOT" \
  --cohort-file "$FULL_COHORT_FILE" \
  --database "$DATABASE" \
  --output-dir "$OUTPUT_DIR" \
  --log-dir "$LOG_DIR" \
  --spill-directory "$SPILL_DIRECTORY" \
  --threads "$DUCKDB_THREADS" \
  --memory-limit "$DUCKDB_MEMORY_LIMIT" \
  --allow-clinical-scan \
  --confirm-full
