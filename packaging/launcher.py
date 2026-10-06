"""Entry point used by PyInstaller for the frozen application."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from manymaze.gui.app import main

    sys.exit(main())
