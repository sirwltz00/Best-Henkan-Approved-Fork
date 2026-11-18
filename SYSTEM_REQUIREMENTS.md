# Henkan FE — Windows PC requirements

Henkan FE is a Windows desktop application for converting UTAU voicebanks and preparing VOCALOID singer databases. This document describes practical PC targets for the packaged Windows build.

> These are project-level operating and performance recommendations. They are not official Yamaha hardware specifications, and they are not a promise that every external DBTool/VOCALOID installation will work. The GUI's **PC Requirements** page performs a local compatibility check and explains the results.

## Supported platform

- **Target:** 64-bit Windows 10 or Windows 11.
- **CPU architecture:** Intel/AMD x64 (AMD64 / x86-64).
- **Not supported targets:** 32-bit Windows, macOS, Linux, and Windows on ARM. The packaged application is built as a 64-bit Windows executable.
- **Python:** Not required for users of the packaged application. Python 3.10+ is for contributors running the source tree; the current Windows CI package is built with Python 3.11.
- **Internet:** Not required for normal use after the complete application ZIP and any separately needed components are available.

## Hardware guidance

| Component | Minimum working target | Recommended target | Why it matters |
|---|---|---|---|
| CPU | Modern 64-bit Intel/AMD dual-core | Modern 4-core CPU or better | Audio feature extraction and resampling are CPU-based. More cores help with larger workloads and multitasking. |
| RAM | 4 GB | 8 GB; 16 GB for large banks or long recordings | WAV audio and intermediate data use memory. Close browsers, DAWs and other heavy apps when RAM is constrained. |
| Free storage | 2 GB free on the working/temp drive | 10 GB+ free on an SSD | Conversion produces temporary audio and output files. Large projects may need several times the size of the source WAV collection. |
| Display | The app window can be resized to 820 × 640 | 1280 × 800 or higher | Higher resolution makes the workflow and diagnostic panels easier to use. Windows scaling reduces usable space. |
| Graphics | Integrated graphics | Integrated or dedicated graphics | No dedicated GPU or CUDA support is required. Henkan FE's audio analysis is CPU-based. |

The storage figures are free working-space recommendations, not the size of the Henkan FE download. The actual space required depends on source-bank audio, generated units, temporary files, and how many outputs are retained.

## Required files and external software

### Always keep together

- `HenkanFE.exe` — the windowed application users launch.
- `HenkanFEWorker.exe` — the helper Henkan FE starts internally for conversion, validation, DB building, packaging, and Audio QA.

Both executables must remain in the same extracted folder, beside `_henkan_fe_gui_runtime/` and `_henkan_fe_worker_runtime/`. These two runtime folders contain the supporting libraries and data for the onedir builds; do not move or delete them. Do not launch the worker directly. If Henkan FE opens but tasks cannot start, re-extract the complete ZIP and check whether security software quarantined a required file or folder.

### Required only for building a singer DB

- `VocaloidDBTool3.exe` — a separate Windows executable configured in Henkan FE.
- The language-matched phonetic dictionary from a devkit you are authorized to use.

These proprietary/devkit files are not included in the Henkan FE source or packaged release. A missing DBTool does not mean the PC is underpowered: the GUI can still open and non-DBTool workflows may be available, but **Build singer DB** cannot run without the external tool and dictionary.

The DBTool is a legacy Windows program. If it fails with Windows side-by-side configuration error **14001**, it may need the **Microsoft Visual C++ 2008 Redistributable (x86)**. The x86 runtime can be relevant even when Henkan FE itself is 64-bit. Only install the runtime from a trusted Microsoft source.

### Optional / conditional

- **VOCALOID editor:** Needed to install a generated voicebank into an editor or to audition/render it. It is not required just to convert an UTAU bank.
- **Administrator permission:** Needed for install/uninstall actions that write under protected application folders or Windows registry keys. Henkan FE does not need to be run permanently as administrator for conversion or packaging.
- **VOCALOID4 Editor for Developer:** Used by optional developer/test-render tools; not required for the standard GUI conversion path.

## What the PC Requirements page checks

Henkan FE reads the current Windows/platform identity, process architecture, logical CPU count, installed RAM, free space on the system temporary-file drive, display resolution, and whether the worker helper exists. It separately reports whether the configured DBTool and dictionary paths exist.

The report is a diagnostic aid, not a benchmark. It also checks that the onedir worker runtime folder is present beside `HenkanFEWorker.exe`. It does not test the actual performance of a CPU, validate every audio driver, inspect the licensing status of external components, or guarantee that a particular DBTool build can be automated. Hardware warnings do not prevent Henkan FE from opening.

## Common reasons the application may not work

| Symptom | Likely cause | What to check |
|---|---|---|
| Windows says the app cannot run on this PC | Wrong OS architecture / unsupported platform | Use 64-bit Windows 10/11 on an Intel/AMD x64 PC. |
| Henkan FE opens, but starting a task fails | `HenkanFEWorker.exe` or its runtime folder is missing or blocked | Keep both EXEs and both runtime folders together; re-extract the full ZIP and inspect security-software quarantine. |
| Conversion or DB build says dictionary is missing | No language-matched phonetic dictionary configured | Select the appropriate Japanese or English dictionary in Henkan FE. |
| Build singer DB cannot start | DBTool missing, path invalid, or external devkit not configured | Configure the separately supplied `VocaloidDBTool3.exe`; confirm the dictionary path too. |
| DBTool shows side-by-side error 14001 | Legacy DBTool runtime dependency missing | Check for Microsoft Visual C++ 2008 Redistributable (x86), then restart Windows. |
| Processing is very slow or stops on large banks | Low RAM, CPU, or free disk space; large source audio | Close memory-heavy apps, use an SSD, free working space, or process a smaller bank. |
| Install/uninstall is denied | Windows protected-folder or registry permissions | Accept the UAC prompt for that install/uninstall action. Do not run the app as administrator for every task. |
| App panels do not fit on screen | Low resolution or high Windows display scaling | Use a larger display resolution or reduce Windows scaling. |

## Reporting a compatibility problem

When reporting a problem, include:

1. A screenshot or copied text from **PC Requirements**.
2. Windows edition/version and whether it is 64-bit.
3. The exact Henkan FE step that failed (Convert, Build singer DB, Pack, Install, or V3 → V2).
4. The exact error message from the activity console.
5. For DBTool failures, the DBTool version/source and whether the VC++ 2008 x86 runtime is installed.

Do not attach or redistribute Yamaha devkit binaries, dictionaries, or voicebank data you do not have permission to share.
