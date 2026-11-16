"""Expose the already-installed system pytest to the frozen DELTAR Python.

The directory containing this file is added only for test commands. Appending keeps
the DELTAR environment's NumPy/PyTorch/Pillow ahead of the system environment.
"""

from pathlib import Path
import sys

SYSTEM_SITE_PACKAGES = Path(r"C:\ProgramData\anaconda3\Lib\site-packages")
if SYSTEM_SITE_PACKAGES.is_dir() and str(SYSTEM_SITE_PACKAGES) not in sys.path:
    sys.path.append(str(SYSTEM_SITE_PACKAGES))
