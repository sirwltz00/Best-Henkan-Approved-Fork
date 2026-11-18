"""Console worker for Henkan FE's frozen GUI application.

The GUI launches this helper with redirected stdout/stderr so task output appears in its
activity console. It is not intended to be launched directly by users.
"""
import sys


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("HenkanFEWorker is an internal helper. Launch HenkanFE.exe instead.")
        return 2

    mode, *args = argv
    if mode == "--cli":
        import henkan
        sys.argv = ["henkan.py", *args]
        return henkan.main()
    if mode == "--qa":
        from tools.qa import main as qa_main
        return qa_main(args)

    print("Unknown worker mode: %s" % mode)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
