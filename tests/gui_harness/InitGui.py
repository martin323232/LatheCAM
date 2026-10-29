"""ZZHarness: real-GUI end-to-end test for LatheCAM.

Symlink this folder into the FreeCAD Mod dir as "ZZHarness". Its
InitGui.py schedules a test run in the live Qt event loop: activates
the LatheCAM workbench (runs Initialize + every command's
GetResources through the real C++ registry), runs every toolbar
command through Gui.runCommand, verifies the document and the posted
G-code, then closes FreeCAD. Prints HARNESS PASS / HARNESS FAIL.

NOTE: like every InitGui.py this runs via exec() with separate
globals/locals dicts, so functions defined here cannot see ANY name the
file defines at top level once they are called later (the exec locals
dict is gone and the functions' __globals__ never had those names).
_run() therefore re-imports everything it needs inside its body and
receives the result path as a default argument (evaluated at def time,
stored on the function object, survives the exec scope). Do not
reference module-level names from _run() — it dies with a NameError
PySide swallows silently, and the harness never runs.
"""

from PySide import QtCore

RESULT = "/tmp/lathe_harness_result.txt"
with open(RESULT, "w") as _f:  # proves InitGui.py itself ran
    _f.write("init\n")


def _run(result_path="/tmp/lathe_harness_result.txt"):
    import os
    import traceback

    with open(result_path, "a") as f:
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
        #    real GetResources() calls through C++. FreeCAD registers a
        #    Python workbench under its CLASS name ("LatheCAMWorkbench"),
        #    not the menu text, so resolve the key instead of hardcoding.
        wb_name = next((k for k in Gui.listWorkbenches()
                        if k.startswith("LatheCAM")), None)
        assert wb_name, (
            "LatheCAM workbench not registered; have: "
            f"{sorted(Gui.listWorkbenches())}")
        Gui.activateWorkbench(wb_name)
        with open(result_path, "a") as f:
            f.write("activated\n")
        print("HARNESS step: workbench activated", flush=True)

        # 1b) icons (issue #11): every command Pixmap and the workbench
        #     Icon must point at a file that actually ships
        import LatheCAM.gui as _gi
        _icons = ["setup", "tools", "profile_sel", "post", "vis", "regen",
                  "sim", "sim_choose", "sim_panel", "about", "workbench",
                  "op_panel", "delete_op"]
        _icons += ["op_" + t for t in OPS]
        _missing = [n for n in _icons if not os.path.exists(_gi._icon(n))]
        assert not _missing, f"icon files missing: {_missing}"
        _wb = Gui.getWorkbench(wb_name)
        assert getattr(_wb, "Icon", "") and os.path.exists(_wb.Icon), \
            f"workbench icon not set: {getattr(_wb, 'Icon', None)!r}"
        print(f"HARNESS step: icons ok ({len(_icons)} svgs)", flush=True)

        # 2) patch dialogs so CmdPost needs no interaction
        from PySide import QtWidgets
        QtWidgets.QFileDialog.getSaveFileName = staticmethod(
            lambda *a, **k: (nc_path, "G-code (*.nc)"))
        QtWidgets.QMessageBox.information = staticmethod(lambda *a, **k: None)
        MSGBOXES = []
        QtWidgets.QMessageBox.critical = staticmethod(
            lambda *a, **k: (MSGBOXES.append(a[2] if len(a) > 2 else a),
                             print("HARNESS MSGBOX:",
                                   a[2] if len(a) > 2 else a, flush=True)))

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

        # 6) selecting the ball-seat outline (cone + sphere + the straight
        #    bore connecting them) must produce a profile-following op, not
        #    a straight cylinder bore (the ball-seat regression). The
        #    profile must chain through every face of the outline, so the
        #    r5 bore faces are part of the selection (same input as the
        #    headless ball-seat test in test_headless.py).
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
        curved = [f"Face{i + 1}" for i, fa in enumerate(solid.Faces)
                  if fa.Surface.TypeId in ("Part::GeomCone",
                                           "Part::GeomSphere")]
        assert curved, f"test solid has no cone/sphere face: " \
                       f"{[fa.Surface.TypeId for fa in solid.Faces]}"
        want = curved + [f"Face{i + 1}" for i, fa in enumerate(solid.Faces)
                         if fa.Surface.TypeId == "Part::GeomCylinder"
                         and abs(fa.Surface.Radius - 5.0) < 1e-6]
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

        # 7) simulation panel (issue #8): dock opens over the Harness doc,
        #    the slider scrubs the seekable player, the checklist rebuilds
        #    the timeline, transport works, closing the dock pauses. The
        #    checklist also owns toolpath display while open (issue #8
        #    follow-up): checked ops show their drawn path, unchecked ops
        #    hide theirs, and closing restores the tree's own state.
        Gui.runCommand("CmdLatheVis", 0)
        vis_feats = [o for o in doc.Objects
                     if o.TypeId == "Part::Feature"
                     and getattr(o, "LatheRole", "") == "vis"
                     and getattr(o, "LatheOp", "")]
        assert vis_feats, "show toolpaths drew no per-op objects"
        # tree state before the panel: hide one path of the op that will
        # be checklist item(0) (same doc order _fill_op_list uses)
        _hide_label = ops[0].Label
        tree_hidden = [o for o in vis_feats if o.LatheOp == _hide_label]
        assert tree_hidden, f"op {_hide_label!r} drew no paths"
        tree_hidden[0].ViewObject.Visibility = False
        _mb7 = len(MSGBOXES)          # dialogs during the sim-panel steps
        Gui.runCommand("CmdLatheSimPanel", 0)
        from LatheCAM import gui as _gui8
        pan = _gui8._SIM_PANEL
        assert pan.get("dock") is not None, "sim panel did not open"
        assert pan["player"].segments, "player timeline empty"
        assert pan["player"].total > 0, "player total zero"
        Gui.updateGui()
        pan["slider"].setValue(500)
        Gui.updateGui()
        assert pan["player"].t > 0, "slider scrub did not move the player"
        assert "Op" in pan["status"].text(), \
            f"status line empty: {pan['status'].text()!r}"
        # continuous material (issue #13): the panel drives a live mesh
        # that follows the playhead, not an op-boundary Part::Feature
        assert pan["player"].continuous, "panel did not enable continuous"
        _mat = doc.getObject("Sim_part")
        assert _mat is not None and _mat.TypeId == "Mesh::Feature", \
            f"continuous Sim_part missing/wrong: {_mat}"
        assert _mat.Mesh.CountFacets > 100, "continuous mesh empty"
        _v0 = pan["player"]._rsim.state_at(-1).volume()
        _v1 = pan["player"]._rsim.state_at(
            len(pan["player"]._rsim.moves) - 1).volume()
        assert _v1 < _v0, "continuous sim removed nothing"
        pan["slider"].setValue(0)
        assert pan["player"].t == 0.0, "scrub back to start failed"
        pan["speed"].setCurrentIndex(5)          # 8x
        assert pan["player"].speed == 8.0, "speed combo not wired"
        pan["speed"].setCurrentIndex(2)          # back to 1x
        from PySide import QtCore as _tc
        first_label = str(pan["list"].item(0).data(_tc.Qt.UserRole))
        assert first_label == _hide_label, "checklist order != doc order"
        # panel opened with everything checked: the tree-hidden path is shown
        assert tree_hidden[0].ViewObject.Visibility, \
            "panel open did not follow the (all-checked) checklist"
        pan["list"].item(0).setCheckState(_tc.Qt.Unchecked)
        assert first_label not in pan["player"].op_labels, \
            "unchecked op still in the timeline"
        # uncheck must not disturb the live mesh or raise a dialog
        # (issue #13 regression: deleted Sim_part raised ReferenceError
        # from every playback tick — FreeCAD raises ReferenceError, not
        # RuntimeError, so narrow guards miss it)
        _mat2 = doc.getObject("Sim_part")
        assert _mat2 is not None and _mat2.TypeId == "Mesh::Feature", \
            f"uncheck broke the continuous mesh: {_mat2}"
        for _vf in vis_feats:
            if _vf.LatheOp == first_label:
                assert not _vf.ViewObject.Visibility, \
                    "uncheck did not hide the op's drawn path"
                assert _vf.Label in (setup.HiddenVisPaths or []), \
                    "panel hide not persisted to HiddenVisPaths"
        pan["list"].item(0).setCheckState(_tc.Qt.Checked)
        assert first_label in pan["player"].op_labels, "recheck lost the op"
        for _vf in vis_feats:
            if _vf.LatheOp == first_label:
                assert _vf.ViewObject.Visibility, \
                    "recheck did not re-show the op's drawn path"
        pan["play"].click()
        assert pan["player"].playing(), "play did not start the timer"
        for _ in range(50):
            pan["player"]._tick()    # drive manually: _run blocks the loop
        assert pan["player"].t > 0, "ticks did not advance the position"
        assert len(MSGBOXES) == _mb7, \
            f"sim panel raised error dialogs: {MSGBOXES[_mb7:]}"
        pan["stop"].click()
        assert not pan["player"].playing(), "stop left the timer running"
        assert pan["player"].t == 0.0, "stop did not rewind"
        pan["dock"].close()
        Gui.updateGui()
        assert not pan["player"].playing(), "closing the dock did not pause"
        # close restores the tree state captured at open: the first op's
        # path was hidden in the tree before the panel opened, so it must
        # be hidden again and HiddenVisPaths back to that snapshot.
        assert not tree_hidden[0].ViewObject.Visibility, \
            "close did not restore the tree's hidden path"
        assert tree_hidden[0].Label in (setup.HiddenVisPaths or []), \
            "close did not restore HiddenVisPaths"
        print(f"HARNESS step: sim panel ok "
              f"({len(pan['player'].segments)} segments)", flush=True)

        # 8) closed document (issue #8 bug report "data comes from a
        #    closed file"): closing the panel's document must clear the
        #    panel, and reopening it against a NEW document must rebind.
        #    The old code crashed here: _panel_restore_paths ran
        #    collect_program on the dead wrapper (ReferenceError).
        Gui.runCommand("CmdLatheSimPanel", 0)
        Gui.updateGui()
        assert pan["dock"].isVisible(), "panel did not reopen"
        assert pan["doc"] is doc and pan["player"] is not None
        FreeCAD.closeDocument(doc.Name)
        Gui.updateGui()
        assert pan["doc"] is None, "panel kept a closed document"
        assert pan["player"] is None, "player survived document close"
        assert pan["status"].text() == "Document closed.", \
            f"status not reset: {pan['status'].text()!r}"
        assert pan["list"].count() == 0, "checklist still shows closed ops"
        doc2 = FreeCAD.newDocument("Second")
        Gui.runCommand("CmdLatheSetup", 0)
        Gui.runCommand("CmdLatheOp_face", 0)
        s2 = next(o for o in doc2.Objects
                  if getattr(o, "LatheRole", "") == "setup")
        s2.BoreDiameter = 16.0
        for o in doc2.Objects:
            if getattr(o, "LatheRole", "") == "op" and o.OpType == "face":
                o.FaceDiameter = 30.0
        doc2.recompute()
        Gui.runCommand("CmdLatheSimPanel", 0)
        Gui.updateGui()
        assert pan["doc"] is doc2, "panel did not rebind to the new document"
        assert pan["player"] is not None and pan["player"].segments, \
            "rebound player has no timeline"
        assert pan["list"].count() == 1, \
            f"checklist not rebuilt: {pan['list'].count()} items"
        pan["dock"].close()
        Gui.updateGui()
        print("HARNESS step: closed-document rebind ok", flush=True)

        # 9) operation panel (issue #16): dock shows the op's profile and
        #    assigned faces; pick mode collects Face/Edge clicks; Apply
        #    writes SourceFace + ProfileString and clears the profile-less
        #    warning; Delete operation removes op + toolpaths and NEVER the
        #    part, even with a part face selected (the Delete-key trap).
        from LatheCAM import gui as _g16
        odoc = FreeCAD.newDocument("OpPanel")
        cyl = Part.makeCylinder(15.0, 25.0, FreeCAD.Vector(0, 0, -25.0))
        feat = odoc.addObject("Part::Feature", "p")
        feat.Shape = cyl
        odoc.recompute()
        Gui.setActiveDocument("OpPanel")
        Gui.runCommand("CmdLatheSetup", 0)
        Gui.runCommand("CmdLatheOp_od_finish", 0)
        Gui.updateGui()
        o16 = [o for o in odoc.Objects
               if getattr(o, "LatheRole", "") == "op"][0]
        _g16._open_op_panel(o16)
        Gui.updateGui()
        pan16 = _g16._OP_PANEL
        assert pan16.get("dock") is not None, "op panel did not open"
        assert pan16["op_name"] == o16.Name, "panel shows wrong op"
        assert pan16["warn"].isVisible(), \
            "profile-less op showed no warning"
        _mb9 = len(MSGBOXES)
        _g16._op_panel_set_picking(True)
        assert pan16["picking"], "pick mode did not engage"
        cyl_faces = [f"Face{i + 1}" for i, fa in enumerate(cyl.Faces)
                     if fa.Surface.TypeId == "Part::GeomCylinder"]
        assert cyl_faces, "test cylinder has no cylindrical face"
        # real 3D clicks reach the observer with sub="" — the panel must
        # pull the sub-names from the selection itself (live bug: the
        # callback sub is empty for 3D picks, so the list stayed empty)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(feat, [cyl_faces[0]])
        _g16._op_panel_pick_names("OpPanel", feat.Name, "")
        assert pan16["list"].count() == 1, \
            "3D-style pick (sub='') did not reach the list"
        Gui.Selection.clearSelection()
        _g16._op_panel_add_picks(feat, cyl_faces[1:])
        assert pan16["list"].count() == len(cyl_faces), "picks not listed"
        pan16["apply"].click()
        Gui.updateGui()
        assert o16.ProfileString, "apply wrote no profile"
        assert not pan16["warn"].isVisible(), "warning survived apply"
        # apply must also un-hide the op's toolpaths (sim checklist may
        # have hidden them \u2014 live report: "why is no toolpath shown")
        _st16 = [o for o in odoc.Objects
                 if getattr(o, "LatheRole", "") == "setup"][0]
        _hv = [o for o in odoc.Objects
               if getattr(o, "LatheRole", "") == "vis"
               and getattr(o, "LatheOp", "") == o16.Label]
        assert _hv, "op has no vis features to test"
        for _v in _hv:
            _v.ViewObject.Visibility = False
        _st16.HiddenVisPaths = sorted(
            set(str(x) for x in (_st16.HiddenVisPaths or []))
            | {v.Label for v in _hv})
        pan16["apply"].click()
        Gui.updateGui()
        assert all(v.ViewObject.Visibility for v in _hv), \
            "apply left the op's toolpaths hidden"
        assert not (set(str(x) for x in (_st16.HiddenVisPaths or []))
                    & {v.Label for v in _hv}), \
            "apply left the op's paths in HiddenVisPaths"
        src16 = o16.SourceFace
        assert src16 and src16[0] is feat, "SourceFace not stored"
        # delete operation with a part face selected (the Delete-key trap):
        # the op and its drawn paths vanish, the part survives
        Gui.runCommand("CmdLatheVis", 0)
        vis_before = [o.Name for o in odoc.Objects
                      if getattr(o, "LatheRole", "") == "vis"
                      and getattr(o, "LatheOp", "") == o16.Label]
        assert vis_before, "no op toolpaths to clean"
        o16_name = o16.Name
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(feat, [cyl_faces[0]])
        Gui.Selection.addSelection(o16)
        Gui.runCommand("CmdLatheDeleteOp", 0)
        Gui.updateGui()
        assert odoc.getObject(o16_name) is None, "op survived delete"
        assert odoc.getObject(feat.Name) is not None, "delete ate the part!"
        left16 = {o.Name for o in odoc.Objects
                  if getattr(o, "LatheRole", "") == "vis"}
        assert not left16 & set(vis_before), "op toolpaths survived delete"
        assert pan16["op_name"] is None, "panel not reset after delete"
        assert len(MSGBOXES) == _mb9, \
            f"op panel raised error dialogs: {MSGBOXES[_mb9:]}"
        pan16["dock"].close()
        FreeCAD.closeDocument(odoc.Name)
        Gui.updateGui()
        print("HARNESS step: operation panel ok", flush=True)

        # 10) Clear simulation: Sim_* objects vanish, ops and toolpaths stay
        sdoc = FreeCAD.newDocument("SimClear")
        scyl = Part.makeCylinder(15.0, 25.0, FreeCAD.Vector(0, 0, -25.0))
        sfeat = sdoc.addObject("Part::Feature", "p")
        sfeat.Shape = scyl
        sdoc.recompute()
        Gui.setActiveDocument("SimClear")
        Gui.runCommand("CmdLatheSetup", 0)
        Gui.runCommand("CmdLatheOp_od_finish", 0)
        Gui.updateGui()
        sgrp = sdoc.addObject("App::DocumentObjectGroup", "Sim_Simulation")
        spart = sdoc.addObject("Part::Feature", "Sim_part")
        spart.Shape = scyl
        stl = sdoc.addObject("Part::Feature", "Sim_tool")
        stl.Shape = Part.makeBox(1, 1, 1)
        sgrp.Group = [spart, stl]
        sdoc.recompute()
        Gui.runCommand("CmdLatheVis", 0)
        Gui.updateGui()
        vis_names = {o.Name for o in sdoc.Objects
                     if getattr(o, "LatheRole", "") == "vis"}
        assert vis_names, "vis objects missing before clear"
        Gui.runCommand("CmdLatheSimClear", 0)
        Gui.updateGui()
        left = [o.Name for o in sdoc.Objects if o.Name.startswith("Sim_")]
        assert not left, f"Sim_* survived clear: {left}"
        assert vis_names <= {o.Name for o in sdoc.Objects}, \
            "clear deleted toolpath visuals"
        ops_left = [o for o in sdoc.Objects
                    if getattr(o, "LatheRole", "") == "op"]
        assert ops_left, "clear deleted the operations"
        FreeCAD.closeDocument(sdoc.Name)
        Gui.updateGui()
        print("HARNESS step: clear simulation ok", flush=True)

        with open(result_path, "a") as f:
            f.write("HARNESS PASS\n")
        print("HARNESS PASS", flush=True)
    except Exception:
        tb = traceback.format_exc()
        with open(result_path, "a") as f:
            f.write("HARNESS FAIL\n" + tb + "\n")
        print("HARNESS FAIL\n" + tb, flush=True)
    finally:
        # close every document first (scripting closeDocument never
        # prompts; the main-window close would otherwise raise the
        # invisible "save changes?" dialog offscreen and the app would
        # only die on the script's 60 s kill). FreeCAD 1.1 has no
        # Document.Modified Python attribute — do not try to set it.
        for d in list(FreeCAD.listDocuments().values()):
            try:
                FreeCAD.closeDocument(d.Name)
            except Exception:
                pass
        Gui.getMainWindow().close()


QtCore.QTimer.singleShot(5000, _run)
