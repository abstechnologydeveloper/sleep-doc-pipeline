#!/usr/bin/env python3
"""Compatibility entry point for scene image generation."""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from pipeline.images import main


if __name__ == "__main__":
    main()
