#!/usr/bin/env python3
"""Source-tree entry point; set PYTHONPATH to the plugin root when packaging."""

from oprunway.cli import main


if __name__ == "__main__":
    raise SystemExit(main())

