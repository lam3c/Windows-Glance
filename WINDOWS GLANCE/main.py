"""Windows Glance application entry point."""

import os
import sys

# Make sure "core" and "ui" can always be imported, whatever the
# working directory is (for example when started at Windows login).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.engine import main

if __name__ == "__main__":
    main()