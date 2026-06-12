# Production Safety Bridge Ops Note

This note covers the fixture-tested bridge features added before broad
production rollout: inactive archive hooks, managed-user isolation allowlists,
and tokenized retention cleanup.

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

Production retention is still report-only. `ssn-retention-cleanup` can delete
only explicit SSN test artifacts after a reviewed `retention_delete` token.
Candidates whose names do not start with `ssn-test-` or `tmp-ssn-test-` are
skipped, even with a token. Apply also skips symlinks and candidates outside
the reviewed report root.

```bash
sudo ssn-retention-cleanup --profile gpu-bisect-quadro-p620 \
  --root /tmp/ssn-retention-bridge-root \
  --older-than-days 1 \
  --report /var/lib/slurm-single-node/plans/ssn-test-retention/report.json

sudo ssn-plan-token create \
  --plan /var/lib/slurm-single-node/plans/ssn-test-retention/report.json \
  --risk retention_delete \
  --reason "reviewed SSN test retention cleanup"

sudo ssn-retention-cleanup --apply --yes-delete \
  --root /tmp/ssn-retention-bridge-root \
  --report /var/lib/slurm-single-node/plans/ssn-test-retention/report.json \
  --plan-token <token>
```

## No-Requeue Caveat

The managed wrapper and Slurm `cli_filter/lua` reject `--no-requeue`.
The wrapper also rejects `--requeue=0/no/false`. On the Quadro Slurm 25.11.2
stack, absolute `/usr/bin/sbatch --requeue=0` is normalized to a positive
`requeue` value before Lua can see the original spelling, so that exact
absolute-binary spelling is documented as a client-filter limitation.
