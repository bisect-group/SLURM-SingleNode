# Planning Decisions

This file records the decisions made before implementation work begins. Update it during the review loop when the desired behavior changes.

## Generalization Model

Use hardware/site profiles as the main generalization mechanism.

A profile is a reviewed configuration for one kind of single-node server. It should select or define:

- Cluster identity and displayed name.
- Hostname and inventory target.
- CPU count, sockets, cores, threads, and memory.
- GPU availability, GPU type, count, and GRES behavior.
- Partition names and defaults.
- Tier definitions.
- Storage features.
- Login policy.
- Documentation labels and examples.

Initial profiles should cover:

- The existing DGX/V100 host.
- A generic NVIDIA GPU server.
- A CPU-only server.

## Hardware Discovery

Use discover-then-pin.

The setup should provide a discovery workflow that gathers candidate values from tools such as `slurmd -C`, `lscpu`, `free`, and `nvidia-smi`, but the final values should be written into a reviewed profile. The playbooks should use pinned profile values rather than changing node definitions automatically every run.

## Accounting

Keep `slurmdbd` and MariaDB accounting as the default.

Reason: the intended policy includes different user tiers, priorities, compute slices, job limits, and historical reporting. Slurm accounting is the clean place to represent those associations and QoS limits.

## Tier Policy

Use arbitrary profile-defined tiers.

Tier names and limits should not be hardcoded to `gpu1`, `gpu2`, `gpu3`, `gpu4`, or `deadline`. A profile should be able to define tiers such as:

- CPU-only small tier.
- Standard CPU tier.
- Small GPU tier.
- Large GPU tier.
- Temporary high-priority tier.

Each tier should be able to define CPU, memory, GPU, job-count, submit-count, and priority policy. GPU fields must be optional so CPU-only profiles are natural.

## Login Policy

Use constrained login as the default.

Users should be able to SSH into the all-in-one node for:

- Editing files.
- Copying, moving, syncing, and organizing data.
- Submitting jobs.
- Checking queue and job state.
- Running status tools such as `htop`, `btop`, `gpustat`, and `nvidia-smi`.

Users should not run real CPU or GPU workloads directly in their login shell. The future design should constrain login sessions and make Slurm examples the normal route for CPU jobs, GPU jobs, and notebooks.

Strict Slurm-only SSH is not the default because the same machine is the login node and the compute node. It can remain a possible optional mode for environments with a separate login path.

## Interactive Workflows

Keep interactive workflows as examples only for now.

Do not add helper commands such as `cpu-shell`, `gpu-shell`, `cpu-jupyter`, or `gpu-jupyter` in the first generalization pass. Instead, document exact `srun` and `sbatch` examples for:

- CPU interactive shell.
- GPU interactive shell.
- CPU Jupyter notebook.
- GPU Jupyter notebook.

## Storage Policy

Use optional storage features per profile.

Profiles should independently enable or disable:

- `/home` quota management.
- Shared data root.
- Archive root.
- Scratch path.
- Shared conda installation.

The existing DGX profile can keep `/home`, `/storage/nas`, `/storage/nas/_archive`, `/scratch`, and `/tools/miniconda3`. Smaller or CPU-only servers should not have to provide those paths unless explicitly enabled.

## Documentation Loop

Use `docs/current-status.md`, `docs/drawbacks.md`, and this file as the planning artifacts. The expected workflow is:

1. Review these docs.
2. Edit or annotate them with desired changes.
3. Re-read and refine the plan.
4. Implement code changes only after the desired generalized behavior is clear.

