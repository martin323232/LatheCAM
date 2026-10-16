"""Headless integration test: run under freecadcmd.

Simulates an installed addon: Mod/LatheCAM on MOD_PATH, build a document
with Setup + Ops via the object proxies, post to file, verify G-code.
"""

import sys

import FreeCAD

FAILED = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f" — {detail}"), flush=True)
    if not cond:
        FAILED.append(name)


# import as an installed addon would
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from LatheCAM import objects
from LatheCAM.program import post_document, load_tools

check("tool library loads from json", len(load_tools()) == 6)

doc = FreeCAD.newDocument("LatheTest")
setup = objects.create_setup(doc)
setup.StockDiameter = 30.0
setup.StockLength = 60.0
setup.MaxRPM = 3000.0

f = objects.create_op(doc, "face")
f.Order = 10
f.FaceDiameter = 30.0
f.DepthPerPass = 1.5

r = objects.create_op(doc, "od_rough")
r.Order = 20
r.ZEnd = -40.0
r.TargetDiameter = 20.0
r.DepthPerPass = 2.0
r.StockToLeave = 0.4

fin = objects.create_op(doc, "od_finish")
fin.Order = 30
fin.ZEnd = -40.0
fin.TargetDiameter = 20.0
fin.ToolName = "OD-finish"

g = objects.create_op(doc, "groove")
g.Order = 40
g.ToolName = "Groove 3mm"
g.ZStart = -25.0
g.ZEnd = -25.0
g.GrooveDiameter = 16.0
g.Peck = 0.5

t = objects.create_op(doc, "thread")
t.Order = 50
t.ToolName = "Thread 60deg"
t.ThreadMajorDia = 20.0
t.ThreadPitch = 2.0
t.ThreadLength = 18.0

p = objects.create_op(doc, "part")
p.Order = 60
p.ToolName = "Part 2mm"
p.ZStart = -45.0
p.Peck = 0.5

doc.recompute()
check("setup role set", setup.LatheRole == "setup")
check("op role set", r.LatheRole == "op")

out = "/tmp/lathe_test.nc"
gcode = post_document(doc, out)
lines = gcode.splitlines()

check("gcode written", len(lines) > 50, f"{len(lines)} lines")
check("G21 present", any("G21" in l for l in lines))
check("G95 present", any("G95" in l for l in lines))
check("G32 threading", any(l.startswith("G32") for l in lines))
check("M30 end", lines[-1] == "M30")
check("op comments present", sum(1 for l in lines if l.startswith("(")) >= 6)

# error path: missing tool name must raise
bad = objects.create_op(doc, "od_rough")
bad.ToolName = "nonexistent"
try:
    post_document(doc)
    check("bad tool raises", False)
except ValueError as e:
    check("bad tool raises", "nonexistent" in str(e))
doc.removeObject(bad.Name)  # keep doc postable for the vis test below

# ProfileString parsing (od_finish stepped profile)
prof_op = objects.create_op(doc, "od_finish")
prof_op.ProfileString = "15,0; 15,-25; 25,-25; 25,-45"
doc.recompute()
pv = prof_op.Proxy.to_op().profile
check("ProfileString parsed", pv == [(15.0, 0.0), (15.0, -25.0),
      (25.0, -25.0), (25.0, -45.0)], str(pv))

# --- toolpath visualization (vis.py) ---
from LatheCAM import vis

vis_objs = vis.visualize(doc)
n_feat = len([o for o in vis_objs if o.TypeId == "Part::Feature"])
check("visualize creates objects", n_feat >= 2, str([o.Name for o in vis_objs]))
n_edges = sum(len(o.Shape.Edges) for o in vis_objs if o.Name != "Vis_stock")
check("toolpath edges built", n_edges > 20, f"{n_edges} edges")
stock_o = doc.getObject("Vis_stock")
check("stock cylinder shown", stock_o is not None and stock_o.Shape.isValid())
vis.visualize(doc)  # re-run must replace, not duplicate
n_vis = len([o for o in doc.Objects if o.Name.startswith("Vis_")
             and o.TypeId == "Part::Feature"])
