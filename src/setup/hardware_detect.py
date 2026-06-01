"""
Hardware detection for local LLM setup — CPU cores, RAM, GPU vendor/VRAM.

Recommends appropriate providers and model sizes based on detected hardware:
  NVIDIA GPU → ik_llama (MoE-optimized) or ollama (simpler)
  AMD GPU    → vllm (ROCm backend)
  Intel GPU  → vllm (XPU/SYCL backend) or ipex-llm (Ollama fork)
  CPU-only   → ollama (easiest setup)
"""
import asyncio
import subprocess
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class GPUInfo:
    vendor: str                     # "nvidia", "amd", "intel"
    name: Optional[str] = None      # e.g. "Tesla V100-SXM2-32GB", "Arc Pro B60"
    vram_gb: Optional[float] = None
    driver: Optional[str] = None    # e.g. "CUDA 12.6", "ROCm 6.1", "oneAPI 2025.1"
    count: int = 1                  # number of GPUs detected


@dataclass
class HardwareInfo:
    cpu_cores: int
    ram_gb: float
    has_gpu: bool
    gpu_type: Optional[str]         # "nvidia", "amd", "intel", None
    gpu_vram_gb: Optional[float]
    gpu_info: Optional[GPUInfo] = None
    recommended_local: bool = False
    recommended_model: Optional[str] = None
    recommended_provider: Optional[str] = None
    provider_notes: Optional[str] = None


