#!/usr/bin/env bash
#SBATCH --job-name=sofa-resp-audit
#SBATCH --chdir=/hpcwork/jrc_combine/joana/mimic-clinical-scores
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=00:20:00
#SBATCH --output=logs/full/sofa_first_day_adapted/respiratory-audit-%j.out
#SBATCH --error=logs/full/sofa_first_day_adapted/respiratory-audit-%j.err

set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/hpcwork/jrc_combine/joana/mimic-clinical-scores}"
DATABASE="${DATABASE:-$PROJECT_ROOT/work/full/sofa_first_day_adapted.duckdb}"
SPILL_DIRECTORY="${SPILL_DIRECTORY:-$PROJECT_ROOT/work/full/sofa_respiratory_audit_spill}"
DUCKDB_THREADS="${DUCKDB_THREADS:-${SLURM_CPUS_PER_TASK:-4}}"
DUCKDB_MEMORY_LIMIT="${DUCKDB_MEMORY_LIMIT:-18GB}"

mkdir -p "$PROJECT_ROOT/logs/full/sofa_first_day_adapted" "$SPILL_DIRECTORY"
chmod 700 "$PROJECT_ROOT/logs/full/sofa_first_day_adapted" "$SPILL_DIRECTORY"

"$PROJECT_ROOT/.venv/bin/python" scripts/audit_sofa_respiratory_missingness.py \
  --database "$DATABASE" \
  --threads "$DUCKDB_THREADS" \
  --memory-limit "$DUCKDB_MEMORY_LIMIT" \
  --spill-directory "$SPILL_DIRECTORY"
