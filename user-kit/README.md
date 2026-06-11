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

## Tiers

Use the default tier for routine work. Admins may grant higher tiers by setting
your Slurm QoS in `users.yml`.

Common QoS names use the site prefix:

- `ssn-standard`
- `ssn-priority`
- `ssn-emergency`

Request a tier explicitly only when you know you have access:

```bash
sbatch --qos=ssn-priority examples/00-hello-cpu.sh
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
- `/scratch/$USER` for rebuildable caches and temporary files.
- `SLURM_TMPDIR` for per-job scratch on scratch-enabled profiles.

When `SLURM_TMPDIR` exists, use it for temporary job working data:

```bash
mkdir -p "$SLURM_TMPDIR/work"
```

Scratch is not durable. Move outputs that matter into `/data/$USER`.

## Interactive Work

Interactive sessions still go through Slurm:

```bash
examples/20-interactive-srun.sh
```

Remote editors and login shells are for editing, queue checks, file movement,
and job submission. Direct compute on login is prohibited by policy and
constrained technically.

Check the site MOTD or ask an admin which profile is active.
