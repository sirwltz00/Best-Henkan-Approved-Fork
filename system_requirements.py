"""PC requirement guidance and read-only diagnostics for the Henkan Windows app.

The thresholds here are practical project guidance, not hardware requirements supplied by
Yamaha.  They intentionally produce warnings instead of preventing the application from opening.
"""
from __future__ import annotations

import ctypes
import multiprocessing
import os
import platform
import shutil
import struct
import tempfile
from typing import Any

GB = 1024 ** 3


def _memory_bytes() -> tuple[int | None, int | None]:
    """Return (total, available) physical RAM bytes when the OS exposes it."""
    if os.name == 'nt':
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ('dwLength', ctypes.c_ulong),
                ('dwMemoryLoad', ctypes.c_ulong),
                ('ullTotalPhys', ctypes.c_ulonglong),
                ('ullAvailPhys', ctypes.c_ulonglong),
                ('ullTotalPageFile', ctypes.c_ulonglong),
                ('ullAvailPageFile', ctypes.c_ulonglong),
                ('ullTotalVirtual', ctypes.c_ulonglong),
                ('ullAvailVirtual', ctypes.c_ulonglong),
                ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
            ]
        try:
            status = MEMORYSTATUSEX()
            status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys), int(status.ullAvailPhys)
        except Exception:  # noqa: BLE001 - diagnostics must never stop startup
            pass
    try:
        page = int(os.sysconf('SC_PAGE_SIZE'))
        total = int(os.sysconf('SC_PHYS_PAGES')) * page
        available_pages = int(os.sysconf('SC_AVPHYS_PAGES'))
        return total, available_pages * page
    except (AttributeError, OSError, ValueError):
        return None, None


def _windows_version_supported() -> bool:
    if os.name != 'nt':
        return False
    try:
        ver = __import__('sys').getwindowsversion()
        return ver.major == 10  # Windows 10 and Windows 11 both report major version 10.
    except Exception:  # noqa: BLE001
        return False


def _windows_description() -> str:
    if os.name != 'nt':
        return '%s %s' % (platform.system() or 'Unknown OS', platform.release() or '')
    try:
        ver = __import__('sys').getwindowsversion()
        if ver.major == 10 and ver.build >= 22000:
            name = 'Windows 11'
        elif ver.major == 10:
            name = 'Windows 10'
        else:
            name = 'Windows %d.%d' % (ver.major, ver.minor)
        return '%s (build %d)' % (name, ver.build)
    except Exception:  # noqa: BLE001
        return platform.platform()


def collect_system_info(app_dir: str | None = None, screen_size: tuple[int, int] | None = None,
                        frozen: bool = False) -> dict[str, Any]:
    """Collect local specs without installing packages, opening external apps or changing settings."""
    app_dir = os.path.abspath(app_dir or os.getcwd())
    ram_total, ram_available = _memory_bytes()
    # Henkan's DBTool preparation uses the system temp directory; check that drive as the
    # most useful common workspace signal. The chosen output drive still needs its own space.
    disk_path = tempfile.gettempdir()
    try:
        disk_free = int(shutil.disk_usage(disk_path).free)
    except OSError:
        disk_free = None

    try:
        logical_cores = multiprocessing.cpu_count()
    except NotImplementedError:
        logical_cores = None

    machine = platform.machine() or 'Unknown'
    processor = os.environ.get('PROCESSOR_IDENTIFIER') or platform.processor() or machine
    bits = struct.calcsize('P') * 8
    worker_name = 'HenkanWorker.exe' if frozen else 'henkan_worker.py'
    worker_path = os.path.join(app_dir, worker_name)

    return {
        'os_name': _windows_description(),
        'is_windows': os.name == 'nt',
        'is_supported_windows': _windows_version_supported(),
        'architecture': machine,
        'process_bits': bits,
        'is_64bit': bits == 64 and machine.lower() in ('amd64', 'x86_64', 'x64'),
        'processor': processor,
        'logical_cores': logical_cores,
        'ram_total_bytes': ram_total,
        'ram_available_bytes': ram_available,
        'disk_free_bytes': disk_free,
        'disk_path': disk_path,
        'screen_width': screen_size[0] if screen_size else None,
        'screen_height': screen_size[1] if screen_size else None,
        'worker_path': worker_path,
        'worker_exists': os.path.isfile(worker_path),
        'frozen': bool(frozen),
    }


def _gb(value: int | None) -> str:
    return 'Not available' if value is None else '%.1f GB' % (value / GB)


