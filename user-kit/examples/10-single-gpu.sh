#!/usr/bin/env bash
#SBATCH --partition=compute
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --time=04:00:00
#SBATCH --job-name=single-gpu

set -euo pipefail

nvidia-smi
echo "SLURM_TMPDIR=${SLURM_TMPDIR:-not configured}"
python3 - <<'PY'
try:
    import torch
except Exception as exc:
    print("PyTorch is not installed in this environment:", exc)
else:
    print("CUDA available:", torch.cuda.is_available())
    print("CUDA devices:", torch.cuda.device_count())
PY
