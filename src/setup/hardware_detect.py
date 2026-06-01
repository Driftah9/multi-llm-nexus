"""
Hardware detection for local LLM setup — CPU cores, RAM, GPU presence.
Recommends appropriate model sizes based on available resources.
"""
import asyncio
import subprocess
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class HardwareInfo:
    cpu_cores: int          # logical cores
    ram_gb: float           # available RAM in GB
    has_gpu: bool           # GPU detected
    gpu_type: Optional[str] # "nvidia", "amd", "intel", None
    gpu_vram_gb: Optional[float]  # VRAM if GPU present
    recommended_local: bool # should we recommend local LLM?
    recommended_model: Optional[str]  # suggested model if recommended_local


async def detect_hardware() -> HardwareInfo:
    """Probe the system for CPU, RAM, and GPU details."""
    cpu_cores = _get_cpu_cores()
    ram_gb = _get_ram_gb()
    gpu_type, gpu_vram_gb = await _detect_gpu()

    # Decision: is the hardware suitable for a local LLM?
    recommended_local = ram_gb >= 8  # minimum 8GB for useful local inference
    recommended_model = None

    if recommended_local:
        if gpu_type == "nvidia" and gpu_vram_gb and gpu_vram_gb >= 6:
            recommended_model = "llama3.1:8b"
        elif gpu_type == "nvidia" and gpu_vram_gb and gpu_vram_gb >= 3:
            recommended_model = "llama3.2:3b"
        elif gpu_type:  # AMD or Intel GPU
            recommended_model = "phi4-mini"
        elif ram_gb >= 32:
            recommended_model = "llama3.1:8b"  # CPU can handle 8b if plenty of RAM
        elif ram_gb >= 16:
            recommended_model = "llama3.2:3b"
        else:
            recommended_model = "phi4-mini"  # smallest, ~1GB

    return HardwareInfo(
        cpu_cores=cpu_cores,
        ram_gb=ram_gb,
        has_gpu=gpu_type is not None,
        gpu_type=gpu_type,
        gpu_vram_gb=gpu_vram_gb,
        recommended_local=recommended_local,
        recommended_model=recommended_model,
    )


def _get_cpu_cores() -> int:
    """Get logical CPU core count."""
    try:
        result = subprocess.run(
            ["nproc"], capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass

    # Fallback: read /proc/cpuinfo
    try:
        with open("/proc/cpuinfo") as f:
            return sum(1 for line in f if line.startswith("processor"))
    except Exception:
        return 2  # Safe default


def _get_ram_gb() -> float:
    """Get total available RAM in GB."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb / (1024 * 1024)
    except Exception:
        pass
    return 4.0  # Safe default


async def _detect_gpu() -> tuple[Optional[str], Optional[float]]:
    """
    Detect GPU presence and VRAM.
    Returns: (gpu_type, vram_gb) where gpu_type is "nvidia", "amd", "intel", or None
    """
    # Try NVIDIA
    gpu_type, vram = await _check_nvidia_gpu()
    if gpu_type:
        return gpu_type, vram

    # Try AMD/ROCm
    gpu_type, vram = await _check_amd_gpu()
    if gpu_type:
        return gpu_type, vram

    # Try Intel Arc via lspci (less reliable but better than nothing)
    if _check_intel_gpu():
        return "intel", None  # No easy way to get Intel Arc VRAM

    return None, None


async def _check_nvidia_gpu() -> tuple[Optional[str], Optional[float]]:
    """Check for NVIDIA GPU via nvidia-smi."""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,nounits,noheader"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            # Output is in MB
            mb = int(result.stdout.strip().split()[0])
            vram_gb = mb / 1024.0
            return "nvidia", vram_gb
    except Exception:
        pass
    return None, None


async def _check_amd_gpu() -> tuple[Optional[str], Optional[float]]:
    """Check for AMD/ROCm GPU via rocm-smi."""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["rocm-smi", "--showproductname", "--json"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            # If rocm-smi works, we have an AMD GPU (VRAM harder to extract)
            return "amd", None
    except Exception:
        pass
    return None, None


def _check_intel_gpu() -> bool:
    """Check for Intel GPU via lspci."""
    try:
        result = subprocess.run(
            ["lspci"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            # Look for Intel VGA or 3D controller
            for line in result.stdout.split("\n"):
                if "Intel" in line and ("VGA" in line or "3D" in line or "Arc" in line):
                    return True
    except Exception:
        pass
    return False


def hardware_report(info: HardwareInfo) -> str:
    """Format hardware info as a human-readable report."""
    lines = [
        f"  CPU: {info.cpu_cores} cores",
        f"  RAM: {info.ram_gb:.1f} GB",
    ]

    if info.has_gpu:
        gpu_str = info.gpu_type.upper() if info.gpu_type else "Unknown"
        if info.gpu_vram_gb:
            lines.append(f"  GPU: {gpu_str} ({info.gpu_vram_gb:.1f} GB VRAM)")
        else:
            lines.append(f"  GPU: {gpu_str} (VRAM unknown)")
    else:
        lines.append("  GPU: None (CPU-only)")

    if info.recommended_local:
        lines.append(f"\n✓ Local LLM recommended — {info.recommended_model}")
        if info.has_gpu:
            lines.append("  GPU acceleration enabled.")
        else:
            lines.append("  (CPU inference — slower but still useful for triage.)")
    else:
        lines.append("\n✗ Insufficient RAM for local LLM (8GB minimum recommended).")

    return "\n".join(lines)
