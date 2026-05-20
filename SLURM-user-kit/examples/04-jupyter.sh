#!/bin/bash
# ============================================================
# 04-jupyter.sh — Jupyter Notebook on a GPU node, browser on your laptop
# ============================================================
# This is what `gpu-jupyter` (the helper command) wraps. Use this if
# you want to customize beyond the helper's defaults.
#
# After submitting, look at logs/jupyter-<jobid>.out for the SSH tunnel
# command to paste into your laptop terminal.
# ============================================================

#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=08:00:00
#SBATCH --job-name=jupyter
#SBATCH --output=logs/jupyter-%j.out

set -euo pipefail
mkdir -p logs

source /tools/miniconda3/etc/profile.d/conda.sh
conda activate research                   # ← EDIT: env that has jupyterlab installed
                                          #   conda install -c conda-forge jupyterlab

# Pick a random port in the user range to avoid collisions
PORT=$(shuf -i 8000-9999 -n 1)
NODE=$(hostname -s)
LOGIN_HOST=rbcdsaidgx                     # ← EDIT if your DNS name differs

cat <<EOF
============================================================
  Jupyter is starting on $NODE:$PORT.
  In a terminal ON YOUR LAPTOP, run this SSH tunnel command:

    ssh -N -L 8888:localhost:$PORT $USER@$LOGIN_HOST

  Then open in your laptop's browser:

    http://localhost:8888

  The token to paste is in the Jupyter startup log below.
  When you're done, run 'scancel $SLURM_JOB_ID' on the cluster
  (or just close your laptop SSH tunnel — the job will time out).
============================================================
EOF

# Use --no-browser since there's no display; bind to all interfaces
# so the tunnel reaches it.
jupyter lab \
    --no-browser \
    --port="$PORT" \
    --ip=0.0.0.0 \
    --notebook-dir="$HOME"
