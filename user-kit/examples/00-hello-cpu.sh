#!/usr/bin/env bash
#SBATCH --partition=compute
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --time=00:05:00
#SBATCH --job-name=hello-cpu

set -euo pipefail

echo "Running on $(hostname)"
echo "Job ID: ${SLURM_JOB_ID}"
echo "CPUs: ${SLURM_CPUS_PER_TASK:-1}"
python3 - <<'PY'
import platform
print("Hello from Python", platform.python_version())
PY
