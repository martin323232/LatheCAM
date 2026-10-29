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
# gui.py reads the selection through getSelectionEx() (selection seeding,
# ProfileString auto-fill); with nothing selected it must return [].
# addSelection/clearSelection signatures kept permissive to match the
# real FreeCADGui.Selection API (obj, subnames...).
fake_gui.Selection = types.SimpleNamespace(
    clearSelection=lambda *a, **k: None,
    addSelection=lambda *a, **k: None,
    getSelectionEx=lambda *a, **k: [])
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
    for extra in ("CmdLatheVis", "CmdLatheRegen", "CmdLatheSim",
                  "CmdLatheSimChoose", "CmdLatheSimPanel"):
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

    # seekable player (issue #8): pure position math, headless-safe —
    # the panel drives seek() from its slider, both directions
    from LatheCAM import sim as _sim3
    pl = _sim3.SimPlayer(doc, duration=10.0)
    assert pl.segments and pl.total > 0, "empty timeline"
    n_all = len(pl.segments)
    pl.seek(-5.0)
    assert pl.t == 0.0, "seek must clamp at 0"
    pl.seek(pl.total + 5.0)
    assert abs(pl.t - pl.total) < 1e-9, "seek must clamp at total"
    s_end, f_end = pl.segment_at(pl.t)
    assert f_end == 1.0 and s_end is pl.segments[-1], "end-of-program seek"
    pl.seek(pl.total / 2.0)
    s_mid, f_mid = pl.segment_at(pl.t)
    assert s_mid is not None and 0.0 <= f_mid <= 1.0, "mid seek"
    idx = [pl.segment_at(pl.total * k / 20.0)[0].op for k in range(21)]
    assert idx == sorted(idx), "segment_at must be monotone in t"
    face_label = by_type["face"].Label
    pl.set_ops([face_label])
    assert pl.op_labels == [face_label], "subset op list wrong"
    assert all(s.label == face_label for s in pl.segments), "subset leaked"
    assert 0 < len(pl.segments) < n_all, "subset should shrink the timeline"
    pl.set_ops(None)
    assert len(pl.segments) == n_all, "set_ops(None) must restore all ops"
    pl.set_speed(4.0)
    assert pl.speed == 4.0, "speed not stored"
    hits = []
    pl.on_position = lambda t: hits.append(t)
    pl.seek(0.0)
    pl.seek(pl.total * 0.3)
    assert len(hits) == 2, "on_position must fire on every seek"
    seg_rapid = next(s for s in pl.segments if s.kind == "rapid")
    seg_feed = next(s for s in pl.segments if s.kind == "feed")
    assert _sim3.gcode_readout(seg_rapid).startswith("G0 X")
    assert _sim3.gcode_readout(seg_feed).startswith("G1 X")
    print(f"PLAYER OK: {n_all} segments, total={pl.total:.1f}s", flush=True)

    # continuous material (issue #13): snapshots per cutting move, the
    # envelope profile follows the playhead anywhere in the program
    pl2 = _sim3.SimPlayer(doc, duration=10.0)
    pl2.enable_continuous()
    assert pl2._rsim is not None, "continuous mode built no removal sim"
    assert len(pl2._seg_cut) == len(pl2.segments), "segment map incomplete"
    p0 = pl2.material_profile_at(0.0)
    stock_r = None
    setup_obj = [o for o in doc.Objects if getattr(o, "LatheRole", "") == "setup"][0]
    stock_r = float(setup_obj.StockDiameter) / 2.0
    assert all(abs(z[1] - stock_r) < 1e-9 for z in p0), \
        "t=0 must show untouched stock"
    p_end = pl2.material_profile_at(pl2.total)
    v_start = pl2._rsim.env0.volume()
    vols = [pl2._rsim.state_at(pl2._seg_cut[pl2.segments.index(
        pl2.segment_at(pl2.total * k / 10.0)[0])],
        1.0).volume() for k in range(11)]
    assert all(vols[i] >= vols[i + 1] - 1e-6 for i in range(10)), \
        f"material must only shrink: {vols}"
    assert vols[-1] < vols[0], "program removed nothing"
    v_sim = _sim.simulate(doc).Shape.Volume
    assert abs(v_sim - vols[-1]) < 0.10 * v_sim, \
        f"continuous final {vols[-1]:.0f} vs sim {v_sim:.0f} differ >10%"
    mesh = _sim3.envelope_mesh(p_end)
    assert mesh.CountFacets > 100, "mesh too thin"
    mesh0 = _sim3.envelope_mesh(p0)
    assert mesh0.CountPoints > 100, "stock mesh empty"
    print(f"CONTINUOUS OK: {len(pl2._rsim.moves)} cut moves, "
          f"{v_start:.0f} -> {vols[-1]:.0f} mm3 (sim {v_sim:.0f})", flush=True)

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

    # post-dialog default path (issue #9): <DocName>_<stamp>.nc next to
    # the saved document; unsaved/unnamed docs fall back to ~
    p = _gui._default_post_path(doc)
    assert p.startswith(os.path.expanduser("~/") + "Sim_"), p
    assert p.endswith(".nc") and len(p.split("_")[-2]) == 10, p  # date_time stamp kept
    doc9 = FreeCAD.newDocument("D6-test")
    doc9.Label = 'a/b:c*d?e"f<g>h|i'
    p9 = _gui._default_post_path(doc9)
    assert p9.startswith(os.path.expanduser("~/") + "a_b_c_d_e_f_g_h_i_"), p9
    os.makedirs("/tmp/lathecam_issue9", exist_ok=True)
    doc9.saveAs("/tmp/lathecam_issue9/D6-test.FCStd")
    assert doc9.FileName, "saveAs did not set FileName"
    # saveAs resets Label to the file base name; saved doc defaults to its folder
    assert _gui._default_post_path(doc9).startswith(
        "/tmp/lathecam_issue9/D6-test_"), "saved doc must default to doc folder"
    FreeCAD.closeDocument(doc9.Name)
    docu = FreeCAD.newDocument()  # truly unnamed ("Unnamed"), unsaved
    assert _gui._default_post_path(docu).startswith(
        os.path.expanduser("~/lathe_program_")), "unsaved fallback broken"
    FreeCAD.closeDocument(docu.Name)
    print("POSTDEFAULT OK", flush=True)

    print("SIM PASS", flush=True)
except Exception as e:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    print(f"SIM FAIL: {e}", flush=True)
    sys.exit(1)