def evaluate_system(info: dict[str, Any]) -> list[dict[str, str]]:
    """Convert collected values into friendly, non-blocking status rows."""
    rows: list[dict[str, str]] = []
    windows = bool(info.get('is_windows')) and bool(info.get('is_supported_windows', info.get('is_windows')))
    is_64 = bool(info.get('is_64bit'))
    rows.append({
        'key': 'platform', 'name': 'Windows / architecture',
        'detected': '%s · %s-bit (%s)' % (info.get('os_name', 'Unknown'), info.get('process_bits', '?'),
                                          info.get('architecture', 'Unknown')),
        'minimum': 'Windows 10 or 11, 64-bit', 'recommended': 'Windows 11, 64-bit',
        'status': 'good' if windows and is_64 else 'bad',
        'explanation': ('This Windows build targets 64-bit Windows.' if windows and is_64 else
                        'This packaged build targets 64-bit Windows. Other operating systems and 32-bit Windows '
                        'are not supported for the packaged application.'),
    })

    cores = info.get('logical_cores')
    rows.append({
        'key': 'cpu', 'name': 'Processor',
        'detected': '%s · %s logical cores' % (info.get('processor') or 'Unknown CPU', cores or 'Unknown'),
        'minimum': 'Modern 64-bit dual-core CPU', 'recommended': 'Modern 4-core CPU or better',
        'status': 'good' if isinstance(cores, int) and cores >= 4 else
                  ('info' if isinstance(cores, int) and cores >= 2 else 'warn'),
        'explanation': ('More cores help when analysing larger banks. Henkan does not require a dedicated GPU.'),
    })

    ram = info.get('ram_total_bytes')
    ram_gb = ram / GB if isinstance(ram, int) else None
    rows.append({
        'key': 'ram', 'name': 'Installed memory (RAM)',
        'detected': ('%s total · %s available' % (_gb(ram), _gb(info.get('ram_available_bytes')))),
        'minimum': '4 GB', 'recommended': '8 GB (16 GB for large banks)',
        'status': 'good' if ram_gb is not None and ram_gb >= 8 else
                  ('info' if ram_gb is not None and ram_gb >= 4 else 'warn'),
        'explanation': ('Large voicebanks and long WAV recordings need more memory. Close other audio/editing apps '
                        'when memory is low.'),
    })

    disk = info.get('disk_free_bytes')
    disk_gb = disk / GB if isinstance(disk, int) else None
    disk_path = str(info.get('disk_path') or '')
    disk_volume = os.path.splitdrive(disk_path)[0] or disk_path
    disk_detected = _gb(disk) + ((' free · ' + disk_volume) if disk_volume else '')
    rows.append({
        'key': 'disk', 'name': 'Free working space (temp drive)',
        'detected': disk_detected, 'minimum': '2 GB free', 'recommended': '10 GB+ on an SSD',
        'status': 'good' if disk_gb is not None and disk_gb >= 10 else
                  ('info' if disk_gb is not None and disk_gb >= 2 else 'warn'),
        'explanation': ('Temporary WAVs and generated voicebank files can use several times the source audio size. '
                        'Keep additional room for large projects.'),
    })

    width, height = info.get('screen_width'), info.get('screen_height')
    resolution = ('%s × %s' % (width, height)) if width and height else 'Not available'
    enough = bool(width and height and width >= 1024 and height >= 700)
    can_fit = bool(width and height and width >= 820 and height >= 640)
    rows.append({
        'key': 'display', 'name': 'Display resolution', 'detected': resolution,
        'minimum': 'At least 820 × 640 to fit the app window',
        'recommended': '1280 × 800 or higher',
        'status': 'good' if enough else ('warn' if not can_fit else 'info'),
        'explanation': ('Higher resolution makes the workflow panels easier to use. Windows display scaling may '
                        'also affect the available workspace.'),
    })

    rows.append({
        'key': 'worker', 'name': 'Henkan worker helper',
        'detected': ('Found: %s' % os.path.basename(info.get('worker_path', ''))
                     if info.get('worker_exists') else 'Missing: %s' % os.path.basename(info.get('worker_path', ''))),
        'minimum': 'Keep HenkanWorker.exe beside Henkan.exe',
        'recommended': 'Extract the complete Henkan-Windows.zip',
        'status': 'good' if info.get('worker_exists') else 'bad',
        'explanation': ('Henkan uses the worker to run conversion and build tasks. Re-extract the complete ZIP '
                        'if it is missing or was quarantined.'),
    })
    return rows


def overall_status(rows: list[dict[str, str]]) -> tuple[str, str]:
    """Return (status, human-readable summary); performance warnings never block startup."""
    bad = [row for row in rows if row.get('status') == 'bad']
    warnings = [row for row in rows if row.get('status') == 'warn']
    if bad:
        return 'bad', 'One or more compatibility requirements are not met. Henkan may not run correctly.'
    if warnings:
        return 'warn', 'This PC is below at least one minimum working target. Some tasks may be slow or fail.'
    return 'good', 'This PC meets the listed Henkan requirements.'
