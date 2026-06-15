# Production Safety Bridge Ops Note

This note covers the fixture-tested bridge features added before broad
production rollout: inactive archive hooks, Slurm-backed archive execution,
managed-user isolation allowlists, and tokenized retention cleanup.

## Inactive Archive Hooks

Archive hooks live under `/etc/slurm-single-node/archive-hooks.d`. The
directory is root-owned and group-readable by `slurm_admins`; only executable
regular files are considered. Hooks run after the local `.7z` archive exists.

Hook environment:

- `SSN_ARCHIVE_USER`
- `SSN_ARCHIVE_UID`
- `SSN_ARCHIVE_GID`
- `SSN_ARCHIVE_PATH`
- `SSN_ARCHIVE_STATE`
- `SSN_ARCHIVE_OPERATION_HASH`
- `SSN_ARCHIVE_PROFILE`
- `SSN_ARCHIVE_HOOK_DIR`

For backup-required profiles, inactive apply with risk
`inactive_archive_apply` fails before mutation when no executable hook exists.
If a hook fails, SSN records `backup_failed`, keeps the local archive, keeps the
account locked/present, and blocks removal. Retry by generating a new dry plan
and reviewed token after fixing the hook. A reviewed
`inactive_local_only_archive` token remains the explicit local-only override
for disposable/test workflows.

Status:

```bash
sudo ssn-archive-status
```

## Non-Fixture Lifecycle Allowlist

Destructive inactive apply for users outside the `ssn-test-*` fixture prefix
requires an exact reviewed user allowlist:

```bash
sudo ssn-sync-users --profile gpu-bisect-quadro-p620 \
  --user ssn-lifecycle-hooksuccess \
  --dry-run \
  --plan-output /var/lib/slurm-single-node/plans/<plan>/inactive-plan.json

sudo ssn-plan-token create \
  --plan /var/lib/slurm-single-node/plans/<plan>/inactive-plan.json \
  --risk inactive_archive_apply \
  --reason "reviewed fake lifecycle hook-success archive"

sudo ssn-sync-users --profile gpu-bisect-quadro-p620 \
  --user ssn-lifecycle-hooksuccess \
  --apply \
  --plan-output /var/lib/slurm-single-node/plans/<plan>/inactive-plan.json \
  --plan-token <token> \
  --allow-lifecycle-user ssn-lifecycle-hooksuccess
```

For local-only test archives, use the same flow with risk
`inactive_local_only_archive`. SSN refuses protected users such as `root`,
configured admins, `adhil`, and `roshan`, even if they are allowlisted. It also
refuses unmanaged users and users not present in the reviewed inactive plan.

Archive work is submitted through the protected Slurm archive account/QoS from
the storage policy. On the Quadro test profile this is `slurm-admin` with
`archive-admin`. The internal runner is `/usr/local/sbin/ssn-archive-runner`;
operators should call `ssn-sync-users`, not the runner, during normal use.

`ssn-archive-status` reports the archive job id/state, service account, QoS,
runner payload path, runner result path, hook result, archive path, and next
action. When submitting manual fixture evidence jobs from a root shell, prefer
safe output paths such as `--chdir=/tmp --output=/tmp/<name>-%j.out` so test
jobs do not fail writing into `/root`.

## Login/GPU Isolation Rollout

`ssn-login-isolation` supports three target scopes:

- `fixture_only`: active managed users matching `ssn-test-*`.
- `managed_allowlist`: active managed users named with `--allow-user` or
  matching `--allow-prefix`.
- `all_managed_non_admin`: all active managed non-admin users.

Use allowlist mode before broad rollout:

```bash
sudo ssn-login-isolation --profile gpu-bisect-quadro-p620 \
  --target-scope managed_allowlist \
  --allow-user ssn-test-standard \
  --mode cgroup
```

Apply or reset:

```bash
sudo ssn-login-isolation --profile gpu-bisect-quadro-p620 \
  --target-scope managed_allowlist \
  --allow-user ssn-test-standard \
  --mode cgroup --apply

sudo ssn-login-isolation --profile gpu-bisect-quadro-p620 \
  --target-scope managed_allowlist \
  --allow-user ssn-test-standard \
  --mode disabled --apply
```

## Retention Cleanup

`ssn-retention-cleanup` can now trial production-shaped deletion under
SSN-owned retention roots after a reviewed `retention_delete` token. The
allowed production roots are:

- `/var/lib/slurm-single-node/plans`
- `/var/backups/slurm-single-node/users`
- `/var/backups/slurm-single-node/fstab`

Apply requires `--allow-production-roots`, `--yes-delete`, a matching
operation hash, and a single-use token. It only considers direct children of
the reviewed root, skips symlinks, skips path-traversal candidates, and applies
root-specific name allowlists such as `install-*`, `apply-*`,
`storage-quotas-*`, `users.yml.*`, and `users-state.yml.*`. Test-artifact names
starting with `ssn-test-` or `tmp-ssn-test-` remain allowed for disposable live
tests.

```bash
sudo ssn-retention-cleanup --profile gpu-bisect-quadro-p620 \
  --root /var/lib/slurm-single-node/plans \
  --older-than-days 1 \
  --report /var/lib/slurm-single-node/plans/ssn-test-retention/report.json

sudo ssn-plan-token create \
  --plan /var/lib/slurm-single-node/plans/ssn-test-retention/report.json \
  --risk retention_delete \
  --reason "reviewed SSN test retention cleanup"

sudo ssn-retention-cleanup --apply --yes-delete \
  --allow-production-roots \
  --root /var/lib/slurm-single-node/plans \
  --report /var/lib/slurm-single-node/plans/ssn-test-retention/report.json \
  --plan-token <token>
```

## Scratch Cleanup

Production-shaped scratch cleanup is also tokenized and exact-user allowlisted.
Reports can scan aged children under `/scratch/$USER/cache` and
`/scratch/$USER/tmp`:

```bash
sudo ssn-scratch-cleanup --profile gpu-bisect-quadro-p620 \
  --allow-cleanup-user ssn-test-storage-cleanup \
  --report /var/lib/slurm-single-node/plans/ssn-test-scratch-cleanup/report.json
```

Apply requires `--allow-cleanup-user USER`, `--yes-delete`, and a reviewed
`scratch_cleanup` token. It skips `/scratch/jobs`, symlinks, cache/tmp roots
themselves, non-allowlisted users, and users with active Slurm jobs.

## No-Requeue Caveat

The managed wrapper and Slurm `cli_filter/lua` reject `--no-requeue`.
The wrapper also rejects `--requeue=0/no/false`. On the Quadro Slurm 25.11.2
stack, absolute `/usr/bin/sbatch --requeue=0` is normalized to a positive
`requeue` value before Lua can see the original spelling, so that exact
absolute-binary spelling is documented as a client-filter limitation.
