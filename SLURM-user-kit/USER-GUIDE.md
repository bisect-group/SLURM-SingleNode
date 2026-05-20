---
title: "Tesla DGX-1 — Researcher Quick Start"
subtitle: "How to run your experiments on the cluster"
author: "WSAI, IIT Madras"
date: "May 2026"
titlepage: true
titlepage-color: "0F4C81"
titlepage-text-color: "FFFFFF"
toc: true
toc-own-page: true
colorlinks: true
linkcolor: NavyBlue
urlcolor: NavyBlue
code-block-font-size: \footnotesize
---

# Welcome

The Tesla DGX-1 is a shared GPU server with eight NVIDIA V100 GPUs.
To make it fair for everyone, jobs are scheduled through **SLURM** — you
describe what you want (how many GPUs, how much memory, for how long), and
SLURM runs your code when resources are free.

This guide gets you from zero to a running job in 10 minutes, then covers
the workflows you'll actually use day to day.

# Your first job in 60 seconds

After SSH-ing in:

```bash
# 1. Make a directory for your experiments
cd ~
mkdir -p first-job && cd first-job

# 2. Create a tiny test script
cat > hello.sh <<'EOF'
#!/bin/bash
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=00:05:00
#SBATCH --job-name=hello

echo "Running on $(hostname) at $(date)"
nvidia-smi
echo "Hello from inside my SLURM job"
EOF

# 3. Submit it
sbatch hello.sh
# → Submitted batch job 42

# 4. Watch it
squeue --me
# → JOBID PARTITION  NAME    USER  ST  TIME  NODES  NODELIST(REASON)
# →    42 gpu        hello   jash  R   0:03  1      rbcdsaidgx

# 5. When it finishes, look at the output
cat slurm-42.out
```

That's it. The script ran on a real GPU, the output is in
`slurm-42.out`, and you didn't have to ask anyone for permission.

# How SLURM thinks

```text
┌────────────────────┐
│   Login shell      │  ← you arrive here via SSH. CPU-only, no GPU.
│   /home/$USER      │     This is for editing, file management, submitting.
└──────────┬─────────┘
           │
           │ sbatch / srun
           ▼
┌────────────────────┐
│   SLURM scheduler  │  ← decides when your job runs
│   (slurmctld)      │     based on tier limits + queue order
└──────────┬─────────┘
           │
           ▼
┌────────────────────┐
│   Compute job      │  ← your code runs HERE with GPU + CPU + RAM
│   (cgroup-isolated)│     and disappears when finished
└────────────────────┘
```

The single most important thing to remember: **the GPUs are not on your
login shell**. If you SSH in and run `python train.py`, you'll be on the
CPU with no GPU access. Everything that uses a GPU must go through SLURM.

# Your tier

Every user is in a tier that controls how big a job you can request:

| Tier | Max GPUs / job | Max CPUs / job | Concurrent jobs | When you get this |
|------|---:|---:|---:|---|
| `gpu1` | 1 | 20 | 5 | **Default for everyone** |
| `gpu2` | 2 | 20 | 5 | On request for multi-GPU work |
| `gpu3` | 3 | 20 | 5 | On request |
| `gpu4` | 4 | 20 | 5 | One full NUMA half (NVLink-local) |
| `deadline` | 8 | 40 | 5 | Paper/grant crunches — admin-approved |

Find your tier:

```bash
sacctmgr show user $USER withassoc format=user,account,qos,defaultqos
```

The `defaultqos` column tells you your tier (e.g., `tesla-gpu1`).

## Asking for a bigger tier

Email the cluster admin with:

- Why you need more GPUs (which experiment, which paper)
- How long you'll need it (weeks vs months)
- Whether you can use 1-GPU jobs in parallel instead

Tier upgrades take effect immediately. Downgrades happen after the date
you agree on.

# The five SLURM commands you need

```bash
sbatch script.sh         # submit a job
squeue --me              # see your jobs
scancel <jobid>          # kill a job
srun --pty bash          # open an interactive GPU shell
sacct -j <jobid>         # see resources a finished job used
```

The five `#SBATCH` options you need:

```bash
#SBATCH --partition=gpu          # always gpu on this cluster
#SBATCH --gres=gpu:N             # how many GPUs (1, 2, 4, ...)
#SBATCH --cpus-per-task=N        # CPU cores
#SBATCH --mem=NG                 # RAM (e.g. 32G, 128G)
#SBATCH --time=HH:MM:SS          # max wallclock (job killed at this point)
```

Two more you'll want soon:

```bash
#SBATCH --job-name=my-experiment  # so squeue shows a meaningful name
#SBATCH --output=logs/%x-%j.out   # custom log location (%x=name, %j=jobid)
```

# Storage — where to put what

You have three places to put files. Choose based on what the data is.

