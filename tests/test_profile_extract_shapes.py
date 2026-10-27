"""Shape battery for profile_extract: OD, ID, fillets, tapers, grooves.

Runs under freecadcmd (strix) AND inside the user's live FreeCAD via RPC —
pure geometry, no document needed. Prints PASS/FAIL lines and a final
marker: SHAPES TEST PASSED
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "LatheCAM"))

import Part
from FreeCAD import Vector as V

from LatheCAM import profile_extract as px

FAILS = []


def check(name, ok, extra=""):
    print(f"{'PASS' if ok else 'FAIL'}: {name}{'  got=' + str(extra) if not ok else ''}")
    if not ok:
        FAILS.append(name)


def cyl(r, z0, z1):
    return Part.makeCylinder(r, z0 - z1, V(0, 0, z1), V(0, 0, 1))


def circle_edge(solid, r, z, tol=1e-6):
    for e in solid.Edges:
        c = e.Curve
        if (isinstance(c, Part.Circle) and abs(c.Radius - r) < tol
                and abs(c.Center.z - z) < tol and abs(c.Center.x) < tol):
            return e
    raise AssertionError(f"no circle edge r={r} z={z}")


def auto(shape):
    return px.auto_profile(shape)


def faces_where(shape, pred):
    subs = [f"Face{i+1}" for i, f in enumerate(shape.Faces) if pred(f)]
    return px.profile_from_segments(px.segments(shape, subs))


# --- S1 stepped shaft (OD, plain) ------------------------------------------
s1 = cyl(10, 0, -10).fuse(cyl(7, -10, -25))
check("S1 stepped shaft", auto(s1) == "20,0; 20,-10; 14,-10; 14,-25", auto(s1))

# --- S2 shaft with concave shoulder fillet R2 ------------------------------
s2 = s1.makeFillet(2.0, [circle_edge(s1, 7.0, -10.0)])
# tangent pts: (9,-10) on shoulder, (7,-12) on wall, centre (9,-12): CCW -> -R
check("S2 OD shoulder fillet R2 (CCW -> -R)",
      auto(s2) == "20,0; 20,-10; 18,-10; 14,-12,-2; 14,-25", auto(s2))

# --- S3 convex end round R3 -------------------------------------------------
_b3 = cyl(8, 0, -20)
s3 = _b3.makeFillet(3.0, [circle_edge(_b3, 8.0, 0.0)])
# tangent pts: (5,0) on face, (8,-3) on OD, centre (5,-3): CW -> +R
check("S3 OD end round R3 (CW -> +R)",
      auto(s3) == "10,0; 16,-3,3; 16,-20", auto(s3))

# --- S4 taper ---------------------------------------------------------------
s4 = Part.makeCone(10.0, 6.0, 20.0, V(0, 0, 0), V(0, 0, -1))
check("S4 taper 20->12", auto(s4) == "20,0; 12,-20", auto(s4))

# --- S5 bushing: OD 30, bore 16 through, counterbore 22 x 12 ----------------
bush = cyl(15, 0, -30).cut(cyl(8, 2, -32)).cut(cyl(11, 0, -12))
check("S5 auto = outer silhouette only", auto(bush) == "30,0; 30,-30", auto(bush))
id_faces = lambda f: (isinstance(f.Surface, (Part.Cylinder, Part.Toroid))
                      and f.BoundBox.XMax < 12.0) or \
                     (isinstance(f.Surface, Part.Plane)
                      and abs(abs(f.Surface.Axis.z) - 1) < 1e-6
                      and abs(f.BoundBox.ZMin + 12) < 1e-6)
check("S5 ID selection -> bore profile",
      faces_where(bush, id_faces) == "22,0; 22,-12; 16,-12; 16,-30",
      faces_where(bush, id_faces))

# --- S6 counterbore with internal fillet R1.5 -------------------------------
bush6 = bush.makeFillet(1.5, [circle_edge(bush, 11.0, -12.0)])
got6 = faces_where(bush6, id_faces)
check("S6 ID fillet R1.5",
      got6 == "22,0; 22,-10.5; 19,-12,1.5; 16,-12; 16,-30", got6)

# --- S7 grooved shaft: groove D14 x 3 at -8..-11 ----------------------------
ring = cyl(11, -8, -11).cut(cyl(7, -8, -11))
s7 = cyl(10, 0, -25).cut(ring)
check("S7 groove", auto(s7) == "20,0; 20,-8; 14,-8; 14,-11; 20,-11; 20,-25",
      auto(s7))

# --- S8 face chamfer 1.5 mm (like the M10) ----------------------------------
_b8 = cyl(10, 0, -20)
s8 = _b8.makeChamfer(1.5, [circle_edge(_b8, 10.0, 0.0)])
check("S8 face chamfer", auto(s8) == "17,0; 20,-1.5; 20,-20", auto(s8))

# --- pipeline: extracted OD profile -> od_finish -> post ---------------------
from LatheCAM.model import Op, Stock, Tool
from LatheCAM import paths, post

stock = Stock(diameter=20.0, length=25.0)
tool = Tool("fin", 2, "turn", rpm=1200, feed=0.1)
prof2 = [tuple(float(x) for x in p.split(",")) for p in
         auto(s2).split(";")]
op = Op("od_finish", "fin", "s2", z_start=0.0, z_end=-25.0,
        target_diameter=14.0, profile=prof2)
mv = paths.generate(op, tool, stock)
arcs = [m for m in mv if m.kind in ("cw", "ccw")]
g = post.post_program([post.Block("od", 2, 1200.0, mv)], feed_mode="mm_per_min")
# concave shoulder fillet travels CCW -> G03 (matches isolation_plastic ref)
check("P1 od_finish posts G03 R2", "G03" in g and "R2." in g,
      [l for l in g.splitlines() if "G0" in l and "R" in l])

# --- pipeline: extracted ID profile -> id_finish -> post ---------------------
stock5 = Stock(diameter=30.0, length=30.0, bore_diameter=16.0)
btool = Tool("bor", 3, "bore", width=4.0, max_depth=25.0, rpm=700, feed=0.12)
prof6 = [tuple(float(x) for x in p.split(",")) for p in
         faces_where(bush6, id_faces).split(";")]
op6 = Op("id_finish", "bor", "s6", z_start=0.0, z_end=-30.0,
         target_diameter=16.0, profile=prof6)
try:
    mv6 = paths.generate(op6, btool, stock5)
    g6 = post.post_program([post.Block("id", 3, 700.0, mv6)],
                           feed_mode="mm_per_min")
    ok6 = any(m.kind in ("cw", "ccw") for m in mv6)
    check("P2 id_finish follows bore profile with arc", ok6,
          [l for l in g6.splitlines() if "G0" in l and "R" in l][:3])
except Exception as e:
    check("P2 id_finish follows bore profile with arc", False, repr(e))

# --- pipeline: extracted groove profile -> od_rough --------------------------
op7 = Op("od_rough", "rough", "s7", z_start=0.0, z_end=-25.0,
         target_diameter=14.0,
         profile=[tuple(float(x) for x in p.split(","))
                  for p in auto(s7).split(";")],
         depth_per_pass=1.5, stock_to_leave=0.25)
try:
    rtool = Tool("rough", 1, "turn", rpm=800, feed=0.2)
    mv7 = paths.generate(op7, rtool, Stock(diameter=20.0, length=25.0))
    cuts = [m for m in mv7 if m.kind == "feed"]
    check("P3 od_rough handles groove profile", len(cuts) > 6, len(cuts))
except Exception as e:
    check("P3 od_rough handles groove profile", False, repr(e))

print("SHAPES TEST PASSED" if not FAILS else f"SHAPES FAILURES: {FAILS}")
