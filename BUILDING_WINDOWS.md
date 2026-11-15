# Building the Windows application

The supported packaged release is Windows-only and targets 64-bit Windows 10/11 on Intel/AMD x64 PCs. Practical minimum/recommended hardware targets and runtime diagnostics are documented in `SYSTEM_REQUIREMENTS.md` and shown in the GUI under **PC Requirements**. GitHub Actions runs the regression suite and builds two PyInstaller executables:

- `Henkan.exe` — the windowed CustomTkinter application users launch.
- `HenkanWorker.exe` — an internal console helper used by the GUI to run conversion, validation, DBTool automation, packaging and Audio QA while streaming output into the GUI. Keep it beside `Henkan.exe`.

The workflow is `.github/workflows/build.yml`. Push a branch/commit, open a pull request, or run it manually from **Actions → Build Windows application**. A successful run uploads `Henkan-Windows.zip` as a workflow artifact.

## Local build

On 64-bit Windows with Python 3.11 installed:

```powershell
python -m pip install -r requirements.txt
python -m pip install pytest pyinstaller
python -m pytest -q

python -m PyInstaller --clean --noconfirm --onefile --windowed --name Henkan `
  --distpath dist/gui --workpath build/gui --specpath build/spec-gui `
  --collect-all customtkinter --collect-all numpy `
  --add-data "v3v2/idtables.json;v3v2" `
  --add-data "v3v2/v2_japanese_units.json;v3v2" henkan_app.py

python -m PyInstaller --clean --noconfirm --onefile --console --name HenkanWorker `
  --distpath dist/worker --workpath build/worker --specpath build/spec-worker `
  --collect-all numpy --collect-all pywinauto `
  --hidden-import pythoncom --hidden-import pywintypes --hidden-import win32timezone `
  --hidden-import win32api --hidden-import win32con --hidden-import win32gui `
  --hidden-import win32process --hidden-import win32event --hidden-import win32com.client `
  --add-data "v3v2/idtables.json;v3v2" `
  --add-data "v3v2/v2_japanese_units.json;v3v2" henkan_worker.py
```

The GitHub Actions workflow runs import/data smoke tests against both frozen executables. Copy `dist/gui/Henkan.exe` and `dist/worker/HenkanWorker.exe` into one folder before running the app. Both executables are required.

## External DBTool requirement

The workflow intentionally does **not** fetch, bundle or redistribute `VocaloidDBTool3.exe`, Yamaha libraries, voicebank content or devkit dictionaries. The user must supply and configure an independently obtained DBTool and dictionary they are authorized to use. If DBTool reports side-by-side error 14001, its legacy process may require the Microsoft Visual C++ 2008 Redistributable (x86). Henkan is built as an application; it does not package the proprietary tool it automates.
