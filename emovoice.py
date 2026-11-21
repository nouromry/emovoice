"""Launcher so `python emovoice.py [--text|--voice|--name NAME]` keeps working.

The application lives in the emovoice/ package; see README.md.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from emovoice.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
