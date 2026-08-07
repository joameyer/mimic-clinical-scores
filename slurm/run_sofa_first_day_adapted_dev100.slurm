#!/usr/bin/env bash
#SBATCH --job-name=sofa-dev100
#SBATCH --chdir=/hpcwork/jrc_combine/joana/mimic-clinical-scores
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=00:20:00
#SBATCH --output=logs/dev100/sofa_first_day_adapted/slurm-%j.out
#SBATCH --error=logs/dev100/sofa_first_day_adapted/slurm-%j.err

set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/hpcwork/jrc_combine/joana/mimic-clinical-scores}"
MIMIC_ROOT="${MIMIC_ROOT:-/hpcwork/jrc_combine/joana/mimic/data}"
COHORT_FILE="${COHORT_FILE:-$PROJECT_ROOT/inputs/cohort_dev100.parquet}"
DATABASE="${DATABASE:-$PROJECT_ROOT/work/dev100/sofa_first_day_adapted.duckdb}"
OUTPUT_DIR="${OUTPUT_DIR:-$PROJECT_ROOT/outputs/dev100/sofa_first_day_adapted}"
LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/logs/dev100/sofa_first_day_adapted}"
SPILL_DIRECTORY="${SPILL_DIRECTORY:-$PROJECT_ROOT/work/dev100/sofa_first_day_adapted_spill}"
DUCKDB_THREADS="${DUCKDB_THREADS:-${SLURM_CPUS_PER_TASK:-4}}"
DUCKDB_MEMORY_LIMIT="${DUCKDB_MEMORY_LIMIT:-12GB}"

mkdir -p "$LOG_DIR" "$PROJECT_ROOT/work/dev100" "$OUTPUT_DIR" "$SPILL_DIRECTORY"
chmod 700 "$LOG_DIR" "$PROJECT_ROOT/work/dev100" "$OUTPUT_DIR" "$SPILL_DIRECTORY"

"$PROJECT_ROOT/.venv/bin/python" -m mimic_clinical_scores run-all \
  --score sofa_first_day_adapted \
  --mode dev100 \
  --project-root "$PROJECT_ROOT" \
  --mimic-root "$MIMIC_ROOT" \
  --cohort-file "$COHORT_FILE" \
  --database "$DATABASE" \
  --output-dir "$OUTPUT_DIR" \
  --log-dir "$LOG_DIR" \
  --spill-directory "$SPILL_DIRECTORY" \
  --threads "$DUCKDB_THREADS" \
  --memory-limit "$DUCKDB_MEMORY_LIMIT" \
  --allow-clinical-scan
