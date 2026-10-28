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
from LatheCAM import objects
from LatheCAM.program import post_document, load_tools

check("tool library loads from json", len(load_tools()) == 6)

doc = FreeCAD.newDocument("LatheTest")
setup = objects.create_setup(doc)
setup.StockDiameter = 30.0
setup.StockLength = 60.0
setup.MaxRPM = 3000.0
check("setup has ToolChangePrompt", hasattr(setup, "ToolChangePrompt"))
check("setup has ToolLibraryFile", hasattr(setup, "ToolLibraryFile"))

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
check("G94 present (mm/min default)", any("G94" in l for l in lines))
check("O0100 header", any(l == "O0100" for l in lines[:5]), lines[0])
check("G32 threading", any(l.startswith("G32") for l in lines))
check("M30 end", lines[-1] == "M30")
check("op comments present", sum(1 for l in lines if l.startswith("(")) >= 6)

# error path: missing tool name must raise
bad = objects.create_op(doc, "od_rough")
objects._set_tool_enum(bad, "nonexistent")  # enum keeps unknown values
try:
    post_document(doc)
    check("bad tool raises", False)
except ValueError as e:
    check("bad tool raises", "nonexistent" in str(e))
doc.removeObject(bad.Name)  # keep doc postable for the vis test below

# ToolName dropdown: enumeration populated from the library
chk = objects.create_op(doc, "face")
check("ToolName is enumeration",
      chk.getTypeIdOfProperty("ToolName") == "App::PropertyEnumeration")
check("dropdown lists library tools",
      "OD-finish" in chk.getEnumerationsOfProperty("ToolName"))
# migration path: simulate an old string property and restore
chk.removeProperty("ToolName")
chk.addProperty("App::PropertyString", "ToolName", "Op")
chk.ToolName = "OD-finish"
chk.Proxy.onDocumentRestored(chk)
check("string ToolName migrates to enum",
      chk.getTypeIdOfProperty("ToolName") == "App::PropertyEnumeration"
      and chk.ToolName == "OD-finish")
doc.removeObject(chk.Name)

# ProfileString parsing (od_finish stepped profile, R0.4 corner fillet:
# chord 0.566 <= 2R so the arc validator accepts it)
prof_op = objects.create_op(doc, "od_finish")
prof_op.ProfileString = "15,0; 15,-25; 24.2,-25; 25,-25.4,0.4; 25,-45"
doc.recompute()
pv = prof_op.Proxy.to_op().profile
check("ProfileString parsed (D,Z[,R] triples)",
      pv == [(15.0, 0.0, 0.0), (15.0, -25.0, 0.0), (24.2, -25.0, 0.0),
             (25.0, -25.4, 0.4), (25.0, -45.0, 0.0)], str(pv))

# --- toolpath visualization (vis.py) ---
from LatheCAM import vis

vis_objs = vis.visualize(doc)
n_feat = len([o for o in vis_objs if o.TypeId == "Part::Feature"])
check("visualize creates objects", n_feat >= 2, str([o.Name for o in vis_objs]))
n_edges = sum(len(o.Shape.Edges) for o in vis_objs if o.Name != "Vis_stock")
check("toolpath edges built", n_edges > 20, f"{n_edges} edges")
stock_o = doc.getObject("Vis_stock")
check("stock cylinder shown", stock_o is not None and stock_o.Shape.isValid())

# per-op grouping (issue #7): one object per (op, kind) in a per-op subgroup
feed_objs = [o for o in vis_objs if o.TypeId == "Part::Feature"
             and o.Label.endswith("\u00b7 feed")]
check("per-op feed objects", len(feed_objs) >= 3,
      str([o.Label for o in feed_objs]))
check("feed objects belong to distinct ops",
      len({o.LatheOp for o in feed_objs}) == len(feed_objs))
subs = [o for o in doc.Objects if o.TypeId == "App::DocumentObjectGroup"
        and o.Name.startswith("Vis_Op_")]
check("per-op subgroups exist", len(subs) >= 3, str([s.Label for s in subs]))
check("features live in their op subgroup",
      all(any(g.TypeId == "App::DocumentObjectGroup" for g in o.InList)
          for o in feed_objs))
# grab a label BEFORE the rebuild below deletes these objects
one_feed = feed_objs[0].Label

vis.visualize(doc)  # re-run must replace, not duplicate
n_vis = len([o for o in doc.Objects if o.Name.startswith("Vis_")
             and o.TypeId == "Part::Feature"])
check("visualize re-run replaces", n_vis == n_feat, f"{n_vis} vs {n_feat}")

# hidden paths persist across rebuilds via Setup.HiddenVisPaths
setup.HiddenVisPaths = [one_feed]
vis.visualize(doc)
check("hidden paths persist on Setup",
      list(setup.HiddenVisPaths) == [one_feed], str(setup.HiddenVisPaths))

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
r_at = {round(z, 2): r for z, r, _b in profile}
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