| Path | Size | Speed | Persists | Use for |
|------|-----:|-------|---------|---------|
| `/home/$USER` | 200 GB hard | fast SSD | yes | code, small results, configs |
| `/storage/nas/$USER` | unlimited | NFS (slower) | yes | datasets, checkpoints, archives |
| `/scratch/$SLURM_JOB_ID` | shared 1 TB | local NVMe (fastest) | **NO** | per-job temp, dataloader cache |

**Rules of thumb:**

- Code, scripts, small CSVs → `/home`
- Big datasets, model checkpoints → `/storage/nas/$USER`
- Anything you'd happily lose when the job ends → `/scratch/$SLURM_JOB_ID`
  (automatically wiped after each job)

Check your `/home` quota anytime:

```bash
quota -u $USER
```

If you're near the 200 GB limit, move things to `/storage/nas/$USER`.

## Why this layout matters

Reading a 50 GB dataset directly from NFS (`/storage/nas`) every epoch is
slow and stresses the network. The recommended pattern:

```bash
# At the top of your sbatch script:
rsync -a /storage/nas/$USER/imagenet/ /scratch/$SLURM_JOB_ID/imagenet/

# Then point your code at /scratch/$SLURM_JOB_ID/imagenet/
# When the job ends, /scratch is auto-cleaned.
```

# Python environments

We provide a shared Miniconda at `/tools/miniconda3` so you don't need to
install your own. New users automatically pick it up on first login.

## First-time setup

```bash
# After your first login, check that conda is available:
which conda
# → /tools/miniconda3/bin/conda

# If not, log out and back in once. /etc/skel/.profile sourced conda for you.

# Create your own environment (envs live in ~/.conda/envs)
conda create -n research python=3.11 -y
conda activate research

# Install whatever you need from conda-forge or pip:
conda install -c conda-forge numpy pandas scikit-learn -y
pip install torch torchvision tensorboard wandb
```

We default to the `conda-forge` channel (Anaconda's `defaults` channel
requires per-user license acceptance and is intentionally disabled).
Almost everything in defaults is also on conda-forge.

## Using your env inside an sbatch job

```bash
#!/bin/bash
#SBATCH --partition=gpu
#SBATCH --gres=gpu:1
#SBATCH --time=04:00:00

source /tools/miniconda3/etc/profile.d/conda.sh
conda activate research

python train.py
```

## Big environments (>5 GB)

Some envs (especially deep learning ones with multiple CUDA toolkits) get
huge. If your conda env is pushing your 200 GB quota, move it to NAS:

```bash
# Move the env folder
mv ~/.conda/envs/big-env /storage/nas/$USER/conda-envs/big-env

# Symlink it back so conda still finds it
ln -s /storage/nas/$USER/conda-envs/big-env ~/.conda/envs/big-env

# Verify
conda env list
```

# Common workflows

The `examples/` directory ships with templates for each. Copy, edit, run.

## Single-GPU training

Use this for most ML experiments — your model fits on one V100 (32 GB).

```bash
sbatch examples/01-single-gpu.sh
```

Key knobs in the script:

- `--gres=gpu:1` — one GPU
- `--cpus-per-task=8` — eight CPU cores for the dataloader workers
- `--mem=32G` — plenty for most models
- `--time=12:00:00` — kill the job after 12h if it hasn't finished

## Multi-GPU training (DDP)

For models or batch sizes that need more than one GPU. **Stay on one NUMA
node when possible** — GPUs 0–3 share NVLink, GPUs 4–7 share NVLink, but
crossing between them goes over the slower CPU interconnect.

```bash
sbatch examples/02-multi-gpu-ddp.sh
```

You'll request `tesla-gpu2`, `tesla-gpu3`, or `tesla-gpu4` tier — talk to
the admin first.

## Hyperparameter sweep (job arrays)

When you want to try 50 learning rates, use a **job array** — one sbatch
submission becomes many independent jobs:

```bash
sbatch examples/03-array-sweep.sh
```

Each array task gets its own `$SLURM_ARRAY_TASK_ID`, which you use to
look up the hyperparameters for that run.

Array jobs all count against your tier's "concurrent jobs" limit (5 by
default) — so a 50-element array runs 5 at a time.

## Interactive debugging session

Sometimes you just need a Python REPL with a GPU. Use the `gpu-shell`
helper:

```bash
gpu-shell                 # 1 GPU, 4 CPUs, 16 GB RAM, 4 hours, default
gpu-shell 2               # 2 GPUs (only works if you're in gpu2+ tier)
gpu-shell 1 --time 8:00   # 1 GPU for 8 hours
```

You get a real shell on a compute node. Activate conda, run python, ipython, htop, whatever.

When you're done: `exit` (or just close the terminal) and the GPUs go back
to the queue.

## Jupyter notebook from your laptop

A two-step trick: run Jupyter inside a SLURM job on the cluster, then SSH
tunnel from your laptop to reach it.

