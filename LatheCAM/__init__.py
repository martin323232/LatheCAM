"""LatheCAM — turning CAM for FreeCAD 1.x with Masso G3 post-processor.

Pure-Python addon: no LibLathe/C++ dependency, no legacy Path API.
Works headless (freecadcmd) and in GUI.

Coordinate conventions (lathe):
  X = DIAMETER in mm, X0 = spindle centreline
  Z = 0 at the stock face, negative into the stock (towards the chuck)
"""

__version__ = "0.1.8"
__author__ = "maker (OpenClaw) for martin323232"
