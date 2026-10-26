"""Faithful simulation of FreeCAD 1.1 InitGui.py execution + command use.

Reproduces three things the real GUI does, without a display:
  1. InitGui.py exec'd in function scope (FreeCADGuiInit.RunInitGuiPy)
     — module-level names must not be needed by Initialize().
  2. Gui.addCommand receiving an object and immediately calling
     GetResources() on it (the real C++ registry does this; passing a
     CLASS instead of an instance explodes here, as in the real GUI).
  3. Command Activated() against a real FreeCAD document.

Exit 0 = all good. Run with freecadcmd.
"""

import os
import sys
import types

fake_gui = types.ModuleType("FreeCADGui")


class Workbench:
    def appendToolbar(self, *a):
        pass

    def appendMenu(self, *a):
        pass


registered = {}


def _add_command(name, cmd):
    registered[name] = cmd
    # real FreeCAD calls GetResources() at registration time
    res = cmd.GetResources()
    assert isinstance(res, dict) and "MenuText" in res, f"{name}: bad resources"


fake_gui.Workbench = Workbench
fake_gui.addWorkbench = lambda wb: registered.setdefault("_wb", wb)
fake_gui.addCommand = _add_command
fake_gui.Selection = types.SimpleNamespace(
    clearSelection=lambda: None, addSelection=lambda o: None)
fake_gui.ActiveDocument = None
sys.modules["FreeCADGui"] = fake_gui

MOD = os.path.expanduser("~/.local/share/FreeCAD/v1-1/Mod/LatheCAM")
if MOD not in sys.path:
    sys.path.insert(0, MOD)

OPS = ("face", "od_rough", "od_finish", "id_rough", "id_finish",
       "groove", "part", "thread")

init_mod = types.ModuleType("FreeCADGuiInit")
harness = """
def RunInitGuiPy(path):
    with open(path, "rt", encoding="utf-8") as f:
        exec(compile(f.read(), path, "exec"))
"""
exec(compile(harness, "<FreeCADGuiInit>", "exec"), init_mod.__dict__)

import FreeCAD  # noqa: E402

try:
    init_mod.RunInitGuiPy(MOD + "/InitGui.py")
    wbs = [v for k, v in registered.items() if k == "_wb"]
    assert wbs, "workbench not registered"
    wbs[0].Initialize()
    expected = ["CmdLatheSetup"] + [f"CmdLatheOp_{t}" for t in OPS] + ["CmdLathePost"]
    missing = [n for n in expected if n not in registered]
    for extra in ("CmdLatheVis", "CmdLatheRegen", "CmdLatheSim", "CmdLatheSimChoose"):
        if extra not in registered:
            missing.append(extra)
    assert not missing, f"commands not registered: {missing}"
    print(f"REGISTER OK: {len(expected)} commands", flush=True)

    # activate each command against a real document (Setup first)
    doc = FreeCAD.newDocument("Sim")
    fake_gui.ActiveDocument = types.SimpleNamespace(Document=doc)
    for name in expected[:-1]:  # skip Post (needs dialog stub)
        cmd = registered[name]
        assert cmd.IsActive(), f"{name} inactive"
        cmd.Activated()
    n_setup = sum(1 for o in doc.Objects if getattr(o, "LatheRole", "") == "setup")
    n_ops = sum(1 for o in doc.Objects if getattr(o, "LatheRole", "") == "op")
    assert n_setup == 1 and n_ops == len(OPS), f"setup={n_setup} ops={n_ops}"
    print(f"ACTIVATE OK: {n_setup} setup + {n_ops} ops in document", flush=True)

    # post through the library API (dialog-free path)
    from LatheCAM.program import post_document
    setup = next(o for o in doc.Objects if getattr(o, "LatheRole", "") == "setup")
    setup.BoreDiameter = 16.0
    for o in doc.Objects:
        if getattr(o, "OpType", "") == "face":
            o.FaceDiameter = 30.0
    doc.recompute()
    out = "/tmp/sim_post.nc"
    txt = post_document(doc, out)
    assert "G21" in txt and "G32" in txt and "M30" in txt, "bad gcode"
    print(f"POST OK: {len(txt.splitlines())} lines", flush=True)
    # op-filtered simulation (v0.3.12): animate only some ops, but show the
    # material as it looks when those ops run (all ops up to the last chosen)
    from LatheCAM import sim as _sim
    ops_objs = [o for o in doc.Objects if getattr(o, "LatheRole", "") == "op"]
    by_type = {getattr(o, "OpType", ""): o for o in ops_objs}
    v_full = _sim.simulate(doc).Shape.Volume
    v_thr = _sim.simulate(doc, only=[by_type["thread"].Label]).Shape.Volume
    v_face = _sim.simulate(doc, only=[by_type["face"].Label]).Shape.Volume
    assert abs(v_thr - v_full) < 1e-6 * v_full, \
        f"thread-last filter {v_thr} != full {v_full}"
    assert v_face > v_full, f"face-only {v_face} should exceed full {v_full}"
    _sim.simulate(doc)  # back to the whole program
    print(f"FILTER OK: full={v_full:.0f} thread-only={v_thr:.0f} "
          f"face-only={v_face:.0f}", flush=True)

    # auto-redraw (v0.3.13): the document observer redraws when the program
    # changes, and does nothing when it has already caught up
    from LatheCAM import gui as _gui
    sig_before = _gui._AUTO["sig"]
    assert sig_before is not None, "observer never ran during recompute"
    by_type["face"].FaceDepth = 3.0
    doc.recompute()
    assert _gui._AUTO["sig"] != sig_before, "op edit did not redraw the paths"
    assert _gui.refresh_if_changed(doc) is False, "caught-up doc re-scheduled"
    _gui.AutoVisObserver().slotRecomputedDocument(doc)   # must not raise
    print("AUTOVIS OK", flush=True)

    print("SIM PASS", flush=True)
except Exception as e:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    print(f"SIM FAIL: {e}", flush=True)
    sys.exit(1)
