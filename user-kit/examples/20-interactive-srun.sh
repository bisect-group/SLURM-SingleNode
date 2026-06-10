#!/usr/bin/env bash
set -euo pipefail

exec srun \
  --partition=compute \
  --cpus-per-task=2 \
  --mem=4G \
  --time=01:00:00 \
  --pty bash
