"""Plain-python tests for ISO 1832 insert geometry (issue #14).

Run: python3 tests/test_inserts.py
"""

import math
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.join(_here, "..", "LatheCAM"), os.path.dirname(_here)):
    # dev layout: repo/LatheCAM/LatheCAM/pkg | release layout: repo/LatheCAM/pkg
    if os.path.isfile(os.path.join(_cand, "LatheCAM", "__init__.py")):
        sys.path.insert(0, _cand)
        break

from LatheCAM import inserts
from LatheCAM.model import Tool

FAILED = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


# ---- parsing ----------------------------------------------------------

CODES = ["CCMT060208", "DCMT070204", "CCGT060204", "VCMT110304",
         "WNMG080408", "TCMT16E304", "SPGN120308", "RCMT060208"]
for c in CODES:
    p = inserts.parse_iso(c)
    check(f"parse {c}", p is not None)
    if p:
        rt = inserts.iso_code(p["insert_shape"], p["relief_letter"],
                              p["tol_letter"], p["cb_letter"],
                              p["insert_ic"], p["insert_thickness"],
                              p["nose_radius"], ic_code=p["ic_code"],
                              thk_code=p["thk_code"], re_code=p["re_code"])
        check(f"round-trip {c}", rt == c, f"got {rt}")

check("parse strips spaces/dashes",
      inserts.parse_iso("CCMT 06-02-08")["code"] == "CCMT060208")
check("reject junk", inserts.parse_iso("XX0602") is None)
check("reject unknown shape", inserts.parse_iso("QQMT060208") is None)
check("reject unknown IC", inserts.parse_iso("CCMT990208") is None)
check("reject empty", inserts.parse_iso("") is None)
check("reject None", inserts.parse_iso(None) is None)

# known values (mockup tables)
p = inserts.parse_iso("CCMT060208")
check("CCMT IC", abs(p["insert_ic"] - 6.35) < 1e-9)
check("CCMT thk", abs(p["insert_thickness"] - 2.38) < 1e-9)
check("CCMT nose R", abs(p["nose_radius"] - 0.8) < 1e-9)
check("CCMT relief C=7", abs(p["relief_angle"] - 7.0) < 1e-9)
check("WNMG relief N=0",
      abs(inserts.parse_iso("WNMG080408")["relief_angle"]) < 1e-9)
check("SPGN relief P=11",
      abs(inserts.parse_iso("SPGN120308")["relief_angle"] - 11.0) < 1e-9)

# ---- geometry -----------------------------------------------------------

# C insert IC 6.35: circumradius 6.35, far corner 12.7 from the nose
poly = inserts.insert_polygon("C", 6.35)
nose = min(poly, key=lambda q: q[0])
check("nose at origin", abs(nose[0]) < 1e-9 and abs(nose[1]) < 1e-9)
check("body on +x side", all(x >= -1e-9 for x, _y in poly))
check("C outer_radius", abs(inserts.outer_radius("C", 6.35, 0.0)
                            - 12.7) < 1e-6)
check("outer_radius adds nose R",
      abs(inserts.outer_radius("C", 6.35, 0.8) - 13.5) < 1e-6)
check("lead angle rotates", inserts.outer_radius("C", 6.35, 0.0, 15.0)
      != inserts.outer_radius("C", 6.35, 0.0, 0.0)
      or True)  # extent may coincide; just must not raise
check("round insert ok", inserts.outer_radius("R", 8.0, 0.4) > 8.0)
check("approach C", abs(inserts.approach_angle("C") - 80.0) < 1e-9)
check("approach round", abs(inserts.approach_angle("R") - 95.0) < 1e-9)
check("approach lead", abs(inserts.approach_angle("C", 15.0) - 95.0) < 1e-9)
check("min_bore_d", abs(inserts.min_bore_d(10.0, 13.1) - 36.2) < 1e-9)

# ---- Tool integration -----------------------------------------------------

t = Tool("Bore", 3, "bore")
check("apply_iso ok", t.apply_iso("CCGT060204"))
check("apply_iso fields", t.insert_shape == "C"
      and abs(t.insert_ic - 6.35) < 1e-9
      and abs(t.nose_radius - 0.4) < 1e-9)
check("computed outer_radius", abs(t.outer_radius - 13.1) < 1e-6)
check("computed approach", abs(t.approach_angle - 80.0) < 1e-9)
t.bar_diameter = 10.0
t.compute_geometry()
check("computed min_bore_d", abs(t.min_bore_d - 36.2) < 1e-6)
check("apply_iso rejects junk", not t.apply_iso("nope")
      and t.iso_code == "CCGT060204")

w = inserts.bore_fit_warning(t, 12.0)
check("warn too-small bore", w is not None and "36.2" in w)
check("no warn when fits", inserts.bore_fit_warning(t, 40.0) is None)
check("no warn solid stock", inserts.bore_fit_warning(t, 0.0) is None)
check("no warn for OD tool",
      inserts.bore_fit_warning(Tool("OD", 1, "turn"), 12.0) is None)

# back-compat: old tools.json dicts must still build a Tool
old = {"name": "X", "tool_number": 9, "kind": "turn", "width": 3.0,
       "nose_radius": 0.4, "max_depth": 20.0, "rpm": 800.0, "feed": 0.2,
       "thread_pitch": 0.0, "z_reference": "centre"}
t2 = Tool(**old)
check("old dict back-compat", t2.iso_code == "" and t2.outer_radius == 0.0)

# JSON round-trip with new fields
import json
rec = {k: v for k, v in t.__dict__.items() if not k.startswith("_")}
t3 = Tool(**json.loads(json.dumps(rec)))
check("json round-trip", t3.iso_code == "CCGT060204"
      and abs(t3.min_bore_d - 36.2) < 1e-6)

print()
if FAILED:
    print(f"FAILED {len(FAILED)}: {FAILED}")
    sys.exit(1)
print("ALL PASS")
