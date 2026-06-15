from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .ops import command_stdout


DEFAULT_CUDA_PREFIX = Path("/usr/local")
DEFAULT_LMOD_INIT = (
    Path("/usr/share/lmod/lmod/init/bash"),
    Path("/usr/share/modules/init/bash"),
)


def modules_status_report(
    resolved: dict[str, Any],
    *,
    cuda_prefix: str | Path = DEFAULT_CUDA_PREFIX,
) -> dict[str, Any]:
    policy = resolved.get("resolved_policies", {}).get("modules") or {}
    roots = policy.get("roots") or {}
    modules_root = Path(str(roots.get("modules") or "/tools/modules"))
    core_root = modules_root / "Core"
    report: dict[str, Any] = {
        "schema_version": 1,
        "command": "modules-status",
        "profile": resolved.get("profile"),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "policy": {
            "lmod": bool(policy.get("lmod")),
            "cuda_toolkit_mode": (policy.get("cuda") or {}).get("toolkit_mode"),
            "shared_env_base": (policy.get("shared_env_base") or {}).get("type"),
        },
        "roots": {key: str(value) for key, value in roots.items()},
        "module_roots": {
            "modules": str(modules_root),
            "core": str(core_root),
        },
        "lmod": _lmod_status(),
        "cuda": _cuda_status(resolved, policy, core_root=core_root, cuda_prefix=Path(cuda_prefix)),
        "miniconda": _miniconda_status(policy, core_root=core_root),
        "modulefiles": [],
    }
    report["modulefiles"] = [
        *_cuda_modulefiles(report["cuda"], core_root),
        *_miniconda_modulefiles(report["miniconda"], core_root),
    ]
    return report


def modules_verify_report(
    resolved: dict[str, Any],
    *,
    cuda_prefix: str | Path = DEFAULT_CUDA_PREFIX,
) -> dict[str, Any]:
    status = modules_status_report(resolved, cuda_prefix=cuda_prefix)
    checks: list[dict[str, str]] = []
    modules_enabled = bool(status.get("policy", {}).get("lmod"))
    lmod = status.get("lmod") or {}
    core_root = status.get("module_roots", {}).get("core") or "/tools/modules/Core"
    if modules_enabled:
        checks.append(_check("lmod_init", bool(lmod.get("init_bash")), lmod.get("init_bash") or "missing"))
        module_avail = _module_shell(core_root, "module avail >/dev/null")
        checks.append(_check("module_avail", module_avail.returncode == 0, _detail(module_avail)))
    else:
        checks.append(_skip("lmod_disabled", "modules policy disabled Lmod"))

    miniconda = status.get("miniconda") or {}
    if miniconda.get("status") == "detected":
        proc = _module_shell(core_root, "module load miniconda3 && conda --version")
        checks.append(_check("miniconda_module", proc.returncode == 0, _detail(proc)))
    else:
        checks.append(_skip("miniconda_module", miniconda.get("reason") or "not detected"))

    cuda = status.get("cuda") or {}
    if cuda.get("status") == "not_detected":
        checks.append(_skip("cuda_module", cuda.get("reason") or "not detected"))
    elif cuda.get("status") == "needs_default_review":
        for toolkit in cuda.get("toolkits") or []:
            module_name = toolkit.get("module_name")
            if module_name:
                checks.extend(_verify_cuda_module(resolved, core_root, module_name, toolkit))
        checks.append(_skip("cuda_default_module", "multiple CUDA toolkits detected without reviewed /usr/local/cuda default"))
    else:
        module_name = cuda.get("default_module") or "cuda"
        default_toolkit = _default_cuda_toolkit(cuda)
        checks.extend(_verify_cuda_module(resolved, core_root, module_name, default_toolkit))

    healthy = not any(check["status"] == "FAIL" for check in checks)
    return {
        "schema_version": 1,
        "command": "modules-verify",
        "profile": resolved.get("profile"),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "healthy": healthy,
        "status": status,
        "checks": checks,
    }


