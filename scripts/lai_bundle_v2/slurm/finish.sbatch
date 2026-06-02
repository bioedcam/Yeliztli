#!/bin/bash
# LAI v2 rebuild — final phases after the gnomix array: 06 (validation:
# trios -> phasing switch error + LAI accuracy) and 07 (assemble bundle +
# checksums + metadata). Depends on the phase-05 array via afterok.
#
#SBATCH --job-name=lai_finish
#SBATCH --partition=gpu
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=24:00:00
#SBATCH --output=lai_finish_%j.out

set -euo pipefail

# shellcheck source=/dev/null
source "${CONDA_SH:-$HOME/miniconda3/etc/profile.d/conda.sh}"
conda activate "${CONDA_ENV:-lai_bundle}"

SCRIPTS_DIR="${LAI_SCRIPTS_DIR:-$SLURM_SUBMIT_DIR}"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] finish: phases 06 07 (WORKDIR=$WORKDIR)"
bash "$SCRIPTS_DIR/run_rebuild.sh" 06 07