# --- v0.3.2: sim understands ID vs OD (bore grows, outer untouched) ---
idoc = FreeCAD.newDocument("IdSimTest")
ist = objects.create_setup(idoc)
ist.StockDiameter = 18.0
ist.StockLength = 33.0
ist.BoreDiameter = 6.0
iop = objects.create_op(idoc, "id_finish")
iop.ProfileString = ("17.6,0; 16.1,-0.2; 16.1,-7; 13.914,-7.198;"
                     " 10,-20; 10,-22.1; 6,-22.3")
iop.ZEnd = -22.3
idoc.recompute()
i_stock, i_per_op = sim.motions_for_document(idoc)
i_prof = sim.finished_envelope(i_stock, i_per_op)
b_at = {round(z, 2): (r, b) for z, r, b in i_prof}
check("sim ID: outer untouched by boring",
      all(abs(r - 9.0) < 1e-6 for r, _b in b_at.values()), "")
check("sim ID: counterbore in bore profile",
      abs(b_at[-3.0][1] - 8.05) < 0.06, str(b_at[-3.0]))
check("sim ID: pre-drill untouched past cut",
      abs(b_at[-25.0][1] - 3.0) < 1e-6, str(b_at[-25.0]))
iobj = sim.simulate(idoc)
check("sim ID: result solid valid", iobj.Shape.isValid())


def _rb(z):
    segs = [(0.0, 8.8), (-0.2, 8.05), (-7.0, 8.05), (-7.198, 6.957),
            (-20.0, 5.0), (-22.1, 5.0), (-22.3, 3.0), (-33.0, 3.0)]
    if z >= 0:
        return 8.8
    for (z0, r0), (z1, r1) in zip(segs, segs[1:]):
        if z >= z1:
            return r0 + (z - z0) / (z1 - z0) * (r1 - r0)
    return 3.0


import math as _m
_vol = 0.0
_zz = 0.0
while _zz > -33.0:
    _r = _rb(_zz - 0.0025)
    _vol += _m.pi * (81.0 - _r * _r) * 0.005
    _zz -= 0.005
check("sim ID: tube volume matches bored cavity",
      abs(iobj.Shape.Volume - _vol) < 30, f"{iobj.Shape.Volume:.0f} vs {_vol:.0f}")

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
# a real machine table names tools after the inserts in the turret, so the
# placeholder names are gone: the default must fall back by tool kind
import json as _json
import tempfile as _tf
from LatheCAM import program as _prog
_upath = os.path.join(_tf.mkdtemp(), "tools.json")
_json.dump([
    {"name": "CCMT060208", "tool_number": 1, "kind": "turn", "width": 3.0,
     "nose_radius": 0.8, "max_depth": 20.0, "rpm": 2000.0, "feed": 0.05,
     "thread_pitch": 0.0, "z_reference": "centre"},
    {"name": "Thread 60", "tool_number": 3, "kind": "thread", "width": 3.0,
     "nose_radius": 0.4, "max_depth": 20.0, "rpm": 200.0, "feed": 0.05,
     "thread_pitch": 1.5, "z_reference": "centre"},
    {"name": "CCGT boring", "tool_number": 8, "kind": "bore", "width": 3.5,
     "nose_radius": 0.4, "max_depth": 25.0, "rpm": 2000.0, "feed": 0.025,
     "thread_pitch": 0.0, "z_reference": "centre"},
], open(_upath, "w"))
_orig_ulp = _prog.user_tool_library_path
_prog.user_tool_library_path = lambda: _upath
try:
    check("default tool falls back by kind (bore)",
          objects.default_tool_for("id_finish") == "CCGT boring",
          objects.default_tool_for("id_finish"))
    check("default tool falls back by kind (turn)",
          objects.default_tool_for("od_rough") == "CCMT060208",
          objects.default_tool_for("od_rough"))
    _bob = objects.create_op(_odoc, "id_finish")
    check("new op lands on a real library tool", _bob.ToolName == "CCGT boring",
          _bob.ToolName)
    check("ToolName enum follows the machine table",
          "CCGT boring" in _bob.getEnumerationsOfProperty("ToolName"),
          str(_bob.getEnumerationsOfProperty("ToolName")))
    _odoc.removeObject(_bob.Name)
finally:
    _prog.user_tool_library_path = _orig_ulp
# new properties + K4 default
_st = [o for o in _odoc.Objects if getattr(o, "LatheRole", "") == "setup"][0]
check("K4: tool change default on", bool(_st.ToolChange))
check("FaceDepth property exists", hasattr(a, "FaceDepth"))
check("ThreadLeadIn property exists", hasattr(c, "ThreadLeadIn"))
App_close(_odoc.Name)

# Ball-seat regression: a cone/sphere face selection must become a profile-
# following op. A cylinder-seeded id op silently bores a straight hole and
# leaves the countersink and ball seat untouched.
import Part
from LatheCAM import profile_extract