def modules_verify_errors(report: dict[str, Any]) -> list[str]:
    return [f"{check['name']}: {check['detail']}" for check in report.get("checks", []) if check.get("status") == "FAIL"]


def _lmod_status() -> dict[str, Any]:
    init_bash = next((str(path) for path in DEFAULT_LMOD_INIT if path.exists()), None)
    return {
        "modulecmd": shutil.which("modulecmd"),
        "lmod": shutil.which("lmod"),
        "init_bash": init_bash,
    }


def _cuda_status(
    resolved: dict[str, Any],
    policy: dict[str, Any],
    *,
    core_root: Path,
    cuda_prefix: Path,
) -> dict[str, Any]:
    cuda_policy = policy.get("cuda") or {}
    toolkits = _discover_cuda_toolkits(cuda_prefix)
    if not resolved.get("derived", {}).get("has_gpus"):
        return {
            "toolkit_mode": cuda_policy.get("toolkit_mode"),
            "status": "skipped_cpu_profile",
            "reason": "CPU-only profile",
            "toolkits": toolkits,
            "module_dir": str(core_root / "cuda"),
        }
    if not toolkits:
        return {
            "toolkit_mode": cuda_policy.get("toolkit_mode"),
            "status": "not_detected",
            "reason": f"no CUDA toolkit roots found under {cuda_prefix}",
            "toolkits": [],
            "module_dir": str(core_root / "cuda"),
        }
    default = _select_default_cuda_toolkit(toolkits, cuda_prefix)
    if default is None:
        return {
            "toolkit_mode": cuda_policy.get("toolkit_mode"),
            "status": "needs_default_review",
            "reason": "multiple CUDA toolkits detected and /usr/local/cuda is absent or invalid",
            "toolkits": toolkits,
            "module_dir": str(core_root / "cuda"),
            "default_module": None,
        }
    return {
        "toolkit_mode": cuda_policy.get("toolkit_mode"),
        "status": "detected",
        "reason": "CUDA toolkit detected",
        "toolkits": toolkits,
        "default": default,
        "default_module": str((cuda_policy.get("modules") or {}).get("default") or "cuda"),
        "module_dir": str(core_root / "cuda"),
    }


def _discover_cuda_toolkits(cuda_prefix: Path) -> list[dict[str, Any]]:
    candidates: list[Path] = []
    default_root = cuda_prefix / "cuda"
    if default_root.exists():
        candidates.append(default_root)
    candidates.extend(sorted(path for path in cuda_prefix.glob("cuda-*") if path.exists()))
    seen: set[str] = set()
    toolkits: list[dict[str, Any]] = []
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        if not path.is_dir():
            continue
        version = _cuda_version(path)
        toolkit = {
            "root": str(path),
            "realpath": str(path.resolve()),
            "name": path.name,
            "version": version,
            "is_default_path": path == default_root,
            "has_nvcc": (path / "bin" / "nvcc").exists(),
            "has_lib64": (path / "lib64").exists(),
            "has_include": (path / "include").exists(),
        }
        if version:
            toolkit["module_name"] = f"cuda/{version}"
        toolkits.append(toolkit)
    return toolkits


def _cuda_version(root: Path) -> str | None:
    version_json = root / "version.json"
    if version_json.exists():
        try:
            data = json.loads(version_json.read_text())
            value = data.get("cuda", {}).get("version") or data.get("cuda_version")
            if value:
                return _short_version(str(value))
        except Exception:
            pass
    version_txt = root / "version.txt"
    if version_txt.exists():
        match = re.search(r"(\d+\.\d+(?:\.\d+)?)", version_txt.read_text(errors="ignore"))
        if match:
            return _short_version(match.group(1))
    name_match = re.search(r"cuda[-_]?(\d+\.\d+)", root.name)
    if name_match:
        return _short_version(name_match.group(1))
    nvcc = root / "bin" / "nvcc"
    if nvcc.exists():
        out = command_stdout([str(nvcc), "--version"]) or ""
        match = re.search(r"release\s+(\d+\.\d+)", out)
        if match:
            return _short_version(match.group(1))
    return None


