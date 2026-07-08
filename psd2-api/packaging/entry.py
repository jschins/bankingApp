"""PyInstaller entry point for a family member's psd2-api executable.

Importing the installed package (rather than running ``__main__`` as a loose
script) keeps the package's relative imports working inside the frozen build.
With no command-line arguments the app runs the guided re-authorization flow.
"""
import sys

from psd2_api.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
