"""ZZHarness: real-GUI end-to-end test for LatheCAM.

Symlink this folder into the FreeCAD Mod dir as "ZZHarness". Its
InitGui.py schedules a test run in the live Qt event loop: activates
the LatheCAM workbench (runs Initialize + every command's
GetResources through the real C++ registry), runs every toolbar
command through Gui.runCommand, verifies the document and the posted
G-code, then closes FreeCAD. Prints HARNESS PASS / HARNESS FAIL.

NOTE: like every InitGui.py this runs via exec() in a function scope,
so _run() re-imports everything it needs (module-level names are gone
by the time the timer fires).
"""

from PySide import QtCore

RESULT = "/tmp/lathe_harness_result.txt"
with open(RESULT, "w") as _f:  # proves InitGui.py itself ran
    _f.write("init\n")


def _run():
    import os
    import traceback

    with open(RESULT, "a") as f:
        f.write("run-entered\n")

    import FreeCAD
    import FreeCADGui as Gui

    OPS = ("face", "od_rough", "od_finish", "id_rough", "id_finish",
           "groove", "part", "thread")
    nc_path = os.path.expanduser("~/lathe_program.nc")
    try:
        os.remove(nc_path)
    except OSError:
        pass

    try:
        # 1) activate workbench: real Initialize(), real addCommand,
        #    real GetResources() calls through C++
        Gui.activateWorkbench("LatheCAM")
        with open(RESULT, "a") as f:
            f.write("activated\n")
        print("HARNESS step: workbench activated", flush=True)

        # 2) patch dialogs so CmdPost needs no interaction
        from PySide import QtWidgets
        QtWidgets.QFileDialog.getSaveFileName = staticmethod(
            lambda *a, **k: (nc_path, "G-code (*.nc)"))
        QtWidgets.QMessageBox.information = staticmethod(lambda *a, **k: None)
        QtWidgets.QMessageBox.critical = staticmethod(
            lambda *a, **k: print("HARNESS MSGBOX:", a[2] if len(a) > 2 else a,
                                  flush=True))

        # 3) real document + every command through the real command stack
        doc = FreeCAD.newDocument("Harness")
        names = (["CmdLatheSetup"] + [f"CmdLatheOp_{t}" for t in OPS]
                 + ["CmdLathePost"])
        for name in names:
            Gui.runCommand(name, 0)
        print("HARNESS step: commands run", flush=True)

        # 4) verify document contents
        setup = next(o for o in doc.Objects
                     if getattr(o, "LatheRole", "") == "setup")
        ops = [o for o in doc.Objects if getattr(o, "LatheRole", "") == "op"]
        assert len(ops) == len(OPS), f"op count {len(ops)}"
        setup.BoreDiameter = 16.0          # make the ID ops valid
        for o in ops:
            if o.OpType == "face":
                o.FaceDiameter = 30.0
        doc.recompute()
        print(f"HARNESS step: doc ok ({len(ops)} ops)", flush=True)

        # 5) post again (now with valid bore) and verify G-code
        Gui.runCommand("CmdLathePost", 0)
        assert os.path.exists(nc_path), "no posted file"
        txt = open(nc_path).read()
        assert "G21" in txt and "M30" in txt, "bad gcode"
        assert "G32" in txt, "no threading"
        print(f"HARNESS step: gcode ok ({len(txt.splitlines())} lines)",
              flush=True)

        # 6) selecting a cone/sphere face must produce a profile-following
        #    op, not a straight cylinder bore (the ball-seat regression)
        import Part
        from LatheCAM import program as _prog
        bdoc = FreeCAD.newDocument("BallSeat")
        outer = Part.makeCylinder(15.0, 25.0, FreeCAD.Vector(0, 0, -25.0))
        bore = Part.makeCylinder(5.0, 25.0, FreeCAD.Vector(0, 0, -25.0))
        funnel = Part.makeCone(5.0, 8.0, 5.196, FreeCAD.Vector(0, 0, -5.196))
        seat = Part.makeSphere(6.0, FreeCAD.Vector(0, 0, -18.0))
        solid = outer.cut(bore.fuse(funnel).fuse(seat))
        feat = bdoc.addObject("Part::Feature", "BallSeat_part")
        feat.Shape = solid
        bdoc.recompute()
        want = [f"Face{i + 1}" for i, fa in enumerate(solid.Faces)
                if fa.Surface.TypeId in ("Part::GeomCone", "Part::GeomSphere")]
        assert want, f"test solid has no cone/sphere face: " \
                     f"{[fa.Surface.TypeId for fa in solid.Faces]}"
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(feat, want)
        Gui.runCommand("CmdLatheOp_id_finish", 0)
        nop = [o for o in bdoc.Objects if getattr(o, "LatheRole", "") == "op"]
        assert len(nop) == 1, f"expected 1 op, got {len(nop)}"
        prof = nop[0].ProfileString
        assert prof, "cone/sphere selection left ProfileString empty"
        assert any(len(p.split(",")) > 2 for p in prof.split(";")), \
            f"profile has no arc: {prof}"
        g2 = _prog.post_document(bdoc)
        assert ("G02" in g2 or "G03" in g2), "no arc move in posted bore"
        assert "R6" in g2 or "R5" in g2, f"no arc radius in gcode:\n{g2}"
        print(f"HARNESS step: ball-seat profile ok ({prof})", flush=True)
        FreeCAD.closeDocument(bdoc.Name)

        with open(RESULT, "a") as f:
            f.write("HARNESS PASS\n")
        print("HARNESS PASS", flush=True)
    except Exception:
        tb = traceback.format_exc()
        with open(RESULT, "a") as f:
            f.write("HARNESS FAIL\n" + tb + "\n")
        print("HARNESS FAIL\n" + tb, flush=True)
    finally:
        Gui.getMainWindow().close()


QtCore.QTimer.singleShot(5000, _run)
