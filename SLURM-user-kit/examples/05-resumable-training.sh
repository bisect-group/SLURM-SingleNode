#!/bin/bash
# ============================================================
# 05-resumable-training.sh — long training that survives walltime limits
# ============================================================
# Pattern: your code checkpoints every N minutes. SLURM kills you at the
# --time limit. You re-submit and the script picks up from the last
# checkpoint. With --signal, you also get a clean shutdown 60s before
# the timeout — long enough to write a final checkpoint.
# ============================================================

#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=24:00:00
#SBATCH --signal=B:USR1@60                # send SIGUSR1 60s before time limit
#SBATCH --job-name=long-train
#SBATCH --output=logs/%x-%j.out
#SBATCH --requeue                          # if node fails, SLURM re-queues us

set -euo pipefail
mkdir -p logs

source /tools/miniconda3/etc/profile.d/conda.sh
conda activate research

# Checkpoint dir survives across job restarts (it's on NAS, not scratch!)
CKPT_DIR="/storage/nas/$USER/checkpoints/long-train"
mkdir -p "$CKPT_DIR"

# Re-submit ourselves before exiting if we got SIGUSR1
trap "echo 'Caught SIGUSR1 — saving checkpoint and re-queuing'; \
      touch $CKPT_DIR/PLEASE_REQUEUE; \
      kill -TERM $PYTHON_PID; \
      wait $PYTHON_PID; \
      sbatch $0; \
      exit 0" USR1

echo "Starting/resuming training from $CKPT_DIR ..."

python train.py \                          # ← EDIT
    --checkpoint-dir "$CKPT_DIR" \
    --resume-if-available \
    --epochs 1000 &
PYTHON_PID=$!
wait $PYTHON_PID
EXIT_CODE=$?

# If training finished naturally (not interrupted), don't re-queue.
if [[ -f "$CKPT_DIR/TRAINING_DONE" ]]; then
    echo "Training complete."
    exit 0
fi

# If we exited cleanly but signal handler didn't fire, the job ran to
# completion of the script's main process — likely you reached the
# requested --epochs. Don't re-queue.
exit $EXIT_CODE

# Notes for your training script (train.py):
# - At every epoch end, save: $CKPT_DIR/latest.pt
# - At the start, check for $CKPT_DIR/latest.pt and load if --resume-if-available
# - When done, touch $CKPT_DIR/TRAINING_DONE
# - Handle SIGTERM: save a final checkpoint, then exit cleanly
