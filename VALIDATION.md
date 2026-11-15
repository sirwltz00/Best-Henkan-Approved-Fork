# Validation record

- Automated command: `python -m unittest discover -v`
- Result: **186 tests run; 0 failures; 4 skipped**.
- The skipped cases require Windows code-page behavior or an external VOCALOID
  developer-kit sample database that was not included in the source archive.
- Syntax check: `python -m compileall -q .` completed successfully.
- CLI smoke check: `python henkan.py --help` completed successfully.

The added converter/DB tests include OTO marker selection for CV and VCV edges, nested case-insensitive WAV lookup, lowercase alias-tail preservation, append-scoped prefix-map handling, malformed OTO diagnostics, DBTool input preflight, and post-build artifact verification with a synthetic PHDC chunk.

This validates the Python implementation and its synthetic/regression tests.
It does not claim that every converted voicebank has been auditioned, or that
Yamaha DBTool build/pack/install workflows were exercised against a real
installed developer kit. Use `docs/VOICEBANK_EVALUATION_TEMPLATE.md` for those
manual checks.
