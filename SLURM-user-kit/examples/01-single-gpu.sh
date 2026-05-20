#!/bin/bash
# ============================================================
# 01-single-gpu.sh — train a model on one GPU (the common case)
# ============================================================
# Edit the marked sections for your project, then submit:
#   sbatch 01-single-gpu.sh
# ============================================================

#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8        # dataloader workers benefit from more cores
#SBATCH --mem=32G                # plenty for most models on one V100
#SBATCH --time=12:00:00          # max wallclock — job is killed at this point
#SBATCH --job-name=train
#SBATCH --output=logs/%x-%j.out  # %x=job-name, %j=job-id
#SBATCH --error=logs/%x-%j.err

set -euo pipefail                # die on any error, undefined var, or pipe failure
mkdir -p logs                    # in case --output dir doesn't exist

# ─── Environment ──────────────────────────────────────────────
source /tools/miniconda3/etc/profile.d/conda.sh
conda activate research          # ← EDIT: your conda env name

# Useful runtime flags
export PYTHONUNBUFFERED=1        # log lines appear immediately, not buffered
export CUDA_LAUNCH_BLOCKING=0    # set =1 only when debugging CUDA errors
export NCCL_P2P_LEVEL=NVL        # use NVLink for any peer ops

# ─── Stage data to fast local scratch (optional but recommended) ──
# Datasets on /storage/nas are slow over NFS. Copy to /scratch first.
# DATA_SRC=/storage/nas/$USER/datasets/imagenet
# DATA_DST=/scratch/$SLURM_JOB_ID/data
# mkdir -p "$DATA_DST"
# echo "Staging dataset to $DATA_DST ..."
# rsync -a "$DATA_SRC/" "$DATA_DST/"

# ─── Banner ───────────────────────────────────────────────────
echo "============================================================"
echo "Job $SLURM_JOB_ID on $(hostname) at $(date)"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
echo "============================================================"

# ─── Run training ─────────────────────────────────────────────
cd "$SLURM_SUBMIT_DIR"           # back to where you ran sbatch from

python train.py \                # ← EDIT: your training command
    --data-dir /storage/nas/$USER/datasets/cifar10 \
    --output-dir /storage/nas/$USER/runs/$SLURM_JOB_ID \
    --batch-size 256 \
    --epochs 100 \
    --lr 1e-3

# ─── Done ─────────────────────────────────────────────────────
echo "============================================================"
echo "Finished at $(date)"
echo "============================================================"
