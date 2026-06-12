# Login And GPU Isolation Ops Notes

SSN keeps the all-in-one node usable for SSH, editing, queue checks, and file
movement while pushing compute through Slurm.

## Commands

- `ssn-login-isolation --profile <profile>` plans fixture-scoped login limits.
- `ssn-login-isolation --profile <profile> --apply --mode cgroup` writes
  per-user `user-<uid>.slice` limits and cgroup GPU-device denial for active
  managed `ssn-test-*` users.
- `ssn-login-isolation --profile <profile> --apply --mode acl` enables the
  fallback device-permission plus Slurm prolog/epilog ACL mode.
- `ssn-login-isolation --profile <profile> --apply --mode disabled` leaves the
  generated files in place but resets SSN resource/device controls.
- `ssn-login-status --profile <profile>` reports targeted user slices and GPU
  snapshot freshness.
- `ssn-gpu-status` reads `/run/slurm-single-node/gpu-status.json`.

## Recovery

If login isolation breaks fixture SSH or Slurm GPU jobs, disable it:

```bash
sudo ssn-login-isolation --profile gpu-bisect-quadro-p620 --apply --mode disabled
sudo loginctl terminate-user ssn-test-standard
```

If the ACL fallback was enabled and you need to inspect device permissions:

```bash
ls -l /dev/nvidia* /dev/nvidia-caps/* 2>/dev/null
getfacl /dev/nvidia0 /dev/nvidiactl 2>/dev/null
```

The current live-test scope is intentionally limited to `ssn-test-*` fixture
users. Production-wide login and GPU isolation should be enabled only after the
fixture path passes SSH, Slurm CPU, Slurm GPU, and direct-GPU-denial tests.
