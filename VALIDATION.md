# Validation record

- Automated command: `python -m pytest -q`
- Latest result for this source revision: **225 passed; 4 skipped; 6 subtests passed**.
- Syntax check: `python -m py_compile gui.py system_requirements.py henkan.py henkan_app.py henkan_worker.py` completed successfully.
- Full Python source compile check: `python -m compileall -q .` completed successfully.
- Windows packaging workflow at `.github/workflows/build.yml` now builds both applications with PyInstaller `--onedir`. Their executables are assembled at the release ZIP root, with separate `_henkan_fe_gui_runtime/` and `_henkan_fe_worker_runtime/` support folders so the GUI's same-folder worker lookup still works without merging their bundled files. PyInstaller's `--add-data` sources remain absolute repository paths. CI smoke-tests GUI imports, bundled JSON resources, CLI dispatch and Audio QA dispatch on `windows-latest` before uploading the application ZIP. Source-level tests cover the worker dispatcher and the frozen worker runtime-folder diagnostic.
- Frozen Windows `.exe` files were **not built in this Linux editing environment**. The Windows GitHub Actions run is still required to verify the onedir PyInstaller builds and executable smoke tests; Winlator compatibility also needs to be checked on the target setup.
- Windows DBTool / VOCALOID4 Editor integration remains dependent on the user's independently supplied
  DBTool and dictionary installation and must be checked on Windows.

The regression suite covers audio analysis, OTO parsing and segmentation, DBTool preflight/post-build
validation, single- and multi-sample `Grw` conversion, commercial `art/sta/vqm` folder discovery, VQM
index preservation and chunk rebasing, Singer Editor voice-profile ranges, native VSQX4 `<cc>`
serialization, note-level OPE/VEL, parameter-probe generation, and the existing converter/backport
behavior.

System-requirement evaluator tests cover minimum and recommended PC tiers, unsupported OS/architecture, low RAM/disk, undersized display and missing worker. These checks validate the Python implementation and synthetic file structures. They do not claim that
every converted voicebank has been auditioned, or that the Yamaha DBTool / VOCALOID4 Editor was run
against a real installed developer kit. Parameter audibility must still be checked by opening the
generated probe projects in the target VOCALOID4 desktop editor and rendering/listening to the A/B
phrases. Older/mobile editors may ignore some V4-specific controls. Use
`docs/VOICEBANK_EVALUATION_TEMPLATE.md` for manual bank checks.

- CustomTkinter is not installed in this Linux editing environment, so the GUI source was syntax-checked but not visually launched here. The Windows Actions build installs the GUI dependency and runs the frozen GUI import/resource smoke test.
