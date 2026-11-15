# Validation record

- Automated command: `python -m unittest discover -v`
- Latest result: **201 tests run; 0 failures; 4 skipped**.
- The skipped cases require Windows code-page behavior or an external VOCALOID
  developer-kit sample database that was not included in the source archive.
- Syntax check: `python -m compileall -q .` completed successfully.
- GUI import check: `import gui` completed successfully.
- CLI smoke checks completed for `henkan.py pack --help`, `henkan.py install --help`,
  and `tools/parameter_probe.py --help`.

The regression suite covers audio analysis, OTO parsing and segmentation, DBTool
preflight/post-build validation, VQM metadata and chunk rebasing, Singer Editor
voice-profile ranges, native VSQX4 `<cc>` serialization, note-level OPE/VEL,
parameter-probe generation, and the existing converter/backport behavior.

These checks validate the Python implementation and synthetic file structures.
They do not claim that every converted voicebank has been auditioned, or that the
Yamaha DBTool / VOCALOID4 Editor was run against a real installed developer kit.
Parameter audibility must still be checked by opening the generated probe projects
in the target VOCALOID4 desktop editor and rendering/listening to the A/B phrases.
Older/mobile editors may ignore some V4-specific controls. Use
`docs/VOICEBANK_EVALUATION_TEMPLATE.md` for manual bank checks.
