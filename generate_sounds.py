#!/usr/bin/env python3
"""Compatibility entry point for optional story sound generation."""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from pipeline.sound import main


if __name__ == "__main__":
    main()
