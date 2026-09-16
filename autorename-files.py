#!/usr/bin/env python3
"""Compatibility shim for older local autorename aliases.

Use ``autorename.py`` as the canonical dispatcher. This file remains only while
existing dotfiles/installations migrate their invocation path.
"""
from autorename import main


if __name__ == "__main__":
    raise SystemExit(main())