async def detect_hardware() -> HardwareInfo:
    """Probe the system for CPU, RAM, and GPU details."""
    cpu_cores = _get_cpu_cores()
    ram_gb = _get_ram_gb()
    gpu_info = await _detect_gpu()

    gpu_type = gpu_info.vendor if gpu_info else None
    gpu_vram_gb = gpu_info.vram_gb if gpu_info else None

    recommended_local = ram_gb >= 8
    recommended_model = None
    recommended_provider = None
    provider_notes = None

    if recommended_local:
        if gpu_type == "nvidia":
            if gpu_vram_gb and gpu_vram_gb >= 16:
                recommended_model = "qwen2.5-coder-32b-iq3_k"
                recommended_provider = "ik_llama"
                provider_notes = (
                    "NVIDIA GPU with 16GB+ VRAM detected. "
                    "ik_llama.cpp recommended for MoE models and GGUF quants. "
                    "vLLM also supported if you prefer HuggingFace model format."
                )
            elif gpu_vram_gb and gpu_vram_gb >= 6:
                recommended_model = "llama3.1:8b"
                recommended_provider = "ollama"
                provider_notes = (
                    "NVIDIA GPU detected. Ollama recommended for easy setup. "
                    "ik_llama.cpp available for advanced GPU inference."
                )
            elif gpu_vram_gb and gpu_vram_gb >= 3:
                recommended_model = "llama3.2:3b"
                recommended_provider = "ollama"
                provider_notes = "NVIDIA GPU with limited VRAM. Ollama with small models."
            else:
                recommended_model = "phi4-mini"
                recommended_provider = "ollama"
                provider_notes = "NVIDIA GPU detected but VRAM is limited."

        elif gpu_type == "amd":
            recommended_provider = "vllm"
            if gpu_vram_gb and gpu_vram_gb >= 16:
                recommended_model = "Qwen/Qwen2.5-14B-Instruct"
                provider_notes = (
                    "AMD GPU detected. vLLM with ROCm backend recommended. "
                    "Install: pip install vllm (ROCm build). "
                    "Ollama also supports AMD via rocm but vLLM gives better throughput."
                )
            elif gpu_vram_gb and gpu_vram_gb >= 6:
                recommended_model = "meta-llama/Llama-3.1-8B-Instruct"
                provider_notes = "AMD GPU detected. vLLM with ROCm backend recommended."
            else:
                recommended_model = "meta-llama/Llama-3.2-3B-Instruct"
                provider_notes = "AMD GPU detected with limited VRAM. vLLM with small models."

        elif gpu_type == "intel":
            recommended_provider = "vllm"
            if gpu_vram_gb and gpu_vram_gb >= 16:
                recommended_model = "Qwen/Qwen2.5-14B-Instruct"
                provider_notes = (
                    "Intel Arc GPU detected. vLLM with XPU/SYCL backend recommended. "
                    "Run: vllm serve <model> --device xpu. "
                    "Alternative: IPEX-LLM (Intel's Ollama fork) for Ollama-style workflow."
                )
            elif gpu_vram_gb and gpu_vram_gb >= 6:
                recommended_model = "meta-llama/Llama-3.1-8B-Instruct"
                provider_notes = (
                    "Intel Arc GPU detected. vLLM with --device xpu recommended. "
                    "Alternative: IPEX-LLM fork of Ollama."
                )
            else:
                recommended_model = "meta-llama/Llama-3.2-3B-Instruct"
                provider_notes = "Intel GPU detected. vLLM with --device xpu."

        else:
            # CPU-only
            if ram_gb >= 32:
                recommended_model = "llama3.1:8b"
            elif ram_gb >= 16:
                recommended_model = "llama3.2:3b"
            else:
                recommended_model = "phi4-mini"
            recommended_provider = "ollama"
            provider_notes = "No GPU detected. CPU-only inference via Ollama."

    return HardwareInfo(
        cpu_cores=cpu_cores,
        ram_gb=ram_gb,
        has_gpu=gpu_type is not None,
        gpu_type=gpu_type,
        gpu_vram_gb=gpu_vram_gb,
        gpu_info=gpu_info,
        recommended_local=recommended_local,
        recommended_model=recommended_model,
        recommended_provider=recommended_provider,
        provider_notes=provider_notes,
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
    try:
        with open("/proc/cpuinfo") as f:
            return sum(1 for line in f if line.startswith("processor"))
    except Exception:
        return 2


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
    return 4.0


async def _detect_gpu() -> Optional[GPUInfo]:
    """Detect GPU presence, vendor, VRAM, and driver stack."""
    info = await _check_nvidia_gpu()
    if info:
        return info

    info = await _check_amd_gpu()
    if info:
        return info

    info = await _check_intel_gpu()
    if info:
        return info

    return None


async def _check_nvidia_gpu() -> Optional[GPUInfo]:
    """Check for NVIDIA GPU via nvidia-smi."""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version,count",
                "--format=csv,nounits,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split("\n")[0].split(", ")
            name = parts[0] if len(parts) > 0 else None
            vram_mb = int(parts[1]) if len(parts) > 1 else None
            driver = parts[2] if len(parts) > 2 else None

            # Count GPUs
            count_result = await asyncio.to_thread(
                subprocess.run,
                ["nvidia-smi", "--list-gpus"],
                capture_output=True, text=True, timeout=3,
            )
            count = len(count_result.stdout.strip().split("\n")) if count_result.returncode == 0 else 1

            return GPUInfo(
                vendor="nvidia",
                name=name,
                vram_gb=vram_mb / 1024.0 if vram_mb else None,
                driver=f"CUDA (driver {driver})" if driver else "CUDA",
                count=count,
            )
    except Exception:
        pass
    return None


async def _check_amd_gpu() -> Optional[GPUInfo]:
    """Check for AMD/ROCm GPU via rocm-smi."""
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["rocm-smi", "--showproductname"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            name = None
            for line in result.stdout.split("\n"):
                if "GPU" in line and ":" in line:
                    name = line.split(":")[-1].strip()
                    break

            # Try to get VRAM
            vram_gb = None
            mem_result = await asyncio.to_thread(
                subprocess.run,
                ["rocm-smi", "--showmeminfo", "vram"],
                capture_output=True, text=True, timeout=3,
            )
            if mem_result.returncode == 0:
                for line in mem_result.stdout.split("\n"):
                    if "Total" in line:
                        match = re.search(r"(\d+)", line)
                        if match:
                            vram_gb = int(match.group(1)) / (1024 * 1024 * 1024)

            return GPUInfo(
                vendor="amd",
                name=name,
                vram_gb=vram_gb,
                driver="ROCm",
            )
    except Exception:
        pass
    return None


async def _check_intel_gpu() -> Optional[GPUInfo]:
    """Check for Intel GPU via xpu-smi, sycl-ls, or lspci."""
    # Try xpu-smi first (best: gives VRAM and device name)
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["xpu-smi", "discovery"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            name = None
            vram_gb = None
            for line in result.stdout.split("\n"):
                if "Device Name" in line:
                    name = line.split(":")[-1].strip()
                if "Memory Physical Size" in line:
                    match = re.search(r"([\d.]+)\s*(GiB|MiB|GB|MB)", line)
                    if match:
                        val = float(match.group(1))
                        unit = match.group(2)
                        if unit in ("MiB", "MB"):
                            val /= 1024
                        vram_gb = val
            return GPUInfo(
                vendor="intel",
                name=name,
                vram_gb=vram_gb,
                driver="oneAPI/SYCL",
            )
    except Exception:
        pass

    # Try sycl-ls (detects Intel GPU via oneAPI runtime)
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["sycl-ls"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "Intel" in line and ("GPU" in line or "Arc" in line or "Level-Zero" in line):
                    name_match = re.search(r"Intel.*?(Arc[^,\]]*|GPU[^,\]]*)", line)
                    return GPUInfo(
                        vendor="intel",
                        name=name_match.group(0).strip() if name_match else "Intel GPU",
                        driver="oneAPI/SYCL",
                    )
    except Exception:
        pass

    # Fallback: lspci
    try:
        result = subprocess.run(
            ["lspci"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "Intel" in line and ("VGA" in line or "3D" in line or "Display" in line):
                    # Skip integrated graphics (common on all Intel CPUs)
                    if "Arc" in line or "DG" in line or "Battlemage" in line:
                        name_match = re.search(r"Intel.*$", line)
                        return GPUInfo(
                            vendor="intel",
                            name=name_match.group(0).strip() if name_match else "Intel Arc",
                            driver="unknown (install oneAPI for GPU acceleration)",
                        )
    except Exception:
        pass

    return None


def hardware_report(info: HardwareInfo) -> str:
    """Format hardware info as a human-readable report."""
    lines = [
        f"  CPU: {info.cpu_cores} cores",
        f"  RAM: {info.ram_gb:.1f} GB",
    ]

    if info.gpu_info:
        gpu = info.gpu_info
        gpu_label = gpu.name or gpu.vendor.upper()
        vram_str = f"{gpu.vram_gb:.1f} GB VRAM" if gpu.vram_gb else "VRAM unknown"
        driver_str = f", {gpu.driver}" if gpu.driver else ""
        count_str = f" x{gpu.count}" if gpu.count > 1 else ""
        lines.append(f"  GPU: {gpu_label}{count_str} ({vram_str}{driver_str})")
    elif info.has_gpu:
        gpu_str = info.gpu_type.upper() if info.gpu_type else "Unknown"
        if info.gpu_vram_gb:
            lines.append(f"  GPU: {gpu_str} ({info.gpu_vram_gb:.1f} GB VRAM)")
        else:
            lines.append(f"  GPU: {gpu_str} (VRAM unknown)")
    else:
        lines.append("  GPU: None (CPU-only)")

    if info.recommended_local:
        lines.append(f"\n✓ Local LLM recommended")
        lines.append(f"  Provider: {info.recommended_provider}")
        lines.append(f"  Model: {info.recommended_model}")
        if info.provider_notes:
            lines.append(f"  {info.provider_notes}")
    else:
        lines.append("\n✗ Insufficient RAM for local LLM (8GB minimum recommended).")

    return "\n".join(lines)
