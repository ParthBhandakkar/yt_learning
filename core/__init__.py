"""
Core strategy analysis framework.
"""

from .core import *
from .core import _parse_timestamp

# Backward-compatible public alias used by older dashboard/data-library code.
parse_timestamp = _parse_timestamp

from .fast_core import *
