# Current Status

This repository currently contains two related deliverables for a single-node Slurm deployment:

- `tesla-scheduler-v2/`: Ansible automation for installing and operating Slurm on one machine.
- `SLURM-user-kit/`: researcher-facing documentation, examples, and small queue/resource helper scripts.

The current implementation is tightly shaped around the Tesla DGX-1 host `rbcdsaidgx`. It assumes the same physical machine is the login node, compute node, and local storage entrypoint.

## Scheduler Implementation

`tesla-scheduler-v2/` installs and configures:

- Core Slurm services: `slurmctld`, `slurmd`, `slurmdbd`.
- Authentication and accounting dependencies: Munge, MariaDB, Slurm accounting database.
- Slurm configuration: `slurm.conf`, `gres.conf`, `cgroup.conf`, and `job_submit.lua`.
- User policy: Unix groups, login profile snippets, tier banner, resource limits.
- GPU isolation: cgroup device constraints, `pam_slurm_adopt`, and NVIDIA udev rules.
- Storage policy: `/home` quota enforcement, `/storage/nas` user directories, home archival for inactive users.
- Shared tooling: `/tools/miniconda3`, admin scripts, MOTD, logrotate, and systemd restart overrides.
- CSV user management: `users.csv` validation, user creation/deactivation, SSH key sync, Slurm account/QoS assignment.

The master setup flow is `site.yml`, with roles ordered as:

1. `slurm_base`
2. `storage_quotas`
3. `shared_miniconda`
4. `slurmdbd_setup`
5. `slurm_config`
6. `gpu_isolation`
7. `user_policy`
8. `admin_tools`

Supporting playbooks include `sync_users.yml`, `verify.yml`, `update_config.yml`, `restore_user.yml`, and the deprecated `add_user.yml` wrapper.

## Current Hardware Assumptions

The current profile is not yet represented as a profile. Its assumptions are embedded directly in variables, templates, scripts, and docs:

- Hostname: `rbcdsaidgx`.
- Cluster name and branding: `tesla`.
- GPU node: 8x NVIDIA Tesla V100-SXM2-32GB.
- GPU type: `v100`.
- CPU topology: 2 sockets, 20 cores per socket, 2 threads per core, 80 logical CPUs.
- Memory: `RealMemory=500000`.
- GPU layout: GPUs 0-3 bound to cores 0-19, GPUs 4-7 bound to cores 20-39.
- GPU fabric: DGX-1 NVLink hybrid cube-mesh with an expected 48 links.
- Default partition: `gpu`.
- Default walltime and max walltime: 48 hours.
- Storage layout: `/home`, `/storage/nas`, `/storage/nas/_archive`, `/scratch`.
- Shared software root: `/tools/miniconda3`.

## Current Policy Model

The policy model uses Slurm accounting and QoS tiers:

- Root Slurm account: `tesla`.
- Per-tier Slurm accounts: `none`, `gpu1`, `gpu2`, `gpu3`, `gpu4`, `deadline`.
- Per-tier QoS names: `tesla-none`, `tesla-gpu1`, `tesla-gpu2`, `tesla-gpu3`, `tesla-gpu4`, `tesla-deadline`.
- Per-tier Unix groups: `tesla-none`, `tesla-gpu1`, `tesla-gpu2`, `tesla-gpu3`, `tesla-gpu4`, `tesla-deadline`.
- User umbrella group: `tesla_users`.

The current tiers enforce max GPUs per job, max CPUs per job, max running/submitted jobs per user, and QoS priority. `deadline` gets a much higher priority but does not preempt running jobs.

`job_submit.lua` provides friendlier submit-time error messages when users request more CPUs or GPUs than their tier allows. Slurm accounting remains the authoritative enforcement mechanism.

## User Management

User state is intended to flow through `/etc/tesla-cluster/users.csv`.

The Python tools under `tesla-scheduler-v2/tools/` handle:

- Discovering existing Unix users into a starter CSV.
- Validating CSV schema and values.
- Creating active Unix users.
- Updating Unix group membership.
- Optionally managing a single SSH public key per user.
- Creating per-user NAS directories.
- Adding/modifying Slurm accounting associations.
- Archiving `/home/<user>` and deleting the Unix user when marked inactive.
- Leaving `/storage/nas/<user>` intact when a user is inactive.

The current CSV schema includes username, full name, email, SSH public key, tier, status, expiry date, UID, GID, created date, and notes.

## User Kit

`SLURM-user-kit/` is meant to teach researchers how to use the machine through Slurm. It includes:

- `README.md`
- `INSTALL.md`
- `USER-GUIDE.md`
- Batch examples for hello world, single GPU, multi-GPU DDP, arrays, Jupyter, and resumable training.
- Helper scripts currently present: `myjobs`, `myresources`, `job-watch`.

The user kit still assumes the Tesla DGX-1 environment, V100 GPUs, the `gpu` partition, `/storage/nas`, `/scratch`, `/tools/miniconda3`, and host `rbcdsaidgx`.

## Observed Runtime Evidence

The checked-in `site-apply.log` shows the setup reached a usable Slurm state on `rbcdsaidgx`:

- `sinfo` reported one idle node in the `gpu` partition.
- QoS entries existed for `tesla-none`, `tesla-gpu1`, `tesla-gpu2`, `tesla-gpu3`, `tesla-gpu4`, and `tesla-deadline`.
- Slurm services were restarted and the final play recap completed with `failed=0`.

The same log also shows one ignored failure in `gpu_isolation`: the task that removes regular users from the `video` group referenced `tesla_users`, but that variable was undefined.

## Local Verification During Planning

These checks were run locally while creating the planning notes:

- Python tools compiled successfully with `py_compile`.
- Bash syntax checks passed for the shipped examples and helper scripts.
- Ansible syntax checks could not be run because `ansible-playbook` is not installed in this environment.

