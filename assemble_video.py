#!/usr/bin/env python3
"""Compatibility entry point for final video assembly."""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from pipeline.video import main


if __name__ == "__main__":
    main()
