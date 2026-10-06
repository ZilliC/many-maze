import sys

from .cli import main

if __name__ == "__main__":  # guard: worker processes (multiprocessing spawn) re-import the main module
    sys.exit(main())
