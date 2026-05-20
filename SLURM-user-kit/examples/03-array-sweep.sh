#!/bin/bash
# ============================================================
# 03-array-sweep.sh — run a hyperparameter sweep as a job array
# ============================================================
# One sbatch submission spawns many independent jobs, each with a
# different $SLURM_ARRAY_TASK_ID. You map that ID to hyperparameters.
#
# Submit:    sbatch 03-array-sweep.sh
# Watch:     squeue --me
# Cancel:    scancel <array-job-id>          # all of them
#            scancel <array-job-id>_<task>   # one task
# ============================================================

#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH --job-name=sweep
#SBATCH --output=logs/sweep/%A_%a.out      # %A = array job id, %a = task id
#SBATCH --error=logs/sweep/%A_%a.err
#SBATCH --array=0-9                        # ← EDIT: 10 tasks, IDs 0..9
                                            #   format: 0-29        → 30 tasks
                                            #           0-29%5      → 30 tasks, max 5 at once
                                            #           1,3,5,7     → just those four

set -euo pipefail
mkdir -p logs/sweep

source /tools/miniconda3/etc/profile.d/conda.sh
conda activate research

# ─── Hyperparameter grid ──────────────────────────────────────
# This script implements a small grid by indexing arrays with $SLURM_ARRAY_TASK_ID.
# For a 2D sweep, use modulo math (see below).

LRS=(1e-4 3e-4 1e-3 3e-3 1e-2)
SEEDS=(0 1)

# 5 LRs × 2 seeds = 10 combinations → matches --array=0-9 above.
N_LRS=${#LRS[@]}
LR_IDX=$((SLURM_ARRAY_TASK_ID % N_LRS))
SEED_IDX=$((SLURM_ARRAY_TASK_ID / N_LRS))

LR=${LRS[$LR_IDX]}
SEED=${SEEDS[$SEED_IDX]}

RUN_NAME="lr${LR}-seed${SEED}"
OUT_DIR="/storage/nas/$USER/runs/sweep-$SLURM_ARRAY_JOB_ID/$RUN_NAME"
mkdir -p "$OUT_DIR"

echo "============================================================"
echo "Task $SLURM_ARRAY_TASK_ID: lr=$LR seed=$SEED"
echo "Output: $OUT_DIR"
echo "============================================================"

cd "$SLURM_SUBMIT_DIR"

python train.py \                            # ← EDIT: your training script
    --output-dir "$OUT_DIR" \
    --lr "$LR" \
    --seed "$SEED" \
    --epochs 50

echo "Done task $SLURM_ARRAY_TASK_ID at $(date)"

# ─── Aggregating results after the sweep ──────────────────────
# After all tasks finish, summarize:
#
#   python summarize_sweep.py \
#       --runs-dir /storage/nas/$USER/runs/sweep-<job-id>/ \
#       --output sweep-results.csv
