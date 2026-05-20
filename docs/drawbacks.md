# Drawbacks Before Generalization

This file records known blockers and design issues in the current implementation before converting the repo into a reusable single-node Slurm setup for DGX, generic GPU, and CPU-only servers.

## Hardware And Branding Are Hardcoded

The repo is currently Tesla/DGX/V100-specific in many places:

- `group_vars/all.yml` hardcodes `tesla`, `rbcdsaidgx`, 8 V100 GPUs, DGX CPU topology, memory, and storage paths.
- `slurm.conf.j2` and `gres.conf.j2` assume one 8-GPU V100 node with fixed NUMA/core bindings.
- `gpu_isolation` assumes NVIDIA GPUs and DGX-1 NVLink health expectations.
- Admin scripts, MOTD, login banner, user docs, and examples all use `tesla-*`, DGX-1, V100, NVLink, and `rbcdsaidgx` language.
- Installed paths such as `/etc/tesla-cluster`, `/usr/local/lib/tesla-cluster`, and `tesla-*` commands make the implementation site-specific.

This needs to become profile-driven before the code can safely support non-DGX GPU servers or CPU-only servers.

## No Profile Model Yet

There is no hardware/site profile layer. The current setup has one implicit profile embedded throughout the repo.

The generalized design needs explicit profiles for at least:

- Existing DGX/V100 machine.
- Generic NVIDIA GPU single-node server.
- CPU-only single-node server.

Profiles should carry reviewed values for hostname, cluster name, node resources, partitions, GRES, tiers, storage features, login policy, and user-facing labels.

## No CPU-Only Mode

The current setup assumes GPUs exist:

- `site.yml` asserts that `nvidia-smi` reports exactly `num_gpus`.
- `slurm.conf.j2` always sets `GresTypes=gpu` and GPU accounting TRES.
- `verify.yml` expects GPU GRES and NVLink checks.
- `gpu_isolation` is always part of the main role sequence.
- User docs and examples assume `#SBATCH --partition=gpu` and `#SBATCH --gres=gpu:N`.

CPU-only servers need a clean path with no NVIDIA driver, no GRES, no NVLink checks, no GPU udev rules, and CPU/Jupyter examples that still require Slurm for computation.

## Tier Model Is Hardcoded

Tiers are not fully data-driven:

- Python tools hardcode `VALID_TIERS = {"none", "gpu1", "gpu2", "gpu3", "gpu4", "deadline"}`.
- QoS names are assumed to be `tesla-<tier>`.
- Unix group names are assumed to be `tesla-<tier>`.
- Shell scripts check specific tier names or assume Tesla naming.
- User docs explain fixed `gpu1/gpu2/gpu3/gpu4/deadline` tiers.

The target model is arbitrary profile-defined tiers, including CPU-only tiers, GPU tiers, temporary priority tiers, and custom local names.

## Storage Assumptions Are Too Rigid

The current storage model assumes every server has:

- `/home` with user quotas enabled.
- `/storage/nas`.
- `/storage/nas/_archive`.
- `/scratch`.
- `/tools/miniconda3`.

These should become optional profile features. A smaller server may have no NAS, no quota support, no shared conda, or a different scratch path.

The Python user-sync tool also hardcodes storage paths and quota size, so it will need configuration input rather than constants.

## Login Enforcement Is Incomplete

The desired future policy is constrained login:

- Users may SSH in for file management, editing, queue/status commands, `htop`, `btop`, `gpustat`, `nvidia-smi`, and Slurm submission.
- Real CPU or GPU computation should go through Slurm.
- CPU-only notebooks should run through documented Slurm examples.

The current implementation mainly hides GPUs outside Slurm by clearing `CUDA_VISIBLE_DEVICES` and using cgroup device constraints. It does not meaningfully prevent CPU computation from a login shell.

Strict `pam_slurm_adopt` is not a good default for this all-in-one design because users need to SSH into the same machine to submit jobs. It may be useful only as an optional mode for sites with a separate bastion/login workflow.

## `pam_slurm_adopt` Is Soft

The current PAM template uses:

```text
account sufficient pam_slurm_adopt.so
```

