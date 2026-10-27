"""Second shape battery: harder OD and ID geometry for profile_extract.

Runs under freecadcmd (strix) and inside the user's live FreeCAD via RPC.
Prints PASS/FAIL lines and a final marker: SHAPES2 TEST PASSED
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "LatheCAM"))

import Part
from FreeCAD import Vector as V

from LatheCAM import profile_extract as px
from LatheCAM.model import Op, Stock, Tool
from LatheCAM import paths, post

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


inner = lambda lim: (lambda f: f.BoundBox.XMax < lim)

# --- T1 four-step shaft (OD) ------------------------------------------------
t1 = cyl(12, 0, -6).fuse(cyl(9, -6, -14)).fuse(cyl(6, -14, -20)).fuse(cyl(4, -20, -26))
check("T1 four-step shaft",
      auto(t1) == "24,0; 24,-6; 18,-6; 18,-14; 12,-14; 12,-20; 8,-20; 8,-26",
      auto(t1))

# --- T2 undercut groove + face chamfer (OD) ---------------------------------
_b = cyl(8, 0, -30)
t2 = _b.cut(cyl(9, -12, -14).cut(cyl(5, -12, -14)))
t2 = t2.makeChamfer(1.5, [circle_edge(t2, 8.0, 0.0)])
check("T2 undercut groove + chamfer",
      auto(t2) == "13,0; 16,-1.5; 16,-12; 10,-12; 10,-14; 16,-14; 16,-30",
      auto(t2))

# --- T3 taper into a step (OD) ---------------------------------------------
t3 = Part.makeCone(10.0, 7.0, 12.0, V(0, 0, 0), V(0, 0, -1)).fuse(cyl(7, -12, -22))
check("T3 taper then step", auto(t3) == "20,0; 14,-12; 14,-22", auto(t3))

# --- T4 double spigot / pin (OD, radius goes back up) ----------------------
t4 = cyl(5, 0, -8).fuse(cyl(3, -8, -16)).fuse(cyl(5, -16, -20))
check("T4 spigot (re-expanded diameter)",
      auto(t4) == "10,0; 10,-8; 6,-8; 6,-16; 10,-16; 10,-20", auto(t4))

# --- T5 flanged hub with through bore: auto must stay outside ---------------
t5 = cyl(9, 0, -10).fuse(cyl(15, -10, -14)).cut(cyl(4, 2, -16))
check("T5 flanged hub auto = outer only",
      auto(t5) == "18,0; 18,-10; 30,-10; 30,-14", auto(t5))

# --- T6 blind bore with bottom fillet R2 (ID) ------------------------------
b6 = cyl(15, 0, -30).cut(cyl(6, 2, -20))
b6 = b6.makeFillet(2.0, [circle_edge(b6, 6.0, -20.0)])
got6 = faces_where(b6, inner(12.0))
check("T6 blind bore + bottom fillet",
      got6 == "12,0; 12,-18; 8,-20,2", got6)

# --- T7 internal retaining-ring groove (ID) --------------------------------
b7 = cyl(15, 0, -30).cut(cyl(8, 2, -32)).cut(cyl(11, -10, -12))
got7 = faces_where(b7, inner(12.0))
check("T7 internal groove",
      got7 == "16,0; 16,-10; 22,-10; 22,-12; 16,-12; 16,-30", got7)

# --- T8 countersink into through bore (ID, cone) ---------------------------
b8 = cyl(15, 0, -25).cut(Part.makeCone(10.0, 6.0, 5.0, V(0, 0, 0), V(0, 0, -1))) \
        .cut(cyl(6, 0, -27))
got8 = faces_where(b8, inner(12.0))
check("T8 countersink + bore", got8 == "20,0; 12,-5; 12,-25", got8)

# --- T9 bore with two counterbores (ID, multi-step) ------------------------
b9 = cyl(16, 0, -34).cut(cyl(6, 2, -36)).cut(cyl(9, 0, -8)).cut(cyl(12, 0, -4))
got9 = faces_where(b9, inner(13.0))
check("T9 two counterbores",
      got9 == "24,0; 24,-4; 18,-4; 18,-8; 12,-8; 12,-34", got9)

# --- pipeline: T1 auto -> od_rough -> post ---------------------------------
rt = Tool("rough", 1, "turn", rpm=800, feed=0.2)
prof1 = [tuple(float(x) for x in p.split(",")) for p in auto(t1).split(";")]
op1 = Op("od_rough", "rough", "t1", z_start=0.0, z_end=-26.0,
         target_diameter=8.0, profile=prof1, depth_per_pass=2.0,
         stock_to_leave=0.3)
try:
    mv1 = paths.generate(op1, rt, Stock(diameter=24.0, length=26.0))
    g1 = post.post_program([post.Block("t1", 1, 900.0, mv1)],
                           feed_mode="mm_per_min")
    cuts = [m for m in mv1 if m.kind == "feed"]
    check("P4 T1 od_rough -> post", len(cuts) > 8 and "Z-26." in g1, len(cuts))
except Exception as e:
    check("P4 T1 od_rough -> post", False, repr(e))

# --- pipeline: T7 ID -> id_rough -> post -----------------------------------
bt = Tool("bor", 3, "bore", width=4.0, max_depth=25.0, rpm=700, feed=0.12)
prof7 = [tuple(float(x) for x in p.split(",")) for p in got7.split(";")]
op7 = Op("id_rough", "bor", "t7", z_start=0.0, z_end=-30.0,
         target_diameter=22.0, profile=prof7, depth_per_pass=2.0,
         stock_to_leave=0.3)
try:
    mv7 = paths.generate(op7, bt, Stock(diameter=30.0, length=30.0,
                                        bore_diameter=16.0))
    g7 = post.post_program([post.Block("t7", 3, 700.0, mv7)],
                           feed_mode="mm_per_min")
    cuts = [m for m in mv7 if m.kind == "feed"]
    check("P5 T7 id_rough -> post", len(cuts) > 4, len(cuts))
except Exception as e:
    check("P5 T7 id_rough -> post", False, repr(e))

# --- pipeline: T6 blind bore -> id_finish -> post --------------------------
prof6 = [tuple(float(x) for x in p.split(",")) for p in got6.split(";")]
op6 = Op("id_finish", "bor", "t6", z_start=0.0, z_end=-20.0,
         target_diameter=12.0, profile=prof6)
try:
    mv6 = paths.generate(op6, bt, Stock(diameter=30.0, length=30.0,
                                        bore_diameter=12.0))
    g6 = post.post_program([post.Block("t6", 3, 700.0, mv6)],
                           feed_mode="mm_per_min")
    check("P6 T6 id_finish blind bore posts arc",
          any(m.kind in ("cw", "ccw") for m in mv6),
          [l for l in g6.splitlines() if "R" in l and "G0" in l][:3])
except Exception as e:
    check("P6 T6 id_finish blind bore posts arc", False, repr(e))

print("SHAPES2 TEST PASSED" if not FAILS else f"SHAPES2 FAILURES: {FAILS}")
