#!/bin/bash
# ============================================================
# 02-multi-gpu-ddp.sh — DistributedDataParallel on multiple GPUs
# ============================================================
# Requires gpu2 / gpu3 / gpu4 / deadline tier. Request from admin first.
#
# IMPORTANT: stay on one NUMA node to use NVLink:
#   GPUs 0-3 are NVLink-connected (NUMA 0)
#   GPUs 4-7 are NVLink-connected (NUMA 1)
# SLURM auto-selects, but 4-GPU jobs may span NUMA. 2-GPU jobs usually don't.
# ============================================================

#SBATCH --partition=gpu
#SBATCH --gres=gpu:2             # ← EDIT: 2, 3, or 4 (your tier max)
#SBATCH --cpus-per-task=16       # ~8 cores per GPU is a good default
#SBATCH --mem=64G
#SBATCH --time=24:00:00
#SBATCH --job-name=train-ddp
#SBATCH --output=logs/%x-%j.out
#SBATCH --error=logs/%x-%j.err

set -euo pipefail
mkdir -p logs

source /tools/miniconda3/etc/profile.d/conda.sh
conda activate research

export PYTHONUNBUFFERED=1
export NCCL_P2P_LEVEL=NVL        # force NVLink for GPU-to-GPU
export NCCL_DEBUG=WARN           # uncomment if you suspect NCCL issues: export NCCL_DEBUG=INFO
export OMP_NUM_THREADS=1         # avoid CPU thread contention with DataLoader workers

echo "============================================================"
echo "Job $SLURM_JOB_ID with $SLURM_GPUS_ON_NODE GPUs on $(hostname)"
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader
nvidia-smi topo -m | head -20
echo "============================================================"

cd "$SLURM_SUBMIT_DIR"

# torchrun is the modern PyTorch DDP launcher.
# nproc_per_node MUST match --gres=gpu:N above.
NUM_GPUS=$(nvidia-smi -L | wc -l)

torchrun \
    --standalone \
    --nproc_per_node="$NUM_GPUS" \
    train_ddp.py \                       # ← EDIT: your DDP-aware script
    --data-dir /storage/nas/$USER/datasets/imagenet \
    --output-dir /storage/nas/$USER/runs/$SLURM_JOB_ID \
    --batch-size 64 \
    --epochs 90

echo "============================================================"
echo "Done at $(date)"
echo "============================================================"

# ─── Minimal train_ddp.py snippet (FYI, not part of this script) ──
#
#   import os, torch
#   import torch.distributed as dist
#   from torch.nn.parallel import DistributedDataParallel as DDP
#
#   dist.init_process_group("nccl")
#   local_rank = int(os.environ["LOCAL_RANK"])
#   torch.cuda.set_device(local_rank)
#   model = MyModel().cuda()
#   model = DDP(model, device_ids=[local_rank])
#   # ... rest of training loop
#   dist.destroy_process_group()