That is a soft adoption posture. It can adopt sessions into jobs when possible, but it is not a strict deny policy for all non-Slurm sessions. The comments also describe stricter possibilities without implementing a complete production policy.

Future work should separate:

- Constrained all-in-one login policy.
- Optional strict compute-node-only policy.
- Admin bypass behavior.
- Failure behavior when Slurm, PAM, or cgroups are not healthy.

Official reference: https://slurm.schedmd.com/pam_slurm_adopt.html

## Cgroup Configuration Needs Generalization

The current `cgroup.conf.j2` enables CPU, RAM, and device constraints for all deployments:

- `ConstrainDevices=yes`
- `ConstrainRAMSpace=yes`
- `ConstrainCores=yes`

This is directionally right for Slurm jobs, but profile-specific behavior is needed for GPU-less systems and login-session constraints.

The future implementation should also ensure the Slurm node definitions match detected hardware closely, reserve enough memory for system/login processes, and keep job cgroups distinct from login-shell constraints.

Official reference: https://slurm.schedmd.com/cgroup.conf.html

## Per-Job Scratch Is Under-Specified

The current config creates `/scratch` and sets `TmpFS=/scratch`, but the docs describe `/scratch/$SLURM_JOB_ID` as per-job scratch that is auto-cleaned. That behavior is not fully represented as a robust job-container or prolog/epilog policy in the current repo.

Future work should decide whether to use Slurm `job_container/tmpfs`, prolog/epilog-managed scratch directories, or a simpler documented shared scratch path.

Official reference: https://slurm.schedmd.com/job_container_tmpfs.html

## User Sync Has Embedded Site Policy

`tools/sync_users.py` currently embeds policy constants:

- CSV path.
- lock path.
- backup path.
- archive root.
- NAS root.
- home root.
- user group name.
- valid tier names.
- default tier.
- quota size and filesystem.
- QoS prefix.

This makes the tool hard to reuse across profiles. It should read generated config or explicit CLI arguments from the Ansible profile instead.

## `sync_users.yml --check` Does Not Actually Dry-Run User Sync

The dry-run log shows that when the playbook itself is run with Ansible check mode, command tasks are skipped:

- CSV validation skipped.
- `sync_users.py --check` skipped.
- planned changes output was empty.

The playbook intends to show planned user changes, so the dry-run command should run even in Ansible check mode when it is read-only.

## `gpu_isolation` References Undefined `tesla_users`

The checked-in apply log shows this ignored error:

```text
Error while resolving value for '_raw_params': 'tesla_users' is undefined
```

The failing task tries to remove regular users from the `video` group by looping over `tesla_users`, but no such variable is defined. The role also runs before `user_policy`, where the `tesla_users` group is created.

Future work should either derive users from the CSV, query group membership after the group exists, or move this cleanup into a user-management step.

## User Kit Is Out Of Sync

`SLURM-user-kit/README.md`, `INSTALL.md`, and `USER-GUIDE.md` mention helper commands that are not present in `SLURM-user-kit/helpers/`:

- `gpu-shell`
- `gpu-jupyter`

Only `myjobs`, `myresources`, and `job-watch` are currently present.

Because the chosen future direction is examples-only for interactive workflows, these references should either be removed or replaced with documented `srun`/`sbatch` examples.

## Example Scripts Have Runtime Command Issues

Bash syntax checks pass, but several example scripts put comments after line-continuation backslashes in multi-line commands. That makes the following arguments run as separate shell commands at runtime.

Affected examples include:

- `SLURM-user-kit/examples/01-single-gpu.sh`
- `SLURM-user-kit/examples/02-multi-gpu-ddp.sh`
- `SLURM-user-kit/examples/03-array-sweep.sh`
- `SLURM-user-kit/examples/05-resumable-training.sh`

These should be fixed before relying on the examples as canonical user workflows.

## Verification Gaps

Local planning-time checks found:

- Python tools compile.
- Bash scripts parse.
- `ansible-playbook` is not installed locally, so Ansible syntax checks could not run here.

Future implementation should add repeatable verification that does not depend on the target DGX host, such as YAML syntax checks, Jinja rendering tests for profiles, Python unit tests for CSV/tier config, and shell linting for examples.

