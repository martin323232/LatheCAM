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
    print("SIM PASS", flush=True)
except Exception as e:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    print(f"SIM FAIL: {e}", flush=True)
    sys.exit(1)
