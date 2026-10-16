"""Demo: build a full lathe program headless and post it.

Run: freecadcmd examples/demo_program.py
"""

import os
import sys

import FreeCAD

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "LatheCAM"))
from LatheCAM import objects          # noqa: E402
from LatheCAM.program import post_document  # noqa: E402

doc = FreeCAD.newDocument("Demo")

setup = objects.create_setup(doc)
setup.StockDiameter = 32.0
setup.StockLength = 70.0
setup.MaxRPM = 3000.0

steps = [
    ("face", "OD-rough", dict(Order=10, FaceDiameter=32.0, DepthPerPass=1.5,
                              Comment="Face to length")),
    ("od_rough", "OD-rough", dict(Order=20, ZEnd=-50.0, TargetDiameter=20.0,
                                  DepthPerPass=2.0, StockToLeave=0.4,
                                  Comment="Rough OD to 20")),
    ("od_finish", "OD-finish", dict(Order=30, ZEnd=-50.0, TargetDiameter=20.0,
                                    Comment="Finish OD 20")),
    ("groove", "Groove 3mm", dict(Order=40, ZStart=-30.0, ZEnd=-30.0,
                                  GrooveDiameter=16.0, Peck=0.5,
                                  Comment="Relief groove 3x2 at Z-30")),
    ("thread", "Thread 60deg", dict(Order=50, ThreadMajorDia=20.0,
                                    ThreadPitch=2.0, ThreadLength=20.0,
                                    Comment="M20x2 external thread")),
    ("part", "Part 2mm", dict(Order=60, ZStart=-55.0, Peck=0.5,
                              Comment="Part off at Z-55")),
]
for op_type, tool, props in steps:
    o = objects.create_op(doc, op_type)
    o.ToolName = tool
    for k, v in props.items():
        setattr(o, k, v)

doc.recompute()
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo.nc")
gcode = post_document(doc, out)
print(f"Wrote {len(gcode.splitlines())} lines to {out}", flush=True)