check("visualize re-run replaces", n_vis == n_feat, f"{n_vis} vs {n_feat}")

# --- simulation (sim.py): finished part computed from toolpath ---
from LatheCAM import sim

sdoc = FreeCAD.newDocument("SimTest")
st = objects.create_setup(sdoc)
st.StockDiameter = 25.0
st.StockLength = 45.0
fc = objects.create_op(sdoc, "face")
fc.FaceDiameter = 25.0
rg = objects.create_op(sdoc, "od_rough")
rg.TargetDiameter = 15.0
rg.ZEnd = -25.0
rg.DepthPerPass = 2.0
rg.StockToLeave = 0.0
fn = objects.create_op(sdoc, "od_finish")
fn.TargetDiameter = 15.0
fn.ZEnd = -25.0
pt = objects.create_op(sdoc, "part")
pt.ZStart = -45.0
sdoc.recompute()

s_stock, s_per_op = sim.motions_for_document(sdoc)
profile = sim.finished_envelope(s_stock, s_per_op)
r_at = {round(z, 2): r for z, r in profile}
check("sim: thin section at r=7.5", abs(r_at[-5.0] - 7.5) < 0.06, str(r_at[-5.0]))
check("sim: shoulder region at r=12.5", abs(r_at[-35.0] - 12.5) < 0.06,
      str(r_at[-35.0]))
sim_obj = sim.simulate(sdoc)
vol_expected = 3.141592653589793 * (7.5 ** 2 * 25 + 12.5 ** 2 * 20)
check("sim: result solid valid", sim_obj.Shape.isValid())
check("sim: volume matches stepped shaft", abs(sim_obj.Shape.Volume - vol_expected) < 60,
      f"{sim_obj.Shape.Volume:.0f} vs {vol_expected:.0f}")
sim.simulate(sdoc)  # re-run must replace
check("sim: re-run replaces", len([o for o in sdoc.Objects
      if o.Name == "Sim_part"]) == 1)

# ---------------------------------------------------------------- v0.1.6 fixes
import tempfile
import os

# B1: proxy persistence across save/reopen
_tmp = os.path.join(tempfile.gettempdir(), "lathecam_persist.FCStd")
sdoc.saveAs(_tmp)
_rdoc = FreeCAD.openDocument(_tmp)
_ro = [o for o in _rdoc.Objects if getattr(o, "LatheRole", "") == "op"][0]
try:
    _ro.Proxy.to_op()
    check("B1: proxy usable after reopen", True)
except AttributeError as e:
    check("B1: proxy usable after reopen", False, str(e))
App_close = FreeCAD.closeDocument
App_close(_rdoc.Name)
os.remove(_tmp)

# K3/K5: create_op auto-order + per-type tool defaults
_odoc = FreeCAD.newDocument("OrderTest")
objects.create_setup(_odoc)
a = objects.create_op(_odoc, "face")
b = objects.create_op(_odoc, "od_rough")
c = objects.create_op(_odoc, "thread")
check("K3: orders auto-increment", (a.Order, b.Order, c.Order) == (10, 20, 30),
      f"{(a.Order, b.Order, c.Order)}")
check("K5: thread op gets thread tool", c.ToolName == "Thread 60deg", c.ToolName)
check("K5: face op gets finish tool", a.ToolName == "OD-finish", a.ToolName)
# new properties + K4 default
_st = [o for o in _odoc.Objects if getattr(o, "LatheRole", "") == "setup"][0]
check("K4: tool change default on", bool(_st.ToolChange))
check("FaceDepth property exists", hasattr(a, "FaceDepth"))
check("ThreadLeadIn property exists", hasattr(c, "ThreadLeadIn"))
App_close(_odoc.Name)

print(f"\n{len(FAILED)} failed" if FAILED else "\nHEADLESS TEST PASSED", flush=True)
sys.exit(1 if FAILED else 0)
