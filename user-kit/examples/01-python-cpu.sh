#!/usr/bin/env bash
#SBATCH --partition=compute
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=01:00:00
#SBATCH --job-name=python-cpu

set -euo pipefail

echo "Working directory: $PWD"
echo "SLURM job: $SLURM_JOB_ID"

python3 - <<'PY'
import os
print("Use /data/%s for persistent large outputs when available." % os.environ["USER"])
print("TMPDIR:", os.environ.get("TMPDIR", "(profile default)"))
PY
