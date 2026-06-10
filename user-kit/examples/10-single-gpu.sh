#!/usr/bin/env bash
#SBATCH --partition=compute
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=04:00:00
#SBATCH --job-name=single-gpu

set -euo pipefail

nvidia-smi
python3 - <<'PY'
try:
    import torch
except Exception as exc:
    print("PyTorch is not installed in this environment:", exc)
else:
    print("CUDA available:", torch.cuda.is_available())
    print("CUDA devices:", torch.cuda.device_count())
PY
