"""
Pytest configuration and shared fixtures for DocRefinery tests.
Ensures project root is on sys.path when running tests from any working directory.
"""

from pathlib import Path
import sys

# Add project root so "src" imports work when running pytest from repo root or elsewhere
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
