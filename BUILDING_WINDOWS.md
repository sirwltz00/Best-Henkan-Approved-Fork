# Building Henkan FE for Windows

The supported packaged release is Windows-only and targets 64-bit Windows 10/11 on Intel/AMD x64 PCs. Practical minimum/recommended hardware targets and runtime diagnostics are documented in `SYSTEM_REQUIREMENTS.md` and shown in the GUI under **PC Requirements**. GitHub Actions runs the regression suite and builds two PyInstaller executables:

- `HenkanFE.exe` — the Henkan Fan Edition windowed CustomTkinter application users launch.
- `HenkanFEWorker.exe` — an internal console helper used by the GUI to run conversion, validation, DBTool automation, packaging and Audio QA while streaming output into the GUI. Keep it beside `HenkanFE.exe`.

Both are PyInstaller **onedir** builds. The GUI build embeds the inherited Henkan icon and bundles `assets/brand/` for the in-app icon and Henkan FE wordmarks. The release ZIP places both executables at its top level and includes `_henkan_fe_gui_runtime/` and `_henkan_fe_worker_runtime/` beside them. Keep those runtime folders in place; they contain each executable's bundled libraries and data files. The two runtime folders are separate so the GUI and worker cannot overwrite each other's files.

The workflow is `.github/workflows/build.yml`. Push a branch/commit, open a pull request, or run it manually from **Actions → Build Windows application**. A successful run uploads `Henkan-FE-Windows.zip` as a workflow artifact.

## Local build

On 64-bit Windows with Python 3.11 installed:

```powershell
python -m pip install -r requirements.txt
python -m pip install pytest "pyinstaller>=6.0"
python -m pytest -q

# Resolve data files before invoking PyInstaller. With --specpath set to a
# build subdirectory, relative --add-data paths may be resolved from that
# generated spec directory instead of the repository root.
$idTables = (Resolve-Path "v3v2/idtables.json").Path
$v2JapaneseUnits = (Resolve-Path "v3v2/v2_japanese_units.json").Path
$brandAssets = (Resolve-Path "assets/brand").Path
$appIcon = (Resolve-Path "assets/brand/henkan-icon.ico").Path

python -m PyInstaller --clean --noconfirm --onedir --windowed --name HenkanFE `
  --icon "$appIcon" `
  --contents-directory _henkan_fe_gui_runtime `
  --distpath dist/gui --workpath build/gui --specpath build/spec-gui `
  --collect-all customtkinter --collect-all numpy `
  --add-data "$idTables;v3v2" `
  --add-data "$v2JapaneseUnits;v3v2" `
  --add-data "$brandAssets;assets/brand" henkan_app.py

python -m PyInstaller --clean --noconfirm --onedir --console --name HenkanFEWorker `
  --contents-directory _henkan_fe_worker_runtime `
  --distpath dist/worker --workpath build/worker --specpath build/spec-worker `
  --collect-all numpy --collect-all pywinauto `
  --hidden-import pythoncom --hidden-import pywintypes --hidden-import win32timezone `
  --hidden-import win32api --hidden-import win32con --hidden-import win32gui `
  --hidden-import win32process --hidden-import win32event --hidden-import win32com.client `
  --add-data "$idTables;v3v2" `
  --add-data "$v2JapaneseUnits;v3v2" henkan_worker.py
```

The build outputs are `dist/gui/HenkanFE/HenkanFE.exe` plus its `_henkan_fe_gui_runtime/` folder, and `dist/worker/HenkanFEWorker/HenkanFEWorker.exe` plus its `_henkan_fe_worker_runtime/` folder. To assemble a runnable release folder, put both EXEs and both runtime folders together at the same top level (the Actions workflow does this automatically). Do not copy only the EXEs: each one needs its matching runtime folder beside it.

```powershell
$release = "dist/Henkan-FE-Windows"
New-Item -ItemType Directory -Force -Path $release | Out-Null
Copy-Item "dist/gui/HenkanFE/HenkanFE.exe" $release
Copy-Item "dist/worker/HenkanFEWorker/HenkanFEWorker.exe" $release
Copy-Item "dist/gui/HenkanFE/_henkan_fe_gui_runtime" "$release/_henkan_fe_gui_runtime" -Recurse
Copy-Item "dist/worker/HenkanFEWorker/_henkan_fe_worker_runtime" "$release/_henkan_fe_worker_runtime" -Recurse
```

## External DBTool requirement

The workflow intentionally does **not** fetch, bundle or redistribute `VocaloidDBTool3.exe`, Yamaha libraries, voicebank content or devkit dictionaries. The user must supply and configure an independently obtained DBTool and dictionary they are authorized to use. If DBTool reports side-by-side error 14001, its legacy process may require the Microsoft Visual C++ 2008 Redistributable (x86). Henkan FE is built as an application; it does not package the proprietary tool it automates.
