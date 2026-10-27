"""Headless tests for profile_extract (run with freecadcmd)."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "LatheCAM"))

import Part
from FreeCAD import Vector

from LatheCAM import profile_extract as px

FAILS = []


def check(name, ok, extra=""):
    print(f"{'PASS' if ok else 'FAIL'}: {name}{'  ' + str(extra) if extra and not ok else ''}")
    if not ok:
        FAILS.append(name)


def make_part():
    """M10-like drum screw: bore 4.2, flange D11.75, shank D9.75,
    R0.5 fillet flange OD -> shoulder, length 15.16."""
    V = Vector
    e_face = Part.makeLine(V(2.1, 0, 0), V(5.875, 0, 0))
    e_od = Part.makeLine(V(5.875, 0, 0), V(5.875, 0, -1.75))
    m = 0.5 / math.sqrt(2.0)
    e_arc = Part.Arc(V(5.875, 0, -1.75), V(5.375 + m, 0, -1.75 + m),
                     V(5.375, 0, -1.25)).toShape()
    e_shoulder = Part.makeLine(V(5.375, 0, -1.25), V(4.875, 0, -1.25))
    e_shank = Part.makeLine(V(4.875, 0, -1.25), V(4.875, 0, -15.16))
    e_end = Part.makeLine(V(4.875, 0, -15.16), V(2.1, 0, -15.16))
    e_bore = Part.makeLine(V(2.1, 0, -15.16), V(2.1, 0, 0))
    wire = Part.Wire([e_face, e_od, e_arc, e_shoulder, e_shank, e_end, e_bore])
    face = Part.Face(wire)
    return face.revolve(V(0, 0, 0), V(0, 0, 1), 360)


EXPECTED_OUTER = ("11.75,0; 11.75,-1.75; 10.75,-1.25,-0.5; 9.75,-1.25; "
                  "9.75,-15.16; 4.2,-15.16")
EXPECTED_AUTO = ("11.75,0; 11.75,-1.75; 10.75,-1.25,-0.5; 9.75,-1.25; "
                 "9.75,-15.16")


def main():
    solid = make_part()

    # 1) faces: everything except the bore cylinder
    subs = [f"Face{i+1}" for i, f in enumerate(solid.Faces)
            if not (isinstance(f.Surface, Part.Cylinder)
                    and abs(f.Surface.Radius - 2.1) < 1e-9)]
    segs = px.segments(solid, subs)
    got = px.profile_from_segments(segs)
    check("F1 face selection -> outer profile", got == EXPECTED_OUTER, got)

    # 2) auto: whole solid, bore skipped, trailing radial trimmed
    got = px.auto_profile(solid)
    check("F2 auto silhouette", got == EXPECTED_AUTO, got)

    # 3) mixed: seam edges + the planar faces (full annuli have no radial
    # seam edge, so edge-only selection cannot close the profile)
    subs = [f"Face{i+1}" for i, f in enumerate(solid.Faces)
            if isinstance(f.Surface, Part.Plane)]
    for i, e in enumerate(solid.Edges):
        vs = e.Vertexes
        if all(abs(v.Point.y) < 1e-9 for v in vs) and len(vs) == 2:
            if isinstance(e.Curve, Part.Line) or (
                    isinstance(e.Curve, Part.Circle) and not e.isClosed()):
                subs.append(f"Edge{i+1}")
    segs = px.segments(solid, subs)
    got = px.profile_from_segments(segs)
    check("F3 mixed faces+edges -> closed profile",
          got == EXPECTED_OUTER + "; 4.2,0", got)

    # 4) convex quarter arc sign: (20,0)->(28,-4) centre (10,-4) -> R+4
    arc = Part.Arc(Vector(10, 0, 0),
                   Vector(12.82842712474619, 0, -1.17157287525381),
                   Vector(14, 0, -4)).toShape()
    got = px.profile_from_segments(px.segments_from_edge(arc))
    check("F4 convex arc R>0 (G02)", got == "20,0; 28,-4,4", got)

    # 5) gap detection in strict mode
    s1 = px.Seg((10.0, 0.0), (10.0, -5.0))
    s2 = px.Seg((12.0, -1.0), (12.0, -6.0))
    try:
        px.profile_from_segments([s1, s2])
        check("F5 gap rejected", False)
    except ValueError:
        check("F5 gap rejected", True)

    # 6) round-trip: extracted string parses with the engine's own parser
    from LatheCAM.model import Op
    pts = []
    for pair in EXPECTED_OUTER.split(";"):
        vals = [float(x) for x in pair.strip().split(",")]
        pts.append(tuple(vals))
    check("F6 string parses to 6 points", len(pts) == 6)

    print("PROFILE EXTRACT TEST PASSED" if not FAILS else f"FAILURES: {FAILS}")


main()