def _short_version(version: str) -> str:
    parts = version.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else version


def _select_default_cuda_toolkit(toolkits: list[dict[str, Any]], cuda_prefix: Path) -> dict[str, Any] | None:
    default_real = (cuda_prefix / "cuda").resolve()
    for toolkit in toolkits:
        if toolkit.get("is_default_path") or toolkit.get("realpath") == str(default_real):
            return toolkit
    if len(toolkits) == 1:
        return toolkits[0]
    return None


def _default_cuda_toolkit(cuda: dict[str, Any]) -> dict[str, Any]:
    default = cuda.get("default")
    if isinstance(default, dict):
        return default
    toolkits = cuda.get("toolkits") or []
    return toolkits[0] if toolkits else {}


def _miniconda_status(policy: dict[str, Any], *, core_root: Path) -> dict[str, Any]:
    shared = policy.get("shared_env_base") or {}
    root = Path(str(shared.get("root") or "/tools/miniconda3"))
    conda = root / "bin" / "conda"
    if shared.get("type") != "miniconda":
        return {
            "status": "skipped",
            "reason": f"shared_env_base type is {shared.get('type')}",
            "root": str(root),
            "module_path": str(core_root / "miniconda3.lua"),
        }
    if not conda.exists():
        return {
            "status": "not_detected",
            "reason": f"{conda} is absent",
            "root": str(root),
            "conda": str(conda),
            "module_path": str(core_root / "miniconda3.lua"),
        }
    return {
        "status": "detected",
        "reason": "Miniconda detected",
        "root": str(root),
        "conda": str(conda),
        "version": command_stdout([str(conda), "--version"]),
        "module_path": str(core_root / "miniconda3.lua"),
    }


def _cuda_modulefiles(cuda: dict[str, Any], core_root: Path) -> list[dict[str, str]]:
    modulefiles: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for toolkit in cuda.get("toolkits") or []:
        version = toolkit.get("version")
        if not version:
            continue
        name = f"cuda/{version}"
        path = core_root / "cuda" / f"{version}.lua"
        key = (name, str(path))
        if key in seen:
            continue
        seen.add(key)
        modulefiles.append(
            {
                "name": name,
                "path": str(path),
                "content": _cuda_module_content(name, str(toolkit["root"]), toolkit.get("version")),
            }
        )
    default = cuda.get("default")
    default_name = cuda.get("default_module")
    if isinstance(default, dict) and default_name:
        modulefiles.append(
            {
                "name": str(default_name),
                "path": str(core_root / f"{default_name}.lua"),
                "content": _cuda_module_content(str(default_name), str(default["root"]), default.get("version")),
            }
        )
    return modulefiles


def _miniconda_modulefiles(miniconda: dict[str, Any], core_root: Path) -> list[dict[str, str]]:
    if miniconda.get("status") != "detected":
        return []
    return [
        {
            "name": "miniconda3",
            "path": str(core_root / "miniconda3.lua"),
            "content": _miniconda_module_content(str(miniconda["root"])),
        }
    ]


def _cuda_module_content(name: str, root: str, version: str | None) -> str:
    version_text = version or "unknown"
    root_lua = _lua_string(root)
    return "\n".join(
        [
            f"help([[SSN validate-only CUDA module {name}.]])",
            f'whatis("Name: {name}")',
            f'whatis("Version: {version_text}")',
            f"local root = {root_lua}",
            'setenv("CUDA_HOME", root)',
            'setenv("CUDA_ROOT", root)',
            'prepend_path("PATH", pathJoin(root, "bin"))',
            'prepend_path("LD_LIBRARY_PATH", pathJoin(root, "lib64"))',
            'prepend_path("LIBRARY_PATH", pathJoin(root, "lib64"))',
            'prepend_path("CPATH", pathJoin(root, "include"))',
            "",
        ]
    )