On the cluster:

```bash
gpu-jupyter               # 1 GPU, 16 GB, 8 hours
```

The script prints a tunnel command to run on **your laptop**. Paste it,
open the URL it shows, and Jupyter runs on a real GPU with your notebook
in the browser on your laptop.

# Monitoring a running job

```bash
# Your queue
squeue --me

# Detailed view of one job
scontrol show job <jobid>

# What your job is actually doing on the GPU (only works if you SSH to the node)
ssh rbcdsaidgx
sudo -u $USER nvidia-smi   # only as your own user; admins can see all

# Resources the job is using right now
sstat -j <jobid> --format=jobid,maxrss,maxvmsize,avecpu

# After the job finishes — what did it actually use?
sacct -j <jobid> --format=jobid,jobname,state,elapsed,maxrss,reqmem,reqgres,exitcode
```

The `maxrss` from `sacct` tells you how much RAM your job actually used —
useful for right-sizing future `--mem=` requests.

# Cancel a job

```bash
scancel 42                # one job
scancel --me              # all your jobs (panic button)
scancel --state=pending   # only your pending jobs
```

# Frequent gotchas

## 1. "My job ran but the output is empty"

You probably forgot to `conda activate` inside the script. The login
shell environment doesn't carry into the job. Source conda + activate
your env at the top of the sbatch script.

## 2. "CUDA out of memory" — but I have a 32 GB V100"

Check what's actually loaded. PyTorch caches grow over training.
`torch.cuda.empty_cache()` between phases helps. If you're using DDP with
gradient accumulation, your effective batch is much larger than you
think.

## 3. "ssh works but nvidia-smi says no GPUs"

That's intentional — login shells don't have GPU access. Submit an
sbatch job or use `gpu-shell` for interactive work.

## 4. "Disk quota exceeded"

```bash
quota -u $USER                # check your usage
du -h --max-depth=1 ~ | sort -h | tail   # find what's big
```

Move large stuff to `/storage/nas/$USER/`.

## 5. "My job is stuck in PENDING (Resources)"

Someone else is using GPUs. `squeue` will show what's running. Be
patient, or request fewer GPUs.

## 6. "My job is PENDING (QOSMaxJobsPerUser)"

You've hit your tier's concurrent-job limit (5). Wait for one to
finish, or run fewer at a time.

## 7. "AssocMaxJobsLimit"

Same as above — you're at your concurrent limit.

## 8. "Invalid account or account/partition combination"

Your sacctmgr account is missing. Email the admin with your username and
ask them to re-run `sync_users.yml`.

## 9. "TIMEOUT" — my job got killed

You set `--time=02:00:00` and it took longer. Increase `--time` and
resubmit. Better: checkpoint your training every epoch and just resume
from the last checkpoint when the job hits the timeout.

## 10. "I can't find my output"

By default, `slurm-<jobid>.out` is written to the directory you ran
`sbatch` from — **not** your home directory. `find ~ -name "slurm-*.out"`
locates orphans.

# Cheatsheet

Print this and tape it to your desk.

```text
SUBMIT             sbatch script.sh
QUEUE              squeue --me
CANCEL             scancel <jobid>
INTERACTIVE        gpu-shell [N_gpus]
JUPYTER            gpu-jupyter [N_gpus]
HISTORY            sacct -u $USER --starttime=$(date -d '7 days ago' +%F)
MY TIER            sacctmgr show user $USER withassoc format=user,defaultqos
MY QUOTA           quota -u $USER

SBATCH HEADER MINIMUM:
  #SBATCH --partition=gpu
  #SBATCH --gres=gpu:1
  #SBATCH --cpus-per-task=8
  #SBATCH --mem=32G
  #SBATCH --time=04:00:00
  #SBATCH --job-name=my-exp
  #SBATCH --output=logs/%x-%j.out

STORAGE:
  /home/$USER          200 GB    code, configs, small results
  /storage/nas/$USER   ∞         datasets, checkpoints
  /scratch/$JOBID      shared    per-job tmp (AUTO-WIPED)

GPU NUMA:
  GPUs 0-3 → NUMA 0    cores 0-19 + 40-59   (NVLink-local)
  GPUs 4-7 → NUMA 1    cores 20-39 + 60-79  (NVLink-local)
  Avoid jobs that mix 0-3 with 4-7 if you care about bandwidth.

CONDA:
  Shared base: /tools/miniconda3
  Your envs:   ~/.conda/envs/
  Default channel: conda-forge
```

# Getting help

- Read this guide first
- Check `slurm-<jobid>.out` for errors
- Try `scontrol show job <jobid>` for state details
- For tier upgrades or account issues, email the cluster admin
- For SLURM concepts in general:
  - <https://slurm.schedmd.com/quickstart.html>
  - <https://slurm.schedmd.com/sbatch.html>
