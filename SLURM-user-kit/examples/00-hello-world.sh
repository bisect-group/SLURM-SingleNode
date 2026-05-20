#!/bin/bash
# ============================================================
# 00-hello-world.sh — your first SLURM job
# ============================================================
# Submit with:  sbatch 00-hello-world.sh
# Watch with:   squeue --me
# Read output:  cat slurm-<jobid>.out
# ============================================================

#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
#SBATCH --time=00:05:00
#SBATCH --job-name=hello

echo "============================================================"
echo "Job ID         : $SLURM_JOB_ID"
echo "Job name       : $SLURM_JOB_NAME"
echo "User           : $USER"
echo "Node           : $(hostname)"
echo "Start time     : $(date)"
echo "Working dir    : $(pwd)"
echo "============================================================"

echo
echo "=== GPU access ==="
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv

echo
echo "=== CPU info (just my slice) ==="
nproc
echo "$SLURM_CPUS_ON_NODE CPUs allocated to me"

echo
echo "=== Per-job scratch (auto-cleaned after job) ==="
ls -la "/scratch/$SLURM_JOB_ID/" 2>/dev/null || echo "(scratch not set up — that's fine)"

echo
echo "============================================================"
echo "Hello from inside my SLURM job!"
echo "End time       : $(date)"
echo "============================================================"
