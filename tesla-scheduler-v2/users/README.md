# Tesla DGX-1 — User Management via CSV

Single source of truth for cluster users lives at `/etc/tesla-cluster/users.csv`.
Edit this file, run `sync_users.yml`, system reconciles.

## CSV schema

| Column | Required | Editable by admin | Notes |
|---|---|---|---|
| `username` | yes | yes | Unix login. `^[a-z_][a-z0-9_-]{0,31}$` |
| `full_name` | yes | yes | GECOS. Use `FULL_NAME_PLACEHOLDER` if unknown |
| `email` | yes | yes | Use `EMAIL_PLACEHOLDER` if unknown |
| `ssh_pubkey` | optional | yes | Single key per row. If empty, sync_users.py won't touch the user's `~/.ssh/authorized_keys` — enroll manually. Format-checked if present. |
| `tier` | yes | yes | One of: `none` / `gpu1` / `gpu2` / `gpu3` / `gpu4` / `deadline` |
| `status` | yes | yes | `active` or `inactive` |
| `expiry_date` | no | yes | ISO date `YYYY-MM-DD`. Informational only |
| `uid` | — | **no** (auto) | Filled by sync_users.py |
| `gid` | — | **no** (auto) | Filled by sync_users.py |
| `created_date` | — | **no** (auto) | Filled by sync_users.py |
| `notes` | no | yes | Free-form |

The last three columns (`uid`, `gid`, `created_date`) are **system-managed**.
Don't hand-edit them unless you know what you're doing.

## Tiers

| Tier | Max GPUs/job | Max CPUs/job | Max queued jobs | Priority |
|---|---|---|---|---|
| `none` | 0 | 4 | 1 | 0 |
| `gpu1` | 1 | 20 | 5 | 100 (default) |
| `gpu2` | 2 | 20 | 5 | 100 |
| `gpu3` | 3 | 20 | 5 | 100 |
| `gpu4` | 4 | 20 | 5 | 100 |
| `deadline` | 8 | 40 | 5 | **1000** (jumps queue) |

## Statuses

- **active** — user exists, has /home, has tier, can submit jobs
- **inactive** — `userdel -r`, /home gets tarred to `/storage/nas/_archive/home_<user>_<date>.tar.gz`; `/storage/nas/<user>` is **left intact** (admin deletes manually)

A user moved from `inactive` → `active` can be restored from archive via `restore_user.yml` or `tesla-restore-user <username>`.

## Workflow

### First time: bootstrapping from an existing system

```bash
# 1. Scan current users on the box, emit a discovered CSV
sudo python3 tools/bootstrap_users_csv.py --out /tmp/users.csv.discovered

# 2. Review and edit
sudo nano /tmp/users.csv.discovered

# 3. Sanity-check
sudo python3 tools/validate_csv.py --csv /tmp/users.csv.discovered

# 4. Install
sudo mkdir -p /etc/tesla-cluster
sudo mv /tmp/users.csv.discovered /etc/tesla-cluster/users.csv
sudo chown root:slurm_admins /etc/tesla-cluster/users.csv
sudo chmod 0640 /etc/tesla-cluster/users.csv

# 5. Dry-run sync
sudo ansible-playbook -i inventories/hosts.ini sync_users.yml --check

# 6. Apply
sudo ansible-playbook -i inventories/hosts.ini sync_users.yml
```

### Day-to-day: add / modify / remove

Just edit `/etc/tesla-cluster/users.csv`, then:

```bash
sudo python3 tools/validate_csv.py     # sanity
sudo ansible-playbook -i inventories/hosts.ini sync_users.yml --check
sudo ansible-playbook -i inventories/hosts.ini sync_users.yml
```

The script is fully idempotent — running it twice does nothing the second time.

### Common operations

| What you want | What you do |
|---|---|
| Add a new user | Append a row with `tier=gpu1` `status=active` and their pubkey, run sync |
| Promote a user to deadline | Change `tier` from `gpu2` to `deadline`, run sync |
| Demote a user | Change `tier` to lower one. Running jobs unaffected, queued GPU jobs >new limit will block until they fit (sync prints warnings) |
| Disable a user temporarily | Set `tier=none` — locks them to CPU-only, 1 job |
| Decommission a user | Set `status=inactive`, run sync — /home gets archived |
| Resurrect a deleted user | Set `status=active` again, run sync, then `sudo tesla-restore-user <name>` to pull from archive |
| Rotate someone's ssh key | Update `ssh_pubkey` in CSV, run sync (replaces `~/.ssh/authorized_keys`) |

### File locking and backups

- The CSV is locked via `flock(/var/lock/tesla-users-csv.lock)` during writes
- A timestamped copy is saved to `/storage/nas/_archive/users-csv-backups/users.csv.<date>` before each sync
- Don't hand-edit while a sync is running — the lock prevents corruption but your edits may be lost