_bdoc = FreeCAD.newDocument("BallSeat")
objects.create_setup(_bdoc)
_outer = Part.makeCylinder(15.0, 25.0, FreeCAD.Vector(0, 0, -25.0))
_bore = Part.makeCylinder(5.0, 25.0, FreeCAD.Vector(0, 0, -25.0))
_funnel = Part.makeCone(5.0, 8.0, 5.196, FreeCAD.Vector(0, 0, -5.196))
_seat = Part.makeSphere(6.0, FreeCAD.Vector(0, 0, -18.0))
_solid = _outer.cut(_bore.fuse(_funnel).fuse(_seat))
_feat = _bdoc.addObject("Part::Feature", "BallSeat_part")
_feat.Shape = _solid
_bdoc.recompute()
_curved = [f"Face{i + 1}" for i, f in enumerate(_solid.Faces)
           if f.Surface.TypeId in ("Part::GeomCone", "Part::GeomSphere")]
_plain = [f"Face{i + 1}" for i, f in enumerate(_solid.Faces)
          if f.Surface.TypeId == "Part::GeomCylinder"
          and abs(f.Surface.Radius - 5.0) < 1e-6]
check("test solid has cone/sphere faces", bool(_curved),
      str([f.Surface.TypeId for f in _solid.Faces]))
check("cylinder-only selection needs no profile",
      not profile_extract.selection_needs_profile([(_feat, _plain)]),
      str(_plain))
check("cone/sphere selection needs a profile",
      profile_extract.selection_needs_profile([(_feat, _curved)]), str(_curved))
_bs = [o for o in _bdoc.Objects if getattr(o, "LatheRole", "") == "setup"][0]
_ptxt = profile_extract.profile_text([(_feat, _curved + _plain)], _bs)
check("ball-seat profile carries an arc",
      any(len(p.split(",")) > 2 for p in _ptxt.split(";")), _ptxt)
_op = objects.create_op(_bdoc, "id_finish")
_op.ProfileString = _ptxt
_op.ZEnd = profile_extract.profile_z_end(_ptxt)
check("profile ZEnd from text", abs(float(_op.ZEnd) + 25.0) < 1e-6,
      str(_op.ZEnd))
_bt = _bs
_bt.BoreDiameter = 3.0
_bnc = post_document(_bdoc)
check("ball seat posts an arc move", "G02" in _bnc or "G03" in _bnc,
      "\n".join(_bnc.splitlines()[-14:]))
check("ball seat arc keeps its radius", "R6." in _bnc or "R5." in _bnc)
App_close(_bdoc.Name)

# ------------------------------------------------- v0.3.21: SourceFace (issue #5)
_sfdoc = FreeCAD.newDocument("SourceFaceTest")
objects.create_setup(_sfdoc)
_scyl = _sfdoc.addObject("Part::Cylinder", "Cyl")
_scyl.Radius = 10.0
_scyl.Height = 40.0
_sop = objects.create_op(_sfdoc, "od_rough")
check("op has SourceFace property", "SourceFace" in _sop.PropertiesList)
check("empty SourceFace yields None", objects.source_faces(_sop) is None)
_sop.SourceFace = (_scyl, ["Face3"])
_src = objects.source_faces(_sop)
check("SourceFace roundtrip",
      _src is not None and _src[0].Name == "Cyl" and _src[1] == ["Face3"],
      str(_src))
_stmp = os.path.join(tempfile.gettempdir(), "lathecam_sourceface.FCStd")
_sfdoc.saveAs(_stmp)
App_close(_sfdoc.Name)  # saveAs renames the doc to the file stem: reopen
_srd = FreeCAD.openDocument(_stmp)  # would collide with it while still open
_sro = [o for o in _srd.Objects if getattr(o, "LatheRole", "") == "op"][0]
_ssrc = objects.source_faces(_sro)
check("SourceFace survives save/reopen",
      _ssrc is not None and _ssrc[0].Name == "Cyl" and _ssrc[1] == ["Face3"],
      str(_ssrc))
App_close(_srd.Name)
os.remove(_stmp)
_sfdoc2 = FreeCAD.newDocument("SourceFaceGone")
objects.create_setup(_sfdoc2)
_c2 = _sfdoc2.addObject("Part::Cylinder", "Cyl2")
_c2.Radius = 5.0
_c2.Height = 10.0
_o2 = _sfdoc2.addObject("App::FeaturePython", "OpGone")
from LatheCAM.objects import OpProxy
OpProxy(_o2, "od_finish")
_o2.SourceFace = (_c2, ["Face3"])
_sfdoc2.removeObject(_c2.Name)
check("deleted source object degrades to None",
      objects.source_faces(_o2) is None)
App_close(_sfdoc2.Name)

print(f"\n{len(FAILED)} failed" if FAILED else "\nHEADLESS TEST PASSED", flush=True)
sys.exit(1 if FAILED else 0)
