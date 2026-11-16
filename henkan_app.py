"""Windows GUI entry point for the Henkan PyInstaller application."""
import os
import sys
import tempfile
import traceback


def _self_test_log_path():
    """Return the CI-provided diagnostic path, or a stable temp-file fallback."""
    return os.environ.get(
        "HENKAN_SELF_TEST_LOG",
        os.path.join(tempfile.gettempdir(), "Henkan-self-test.log"),
    )


def _write_self_test_log(message):
    """Write diagnostics even in a PyInstaller --windowed executable."""
    path = _self_test_log_path()
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(message.rstrip() + "\n")
    return path


def _self_test():
    """Import the frozen GUI stack and check bundled V3/V2 data without opening a window."""
    try:
        import customtkinter  # noqa: F401
        import numpy  # noqa: F401
        import gui  # noqa: F401
        import u2v  # noqa: F401
        import v3v2.convert  # noqa: F401
        import v3v2.install  # noqa: F401

        root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        required = (
            os.path.join(root, "v3v2", "idtables.json"),
            os.path.join(root, "v3v2", "v2_japanese_units.json"),
            os.path.join(root, "assets", "brand", "henkan-icon-128.png"),
            os.path.join(root, "assets", "brand", "henkan-icon.ico"),
            os.path.join(root, "assets", "brand", "henkan-wordmark-dark.png"),
        )
        missing = [path for path in required if not os.path.isfile(path)]
        if missing:
            raise RuntimeError(
                "Missing bundled application data: " + ", ".join(missing)
            )

        _write_self_test_log(
            "SELF-TEST PASSED\n"
            "Frozen GUI imports: customtkinter, numpy, gui, u2v, "
            "v3v2.convert, v3v2.install\n"
            "Bundled V3/V2 JSON resources and Henkan brand assets: present\n"
            "PyInstaller runtime directory: " + root
        )
        return 0
    except Exception:
        details = traceback.format_exc()
        try:
            _write_self_test_log("SELF-TEST FAILED\n" + details)
        except Exception:
            # The process is windowed, so stderr may not exist. Keep the original
            # exception visible to the process exit code even if logging fails.
            pass
        return 1


def main():
    from gui import main as gui_main
    gui_main()


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        raise SystemExit(_self_test())
    main()
