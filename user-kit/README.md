# Slurm Single-Node User Kit

This user kit is generated around the generalized single-node Slurm model.
The same machine is the login node, compute node, and storage entrypoint.

Use the login shell for editing, file movement, queue checks, and job
submission. Run real compute through Slurm with `sbatch` or `srun`.

## First CPU Job

```bash
sbatch examples/00-hello-cpu.sh
squeue --me
```

## GPU Jobs

GPU examples are valid only on GPU profiles. CPU-only profiles reject GPU
requests.

```bash
sbatch examples/10-single-gpu.sh
```

## Storage

Profiles may expose:

- `/home/$USER` for code, configs, environments, and small outputs.
- `/data/$USER` for persistent datasets, checkpoints, and large results.
- `/scratch/$USER` and `SLURM_TMPDIR` only on scratch-enabled profiles.

Check the site MOTD or ask an admin which profile is active.
