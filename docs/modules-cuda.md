# Modules And CUDA Ops Note

SSN manages the shared module surface in validate-only mode by default. It
creates the module tree and renders modulefiles only for software that already
exists on the host.

## Commands

```bash
sudo ssn-modules status --profile gpu-bisect-quadro-p620
sudo ssn-modules verify --profile gpu-bisect-quadro-p620
```

`status` reports Lmod, detected CUDA toolkit roots, detected Miniconda, and the
modulefiles SSN will render. `verify` runs smoke checks for the detected
modulefiles.

## CUDA

CUDA toolkit installation is validate-only in v1. Admins install the toolkit
outside SSN, usually under `/usr/local/cuda` or `/usr/local/cuda-<version>`.

SSN behavior:

- no toolkit found: report `not_detected` and do not render a CUDA module;
- one toolkit found: render `cuda` and `cuda/<version>` when the version can be
  detected;
- `/usr/local/cuda` present: use it as the reviewed default;
- multiple versioned toolkits without `/usr/local/cuda`: render versioned
  modules only and report that the default needs review.

CUDA smoke checks include module load/unload, `nvidia-smi`, `nvcc --version`
when `nvcc` exists, library path sanity when `lib64` exists, and an optional
sample compile/run only when sample source and `nvcc` are available.

## Miniconda

If `/tools/miniconda3/bin/conda` exists, SSN renders a `miniconda3` module and
verifies `conda --version`. If it is absent, the check is skipped.

## Module Path

SSN installs `/etc/profile.d/ssn-modules.sh`, which initializes Lmod when
available and adds `/tools/modules/Core` to the module search path.
