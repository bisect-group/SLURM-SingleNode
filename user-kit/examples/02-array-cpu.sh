#!/usr/bin/env bash
#SBATCH --partition=compute
#SBATCH --array=1-4
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --time=00:10:00
#SBATCH --job-name=array-cpu

set -euo pipefail

echo "Array task: ${SLURM_ARRAY_TASK_ID}"
echo "Running on: $(hostname)"
python3 - <<'PY'
import os
task = os.environ.get("SLURM_ARRAY_TASK_ID")
print(f"Hello from array task {task}")
PY
