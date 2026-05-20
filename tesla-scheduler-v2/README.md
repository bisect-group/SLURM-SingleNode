# Tesla DGX-1 SLURM Scheduler

Ansible automation for `rbcdsaidgx` — single-node SLURM scheduler with QoS-based access tiers, CSV-driven user management, and DGX-1-aware GPU/NUMA bindings.

## Hardware target

| | |
|---|---|
| Model | NVIDIA DGX-1 (Pascal generation chassis, Volta GPUs) |
| GPUs | 8× Tesla V100-SXM2-32GB |
| Fabric | NVLink hybrid cube-mesh — 48 links @ 25.78 GB/s |
| CPUs | 2× Xeon (2 sockets × 20 cores × 2 threads = 80 logical) |
| RAM | ~500 GB |
| NUMA | GPUs 0-3 → NUMA 0 · GPUs 4-7 → NUMA 1 |
| OS | DGX OS 6.3.2 (Ubuntu 24.04 noble) |
| Driver | NVIDIA R580 LTSB (terminal Volta branch) |
| Kernel | `linux-image-6.17.0-1014-nvidia` |

## What this gives you

1. **SLURM with full accounting** — slurmctld + slurmd + slurmdbd backed by local MariaDB
2. **Tiered access via QoS** — six tiers from `none` (CPU-only) to `deadline` (full node, high priority)
3. **CSV-driven user management** — one `users.csv` is the source of truth; an idempotent Python reconciler applies it
4. **GPU isolation outside SLURM** — pam_slurm_adopt + cgroup device constraints; no GPU access without an allocation
5. **NUMA-aware GPU binding** — `gres.conf` pins each GPU to the right CPU cores
6. **/home quotas** — flat 200 GB hard limit per user (root unrestricted); orphan entries reported
7. **Shared miniconda at `/tools/miniconda3`** — auto-activated for new users via `/etc/skel`; existing users untouched
8. **Friendly tier-violation errors** — `job_submit.lua` catches over-tier requests at submit time with helpful messages
9. **Tier-aware login banner** — every interactive login shows the user their tier, limits, and active jobs
10. **Admin tooling** — `tesla-quota`, `tesla-userinfo`, `tesla-tier`, `tesla-restore-user`, `tesla-extend`, `tesla-status`

## Tier table

| Tier | Max GPUs/job | Max CPUs/job | Max queued | Priority |
|---|---|---|---|---|
| `none` | 0 | 4 | 1 | 0 (probationary) |
| `gpu1` | 1 | 20 | 5 | 100 (**default**) |
| `gpu2` | 2 | 20 | 5 | 100 |
| `gpu3` | 3 | 20 | 5 | 100 |
| `gpu4` | 4 | 20 | 5 | 100 |
| `deadline` | 8 | 40 | 5 | **1000** (jumps queue) |

The priority math: with `PriorityWeightQOS=10000`, a 900-point QoS gap gives deadline jobs a 9-million-point head start. Within a single QoS, `PriorityWeightAge=1000` provides FCFS-by-submit-time tie-breaking. No preemption — running jobs are never killed by a higher-priority queued job.

## Storage

| Path | Quota | Backup | Notes |
|---|---|---|---|
| `/home/<user>` | 200 GB hard | tarred on inactive | Local SSD `/dev/sdb1`; archived to `/storage/nas/_archive/` |
| `/storage/nas/<user>` | unlimited | none | Big-data scratch; survives user inactivation |
| `/scratch/` | none | none | Per-job tmpfs, auto-cleaned by SLURM |
| `/tools/miniconda3` | n/a | n/a | Shared, read-only to users |

When a user is marked `inactive`, the playbook tars their `/home` and runs `userdel -r`. Their `/storage/nas/$user` directory is **never auto-deleted** — admin manually purges later (handles the "former user comes back" case).

## Setup workflow

### 1. Bootstrap the cluster

```bash
ansible-playbook -i inventories/hosts.ini site.yml -K
```

This installs everything: SLURM, slurmdbd, MariaDB, miniconda, quotas, tier groups, and all admin tools. Idempotent — safe to re-run.

### 2. Verify health

```bash
ansible-playbook -i inventories/hosts.ini verify.yml
```

Runs 15 read-only checks: services, GRES detection, QoS presence, NVLink count, cgroups, miniconda, quota state.

### 3. Onboard existing users from the live system

```bash
sudo python3 tools/bootstrap_users_csv.py --out /tmp/users.csv.discovered
sudo nano /tmp/users.csv.discovered         # fill in real names/emails, set tiers
sudo python3 tools/validate_csv.py --csv /tmp/users.csv.discovered
sudo mv /tmp/users.csv.discovered /etc/tesla-cluster/users.csv
sudo chown root:slurm_admins /etc/tesla-cluster/users.csv
sudo chmod 0640 /etc/tesla-cluster/users.csv

ansible-playbook -i inventories/hosts.ini sync_users.yml --check   # dry run
ansible-playbook -i inventories/hosts.ini sync_users.yml           # apply
```

### 4. Day-to-day: edit CSV, run sync

```bash
sudo nano /etc/tesla-cluster/users.csv            # add row, change tier, mark inactive
ansible-playbook -i inventories/hosts.ini sync_users.yml --check
ansible-playbook -i inventories/hosts.ini sync_users.yml
```

Single-user sync:

```bash
ansible-playbook -i inventories/hosts.ini sync_users.yml -e "single_user=alice"
```

## Files & layout

```
tesla-scheduler/
├── site.yml                       Master playbook — full cluster setup
├── verify.yml                     15-check health verification
├── sync_users.yml                 CSV → system reconciler
├── restore_user.yml               Restore /home from archive
├── update_config.yml              Re-deploy SLURM configs without reinstall
├── add_user.yml                   Alias for sync_users.yml --user (legacy)
├── group_vars/all.yml             All tunable variables
├── inventories/hosts.ini          Ansible inventory (local)
├── users/
│   ├── users.csv.example          Schema reference
│   └── README.md                  CSV workflow docs
├── tools/
│   ├── bootstrap_users_csv.py     Scan live users → CSV
│   ├── sync_users.py              The reconciler (Python, ~400 lines)
│   └── validate_csv.py            CSV linter
└── roles/
    ├── slurm_base/                Packages, munge, MariaDB, slurm user
    ├── storage_quotas/            200GB /home enforcement
    ├── shared_miniconda/          /tools/miniconda3 + /etc/skel
    ├── slurmdbd_setup/            DB + slurmdbd + sacctmgr accounts/QoS
    ├── slurm_config/              slurm.conf, gres.conf, cgroup.conf, job_submit.lua
    ├── gpu_isolation/             pam_slurm_adopt + udev + NVLink check
    ├── user_policy/               tier groups, limits.conf, tier login banner
    └── admin_tools/               MOTD, helper scripts, logrotate, watchdog
```

## Admin commands

| Command | What it does |
|---|---|
| `tesla-status` | Cluster snapshot — queue, GPUs, per-user counts |
| `tesla-status --all` | Above + GPU utilization, NVLink health, QoS table |
| `tesla-userinfo <user>` | Full per-user info: tier, jobs, quota, NAS usage, archives, last login |
| `tesla-quota` | Show quota usage for all users |
| `tesla-quota --over 150` | Flag users over 150 GB |
| `tesla-quota --normalize <user>` | Reset a user to the default 200 GB limit |
| `tesla-quota --orphans` | List quota entries with no `/etc/passwd` row |
| `tesla-tier [user]` | Show a user's tier and limits |
| `tesla-extend <jobid> <hours>` | Extend a job's walltime past 48h (admin only) |
| `tesla-restore-user <user>` | Restore an inactive user's `/home` from archive |

User commands (all `tesla_users` members):

| Command | What it does |
|---|---|
| `tq` | Your queued jobs (alias) |
| `tqa` | Everyone's queued jobs (alias) |
| `tier` | Your tier and limits (alias for `tesla-tier`) |
| `tesla-status` | Cluster snapshot |

## When something goes wrong

| Symptom | First thing to check |
|---|---|
| `sinfo` says nodes `down` | `systemctl status slurmd` and `tail /var/log/slurm/slurmd.log` |
| Jobs stuck in PD with reason `AssociationJobLimit` | User's tier QoS is full — `sacctmgr show qos format=name,maxjobspu` |
| User can ssh but no GPU access | Expected — they need `sbatch` or `srun --pty` to get a GPU |
| `sacctmgr` says "Couldn't load specified plugin" | slurmdbd is down: `systemctl status slurmdbd && tail /var/log/slurm/slurmdbd.log` |
| New user can't submit | Did you run `sync_users.yml` after adding them to CSV? `tesla-userinfo <name>` will say if slurmdbd doesn't know them |
| User over quota, can't write | `tesla-quota --user X` — if hard limit hit, free space on `/home` or migrate to `/storage/nas/X` |
| MariaDB password lost | It's stored at `/etc/slurm/slurmdbd-mysql.password` (mode 0400, owner slurm) |

## DGX-1 NVLink reference

GPUs 0-3 sit on NUMA 0; GPUs 4-7 on NUMA 1. NVLink connectivity per pair (from `nvidia-smi topo -m`):

```
       0  1  2  3  4  5  6  7
   0   X  NV1 NV1 NV2 NV2 X   X   X
   1   NV1 X  NV2 NV1 X   NV2 X   X
   2   NV1 NV2 X  NV2 X   X   NV1 X
   3   NV2 NV1 NV2 X  X   X   X   NV1
   4   NV2 X   X   X   X  NV1 NV1 NV2
   5   X   NV2 X   X   NV1 X   NV2 NV1
   6   X   X   NV1 X   NV1 NV2 X   NV2
   7   X   X   X   NV1 NV2 NV1 NV2 X
```

For multi-GPU jobs, keeping all 4 GPUs within one NUMA node (0-3 OR 4-7) gives pure NVLink communication. Crossing the boundary forces QPI traffic. The `--gres-flags=enforce-binding` option in `sbatch` enforces this.

## Authorship

Adapted from the Feynman 4×A100 scheduler. Differences:

- 8 GPUs vs 4, with V100/NVLink hybrid cube-mesh fabric
- slurmdbd + MariaDB for real accounting (Feynman was conf-file only)
- QoS-based tiers replacing flat per-user limits
- CSV-driven user management with `sync_users.py` reconciler
- /home quota enforcement (200 GB flat) with archive-on-inactive
- Shared miniconda for new users only (existing users keep their setups)
- Tier-aware MOTD and `job_submit.lua` for UX
