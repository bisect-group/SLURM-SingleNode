# GPU Safety And Recovery Ops Notes

This note covers the current fixture-scoped GPU safety controls on the Quadro
test host and the intended operator workflow for GPU recovery.

## Submit Filtering

SSN renders both controller-side and client-side submit filters:

- `JobSubmitPlugins=lua` with `/etc/slurm/job_submit.lua`
- `CliFilterPlugins=cli_filter/lua`
- `CliFilterParameters=cli_filter_lua_path=/etc/slurm/cli_filter.lua`

The client filter rejects `--no-requeue` for normal users, including script
directives. The existing `/usr/local/bin/sbatch` wrapper remains as a friendly
fallback and also rejects `--requeue=0`, `--requeue=no`, and
`--requeue=false`.

Slurm documents `cli_filter` as bypassable by alternate client configuration,
so it is a policy/UX gate, not a security boundary. Keep QOS/TRES/cgroup
settings as the authoritative enforcement layer.

Live Quadro testing found that absolute `/usr/bin/sbatch --requeue=0` is
normalized by Slurm 25.11.2 to a positive `requeue` option before Lua sees it,
so that exact absolute-binary spelling is a documented client-filter
limitation.

## Verification

Use:

```bash
sudo ssn-verify --profile gpu-bisect-quadro-p620
sudo ssn-gpu-recovery status --profile gpu-bisect-quadro-p620
```

The GPU report checks `nvidia-smi`, `/dev/nvidiaN`, rendered and installed
`gres.conf`, Slurm node GRES, GPU status snapshot freshness, Slurm GPU job
mapping, and fail-closed MIG/MPS/shared-GPU indicators.

## CPU-Only Recovery

Current recovery is fixture-scoped. It may hold/cancel only `ssn-test-*` GPU
jobs and refuses non-fixture GPU jobs.

Plan:

```bash
sudo ssn-gpu-recovery plan \
  --profile gpu-bisect-quadro-p620 \
  --recovery-profile cpu-bisect-node0 \
  --plan /var/lib/slurm-single-node/plans/gpu-recovery-live/gpu-recovery-plan.json
```

Create a reviewed token:

```bash
sudo ssn-plan-token create \
  --plan /var/lib/slurm-single-node/plans/gpu-recovery-live/gpu-recovery-plan.json \
  --risk cpu_only_recovery \
  --reason "GPU recovery"
```

Enter CPU-only mode:

```bash
sudo ssn-gpu-recovery enter \
  --profile gpu-bisect-quadro-p620 \
  --recovery-profile cpu-bisect-node0 \
  --plan /var/lib/slurm-single-node/plans/gpu-recovery-live/gpu-recovery-plan.json \
  --plan-token <token>
```

Exit recovery:

```bash
sudo ssn-gpu-recovery exit \
  --profile gpu-bisect-quadro-p620 \
  --recovery-profile cpu-bisect-node0
```

If exit fails after apply starts, leave the node drained and inspect
`/var/lib/slurm-single-node/gpu-recovery-state.json` before resuming.
