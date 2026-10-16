"""Build three ready-to-use example parts, each as FCStd + posted NC.

Run from the repo root:  freecadcmd examples/build_examples.py

Each example document contains:
  Setup (stock + machine), ordered Ops, Part_reference (finished solid),
  Vis_* toolpath objects and Sim_part (material-removal simulation),
so opening the file in FreeCAD shows everything immediately.

Parts (all mm, lathe orientation: Z = spindle axis, Z0 = stock face):
  spindle_shaft  D30 x 62 bar -> M16x2 journal, relief groove, D24 flange,
                 chamfered, parted at Z-56. Exercises: face, 2x rough,
                 profile finish, groove, thread, part.
  bushing        D30 x 32 bar, pre-drilled D12 -> OD D25 x 25, bore D16
                 through, front chamfer, parted at Z-26. Exercises boring
                 (cuts toward the chuck, no diagonal feeds).
  v_pulley       D52 x 24 bar, pre-drilled D12 -> OD D50, 10 mm wide belt
                 groove to D40, bore D19, parted at Z-21. Exercises the
                 wide multi-centre groove.
"""

import os
import sys

import FreeCAD
import Part
from FreeCAD import Vector

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "LatheCAM"))
from LatheCAM import objects, vis, sim          # noqa: E402
from LatheCAM.program import post_document      # noqa: E402

OUT = os.path.join(HERE, "parts")
os.makedirs(OUT, exist_ok=True)


def revolve_profile(points):
    """Closed (r, z) polyline in the XZ plane revolved 360 deg about Z."""
    pts = [Vector(r, 0.0, z) for r, z in points]
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    face = Part.Face(Part.makePolygon(pts))
    return face.revolve(Vector(0, 0, 0), Vector(0, 0, 1), 360)


def build(name, stock, ops, reference_points, extra_setup=None):
    doc = FreeCAD.newDocument(name)
    doc.Label = name
    setup = objects.create_setup(doc)
    setup.StockDiameter = stock[0]
    setup.StockLength = stock[1]
    setup.BoreDiameter = stock[2]
    setup.MaxRPM = 3000.0
    setup.ToolChange = True
    if extra_setup:
        for k, v in extra_setup.items():
            setattr(setup, k, v)

    for order, (op_type, tool, props) in enumerate(ops, start=10):
        o = objects.create_op(doc, op_type)
        o.Order = order
        if tool:
            o.ToolName = tool
        for k, v in props.items():
            setattr(o, k, v)

    doc.recompute()
    nc_path = os.path.join(OUT, name + ".nc")
    gcode = post_document(doc, nc_path)          # raises on rapid collision

    ref = doc.addObject("Part::Feature", "Part_reference")
    ref.Shape = revolve_profile(reference_points)
    ref.addProperty("App::PropertyString", "LatheRole", "Lathe").LatheRole = "reference"
    doc.recompute()

    vis.visualize(doc)
    sim.simulate(doc)
    doc.recompute()
    doc.saveAs(os.path.join(OUT, name + ".FCStd"))
    print(f"{name}: {len(gcode.splitlines())} NC lines, "
          f"{len(doc.Objects)} objects -> saved", flush=True)


