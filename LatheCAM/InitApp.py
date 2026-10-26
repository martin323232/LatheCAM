"""LatheCAM console-mode init (freecadcmd / batch).

Makes the LatheCAM package importable and registers the object proxies.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

import LatheCAM.objects  # noqa: F401  (ensures proxies import cleanly)