def _miniconda_module_content(root: str) -> str:
    root_lua = _lua_string(root)
    return "\n".join(
        [
            "help([[SSN validate-only Miniconda module.]])",
            'whatis("Name: miniconda3")',
            f"local root = {root_lua}",
            'setenv("CONDA_ROOT", root)',
            'prepend_path("PATH", pathJoin(root, "bin"))',
            "",
        ]
    )


def _lua_string(value: str) -> str:
    return json.dumps(value)


def _verify_cuda_module(
    resolved: dict[str, Any],
    core_root: str,
    module_name: str,
    toolkit: dict[str, Any],
) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    proc = _module_shell(core_root, f"module load {module_name} && module unload {module_name}")
    checks.append(_check(f"{module_name}_load_unload", proc.returncode == 0, _detail(proc)))
    if resolved.get("derived", {}).get("has_gpus"):
        proc = _module_shell(core_root, f"module load {module_name} && nvidia-smi -L >/dev/null")
        checks.append(_check(f"{module_name}_nvidia_smi", proc.returncode == 0, _detail(proc)))
    if toolkit.get("has_nvcc"):
        proc = _module_shell(core_root, f"module load {module_name} && nvcc --version")
        checks.append(_check(f"{module_name}_nvcc_version", proc.returncode == 0, _detail(proc)))
    else:
        checks.append(_skip(f"{module_name}_nvcc_version", "nvcc is not present in detected toolkit"))
    if toolkit.get("has_lib64"):
        proc = _module_shell(
            core_root,
            (
                f"module load {module_name} && "
                'test -n "${CUDA_HOME:-}" && '
                'case ":${LD_LIBRARY_PATH:-}:" in *":${CUDA_HOME}/lib64:"*) exit 0 ;; *) exit 3 ;; esac'
            ),
        )
        checks.append(_check(f"{module_name}_library_path", proc.returncode == 0, _detail(proc)))
    else:
        checks.append(_skip(f"{module_name}_library_path", "lib64 is not present in detected toolkit"))
    sample = Path(str(toolkit.get("root") or "")) / "samples" / "1_Utilities" / "deviceQuery" / "deviceQuery.cpp"
    if sample.exists() and toolkit.get("has_nvcc"):
        with tempfile.TemporaryDirectory(prefix="ssn-cuda-sample-") as tmp:
            proc = _module_shell(
                core_root,
                f"module load {module_name} && nvcc -o {tmp}/deviceQuery {_sh_quote(str(sample))} && {tmp}/deviceQuery >/dev/null",
            )
            checks.append(_check(f"{module_name}_sample_compile_run", proc.returncode == 0, _detail(proc)))
    else:
        checks.append(_skip(f"{module_name}_sample_compile_run", "CUDA sample source or nvcc is not present"))
    return checks


def _module_shell(core_root: str, command: str) -> subprocess.CompletedProcess[str]:
    init = next((path for path in DEFAULT_LMOD_INIT if path.exists()), None)
    if init is None:
        return subprocess.CompletedProcess(["bash", "-lc", command], 127, "", "Lmod bash init not found")
    script = f"source {_sh_quote(str(init))}; module use {_sh_quote(core_root)}; {command}"
    return subprocess.run(["bash", "-lc", script], text=True, capture_output=True)


def _check(name: str, ok: bool, detail: str) -> dict[str, str]:
    return {"name": name, "status": "PASS" if ok else "FAIL", "detail": detail}


def _skip(name: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": "SKIP", "detail": detail}


def _detail(proc: subprocess.CompletedProcess[str]) -> str:
    output = (proc.stdout or proc.stderr or "").strip().splitlines()
    text = output[0] if output else f"rc={proc.returncode}"
    return text[:240]


def _sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"
