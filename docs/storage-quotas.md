# Storage Quotas Ops Note

SSN quota enablement is an admin-only workflow. It is intended for reviewed
storage mounts, not ad hoc cleanup.

## Plan And Enable

Create a plan for the selected profile mounts:

```bash
sudo ssn-storage-quotas plan --profile gpu-bisect-quadro-p620 \
  --mount home --mount data --mount scratch
```

The plan resolves each path to its real mountpoint with `findmnt -T`. On the
Quadro test host, `/home` resolves to `/`, so enabling home quotas edits and
remounts the root filesystem.

Create a reviewed token from the printed plan path:

```bash
sudo ssn-plan-token create \
  --plan /var/lib/slurm-single-node/plans/<plan>/storage-quota-plan.json \
  --risk storage_quota_enable \
  --reason "reviewed quota enablement"
```

Apply the plan:

```bash
sudo ssn-storage-quotas enable \
  --plan /var/lib/slurm-single-node/plans/<plan>/storage-quota-plan.json \
  --plan-token <token>
```

Enablement backs up `/etc/fstab`, adds `usrquota,grpquota` to selected ext4
mounts, remounts them, runs `quotacheck`, then runs `quotaon`.

## Status

```bash
sudo ssn-storage-quotas status --profile gpu-bisect-quadro-p620 \
  --mount home --mount data --mount scratch

quotaon -p / /data /scratch
repquota -u / /data /scratch
```

## Fixture Quotas

Fixture tests can use tiny quotas without changing policy defaults:

```bash
sudo ssn-sync-users --profile gpu-bisect-quadro-p620 \
  --apply-fixture-quotas \
  --fixture-quota home=64MB \
  --fixture-quota data=64MB \
  --fixture-quota scratch=128MB
```

This path only applies to users matching the configured fixture prefix,
defaulting to `ssn-test-*`.

## Recovery

If quota enablement fails before quotas are active, SSN attempts to restore the
fstab backup and remount the selected mountpoints. If partial activation
occurs, SSN leaves the system as-is and writes recovery commands into the
enable report.
