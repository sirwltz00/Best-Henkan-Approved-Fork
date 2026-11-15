"""Windows GUI entry point for the Henkan PyInstaller application."""
import os
import sys


def _self_test():
    """Import the frozen GUI stack and check bundled V3/V2 data without opening a window."""
    import customtkinter  # noqa: F401
    import numpy  # noqa: F401
    import gui  # noqa: F401
    import u2v  # noqa: F401
    import v3v2.convert  # noqa: F401
    import v3v2.install  # noqa: F401

    root = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    required = (
        os.path.join(root, 'v3v2', 'idtables.json'),
        os.path.join(root, 'v3v2', 'v2_japanese_units.json'),
    )
    missing = [path for path in required if not os.path.isfile(path)]
    if missing:
        raise RuntimeError('Missing bundled application data: ' + ', '.join(missing))
    return 0


def main():
    from gui import main as gui_main
    gui_main()


if __name__ == '__main__':
    if '--self-test' in sys.argv[1:]:
        raise SystemExit(_self_test())
    main()