# ---------------------------------------------------------------- 1. shaft
build(
    "spindle_shaft",
    stock=(30.0, 62.0, 0.0),
    ops=[
        ("face", "OD-finish", dict(FaceDiameter=30.0, DepthPerPass=1.5,
                                   Comment="Face to length")),
        ("od_rough", "OD-rough", dict(ZEnd=-55.0, TargetDiameter=24.0,
                                      DepthPerPass=2.0, StockToLeave=0.4,
                                      Comment="Rough flange OD")),
        ("od_rough", "OD-rough", dict(ZEnd=-35.0, TargetDiameter=16.4,
                                      DepthPerPass=4.0, StockToLeave=0.4,
                                      Comment="Rough M16 journal")),
        ("od_finish", "OD-finish", dict(
            ZEnd=-55.0,
            ProfileString="13,0; 16,-1.5; 16,-35; 24,-35; 24,-55",
            Comment="Finish profile: chamfer, journal, flange")),
        ("groove", "Groove 3mm", dict(ZStart=-33.5, ZEnd=-33.5,
                                      GrooveDiameter=13.0, Peck=0.5,
                                      Comment="Thread relief groove 3 x D13")),
        ("thread", "Thread 60deg", dict(ThreadMajorDia=16.0, ThreadPitch=2.0,
                                        ThreadLength=28.0, ThreadZLimit=-32.0,
                                        Comment="M16x2 external thread")),
        ("part", "Part 2mm", dict(ZStart=-56.0, Peck=0.5,
                                  Comment="Part off, face at Z-55")),
    ],
    reference_points=[(0, 0), (13, 0), (16, -1.5), (16, -32), (13, -32),
                      (13, -35), (24, -35), (24, -55), (0, -55)],
)

# -------------------------------------------------------------- 2. bushing
build(
    "bushing",
    stock=(30.0, 32.0, 12.0),
    ops=[
        ("face", "OD-finish", dict(FaceDiameter=30.0, DepthPerPass=1.5,
                                   Comment="Face to length")),
        ("od_rough", "OD-rough", dict(ZEnd=-25.0, TargetDiameter=25.0,
                                      DepthPerPass=2.0, StockToLeave=0.4,
                                      Comment="Rough OD to D25")),
        ("od_finish", "OD-finish", dict(
            ZEnd=-25.0, ProfileString="23,0; 25,-1; 25,-25",
            Comment="Finish OD with 1 mm front chamfer")),
        ("id_rough", "Bore", dict(ZEnd=-25.0, TargetDiameter=16.0,
                                  DepthPerPass=1.5, StockToLeave=0.3,
                                  Comment="Rough bore from D12 pilot")),
        ("id_finish", "Bore", dict(ZEnd=-25.0, TargetDiameter=16.0,
                                   Comment="Finish bore to D16 H7")),
        ("part", "Part 2mm", dict(ZStart=-26.0, Peck=0.5,
                                  Comment="Part off, face at Z-25")),
    ],
    reference_points=[(8, 0), (23, 0), (25, -1), (25, -25), (8, -25)],
)

# ------------------------------------------------------------- 3. v-pulley
build(
    "v_pulley",
    stock=(52.0, 24.0, 12.0),
    ops=[
        ("face", "OD-finish", dict(FaceDiameter=52.0, DepthPerPass=1.5,
                                   Comment="Face to length")),
        ("od_rough", "OD-rough", dict(ZEnd=-20.0, TargetDiameter=50.0,
                                      DepthPerPass=1.0, StockToLeave=0.4,
                                      Comment="Rough OD to D50")),
        ("od_finish", "OD-finish", dict(
            ZEnd=-20.0, ProfileString="48,0; 50,-1; 50,-20",
            Comment="Finish OD with 1 mm front chamfer")),
        ("id_rough", "Bore", dict(ZEnd=-20.0, TargetDiameter=19.0,
                                  DepthPerPass=1.5, StockToLeave=0.4,
                                  Comment="Rough bore from D12 pilot")),
        ("id_finish", "Bore", dict(ZEnd=-20.0, TargetDiameter=19.0,
                                   Comment="Finish bore to D19 (motor shaft)")),
        ("groove", "Groove 3mm", dict(ZStart=-5.0, ZEnd=-15.0,
                                      GrooveDiameter=40.0, Peck=1.0,
                                      Comment="10 mm wide V-belt groove to D40")),
        ("part", "Part 2mm", dict(ZStart=-21.0, Peck=0.5,
                                  Comment="Part off, face at Z-20")),
    ],
    reference_points=[(9.5, 0), (48, 0), (50, -1), (50, -5), (40, -5),
                      (40, -15), (50, -15), (50, -20), (9.5, -20)],
)

print("ALL EXAMPLES BUILT", flush=True)
