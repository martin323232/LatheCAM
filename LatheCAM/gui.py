"""LatheCAM GUI commands (only imported when FreeCADGui is up)."""

import os
from contextlib import contextmanager
from datetime import datetime

import FreeCAD
import FreeCADGui as Gui

from . import objects
from . import vis
from . import sim
from . import program
from .model import OP_TYPES


@contextmanager
def _transaction(doc, name):
    """FreeCAD 1.x transaction helper (no TransactionContext in 1.1)."""
    doc.openTransaction(name)
    try:
        yield
        doc.commitTransaction()
    except Exception:
        doc.abortTransaction()
        raise


class CmdSetup:
    def GetResources(self):
        return {"MenuText": "Lathe Setup",
                "ToolTip": "Create stock + machine setup object",
                "Pixmap": ""}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        doc = Gui.ActiveDocument.Document
        Gui.Selection.clearSelection()
        with _transaction(doc, "Create Lathe Setup"):
            obj = objects.create_setup(doc)
        Gui.Selection.addSelection(obj)


def _selected_cyl_face(op_type="od"):
    """(diameter, z_top, z_bottom) merged over ALL selected faces.

    Diameter comes from cylindrical faces only: the largest (outer skin) for
    od ops, the smallest (throat) for id ops. Z-range is the union of every
    selected face, so cones/spheres (ball seats, chamfers) extend the range.
    """
    import FreeCADGui as Gui
    diams = []
    z_top = z_bot = None
    for sel in Gui.Selection.getSelectionEx():
        obj = sel.Object
        if obj is None:
            continue
        subs = (list(getattr(sel, "SubElementNames", ()) or ())
                or list(getattr(sel, "AsSubElements", ()) or ()))
        for sub in subs:
            leaf = sub.split(".")[-1]
            if not leaf.startswith("Face"):
                continue
            try:
                f = obj.getSubObject(sub)
                surf = f.Surface
            except Exception:
                continue
            bb = f.BoundBox
            z_top = bb.ZMax if z_top is None else max(z_top, bb.ZMax)
            z_bot = bb.ZMin if z_bot is None else min(z_bot, bb.ZMin)
            if getattr(surf, "TypeId", "") == "Part::GeomCylinder":
                diams.append(2.0 * surf.Radius)
    if not diams or z_top is None:
        return None
    dia = max(diams) if op_type.startswith("od") else min(diams)
    return dia, z_top, z_bot


class CmdOp:
    """Creates one op of a fixed type; .op_type set at registration."""

    op_type = "od_rough"

    def GetResources(self):
        return {"MenuText": f"Op: {self.op_type}",
                "ToolTip": f"Add a {self.op_type} operation "
                           "(seeds TargetDiameter from selected face)",
                "Pixmap": ""}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        doc = Gui.ActiveDocument.Document
        seed = None
        if self.op_type in ("od_rough", "od_finish", "id_rough", "id_finish"):
            seed = _selected_cyl_face(self.op_type)
        if objects.collect_program(doc)[0] is None:
            Gui.Selection.clearSelection()
            with _transaction(doc, "Create Lathe Setup"):
                objects.create_setup(doc)
        Gui.Selection.clearSelection()
        with _transaction(doc, f"Create Op {self.op_type}"):
            obj = objects.create_op(doc, self.op_type)
            if seed is not None:
                dia, z_top, z_bot = seed
                setup = objects.collect_program(doc)[0]
                if self.op_type in ("od_rough", "od_finish"):
                    stock_d = float(getattr(setup, "StockDiameter", 0.0) or 0.0)
                    if dia >= stock_d:
                        # selection is bigger than stock: grow the stock so the
                        # op validates (raw bar must clear the finished part)
                        setup.StockDiameter = dia + 2.0
                    obj.TargetDiameter = dia
                    obj.ZStart = z_top
                    obj.ZEnd = z_bot
                else:
                    # internal op: selected face is the bore to cut.
                    # the stock needs a smaller pre-drilled bore to cut from.
                    bore = float(getattr(setup, "BoreDiameter", 0.0) or 0.0)
                    if bore <= 0 or bore >= dia:
                        setup.BoreDiameter = max(dia - 2.0, 0.5)
                    obj.TargetDiameter = dia
                    obj.ZStart = max(z_top, 0.0)
                    obj.ZEnd = z_bot
        Gui.Selection.addSelection(obj)


class CmdPost:
    def GetResources(self):
        return {"MenuText": "Post (Masso G3)",
                "ToolTip": "Generate G-code for all Lathe ops",
                "Pixmap": ""}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        from PySide import QtGui, QtWidgets
        doc = Gui.ActiveDocument.Document
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            None, "Post G-code",
            os.path.expanduser(f"~/lathe_program_{stamp}.nc"),
            "G-code (*.nc *.ngc *.tap);;All files (*)")
        if not path:
            return
        try:
            gcode = program.post_document(doc, path)
            n = len(gcode.splitlines())
            QtWidgets.QMessageBox.information(
                None, "LatheCAM", f"Wrote {n} lines to\n{path}")
        except Exception as e:  # noqa: BLE001 - show any post error
            QtWidgets.QMessageBox.critical(None, "LatheCAM post failed", str(e))


class CmdVisualize:
    def GetResources(self):
        return {"MenuText": "Show toolpaths",
                "ToolTip": "Draw rapid/feed/thread toolpaths and stock in the 3D view",
                "Pixmap": ""}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        from PySide import QtWidgets
        doc = Gui.ActiveDocument.Document
        try:
            vis.visualize(doc)
            _AUTO["sig"] = _program_signature(doc)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(None, "LatheCAM visualize failed", str(e))
            return
        try:
            Gui.SendMsgToActiveView("ViewFit")
        except Exception:
            pass


_AUTO = {"sig": None}  # last drawn program signature (auto-redraw dedupe)
_OBSERVERS = []        # keep observers alive for the whole session


def _program_signature(doc):
    """Cheap hash of everything that affects the toolpaths."""
    import hashlib
    h = hashlib.sha1()
    for o in doc.Objects:
        if getattr(o, "LatheRole", "") not in ("op", "setup"):
            continue
        h.update(o.Name.encode())
        for p in o.PropertiesList:
            if p in ("Label", "Label2"):
                continue
            try:
                h.update(repr(o.getPropertyByName(p)).encode())
            except Exception:
                h.update(b"?")
    return h.hexdigest()


def refresh_if_changed(doc, force=False):
    """Redraw the toolpaths if the program changed. Returns True if scheduled.

    Called from the document observer after every recompute; the signature
    check keeps it from rebuilding (and from re-triggering itself, since
    visualize() itself touches the document).
    """
    if doc is None:
        return False
    if not any(getattr(o, "LatheRole", "") == "op" for o in doc.Objects):
        return False
    sig = _program_signature(doc)
    if not force and sig == _AUTO["sig"]:
        return False
    _AUTO["sig"] = sig

    def run():
        try:
            vis.visualize(doc)
        except Exception:
            pass                    # never break the GUI over a redraw
    import FreeCAD as App
    if getattr(App, "GuiUp", False):
        try:
            from PySide import QtCore
            # defer: we are inside a recompute notification, and creating or
            # removing objects here would re-enter the recompute
            QtCore.QTimer.singleShot(0, run)
            return True
        except Exception:
            pass                    # headless: no event loop, run inline
    run()
    return True


class AutoVisObserver:
    """Redraw toolpaths whenever a LatheCAM program is recomputed.

    Registered with FreeCAD.addDocumentObserver (App level — the Gui-level
    observer never fires for Python observers). slotRecomputedDocument is
    the hook that actually gets called; the signature check in
    refresh_if_changed keeps the redraw from re-triggering itself.
    """

    def __init__(self):
        _OBSERVERS.append(self)

    def slotRecomputedDocument(self, doc):
        refresh_if_changed(doc)

    # older/newer bindings may use this name instead
    def afterRecompute(self, doc):
        refresh_if_changed(doc)


def arm_auto_observer(app_module):
    """Register the auto-redraw observer once per FreeCAD session."""
    import builtins
    if getattr(builtins, "_lathecam_autovis", False):
        return None
    obs = AutoVisObserver()
    app_module.addDocumentObserver(obs)
    builtins._lathecam_autovis = True
    return obs


def observer_armed():
    import builtins
    return bool(getattr(builtins, "_lathecam_autovis", False))


class CmdRegenerate:
    def GetResources(self):
        return {"MenuText": "Regenerate toolpaths",
                "ToolTip": "Recompute the toolpaths from the current op "
                            "parameters and redraw them (use after editing "
                            "op/Setup/tool values)",
                "Pixmap": ""}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        from PySide import QtWidgets
        doc = Gui.ActiveDocument.Document
        doc.recompute()
        if not refresh_if_changed(doc, force=True):
            try:
                vis.visualize(doc)
            except Exception as e:  # noqa: BLE001
                QtWidgets.QMessageBox.critical(None, "LatheCAM regenerate failed",
                                               str(e))
                return
        try:
            Gui.SendMsgToActiveView("ViewFit")
        except Exception:
            pass


_LAST_SIM_OPS = set()  # remember the chooser result between runs


def _selected_op_labels(doc):
    """Labels of Lathe::Op objects currently selected in the tree ([] = none)."""
    try:
        sel = Gui.Selection.getSelectionEx()
    except Exception:
        return []
    names = []
    for sels in sel:
        o = getattr(sels, "Object", None)
        if o is not None and getattr(o, "LatheRole", "") == "op":
            names.append(o.Label)
    return names


def _choose_ops_dialog(doc):
    """Checkbox dialog over the document's ops; returns labels or None."""
    from PySide import QtWidgets
    ops = [o for o in doc.Objects if getattr(o, "LatheRole", "") == "op"]
    if not ops:
        QtWidgets.QMessageBox.information(None, "LatheCAM",
                                          "No LatheCAM operations in document.")
        return None
    dlg = QtWidgets.QDialog()
    dlg.setWindowTitle("LatheCAM \u2014 simulate which operations")
    root = QtWidgets.QVBoxLayout(dlg)
    root.addWidget(QtWidgets.QLabel("Animate the selected operations:"))
    boxes = []
    for o in ops:
        cb = QtWidgets.QCheckBox(f"{o.Label}   ({getattr(o, 'OpType', '')})")
        cb.setChecked(not _LAST_SIM_OPS or o.Label in _LAST_SIM_OPS)
        root.addWidget(cb)
        boxes.append((o, cb))
    row = QtWidgets.QHBoxLayout()
    all_btn = QtWidgets.QPushButton("All")
    none_btn = QtWidgets.QPushButton("None")
    for b in (all_btn, none_btn):
        row.addWidget(b)
    row.addStretch(1)
    bbox = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
    root.addLayout(row)
    root.addWidget(bbox)

    def pick(state):
        for _o, cb in boxes:
            cb.setChecked(state)
    all_btn.clicked.connect(lambda: pick(True))
    none_btn.clicked.connect(lambda: pick(False))
    bbox.accepted.connect(dlg.accept)
    bbox.rejected.connect(dlg.reject)
    if not dlg.exec_():
        return None
    labels = [o.Label for o, cb in boxes if cb.isChecked()]
    _LAST_SIM_OPS.clear()
    _LAST_SIM_OPS.update(labels)
    return labels


class CmdSimulate:
    def GetResources(self):
        return {"MenuText": "Simulate",
                "ToolTip": "Compute the finished part from the toolpath and "
                            "animate the tool. Select one or more operations "
                            "in the tree first to run only those.",
                "Pixmap": ""}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        from PySide import QtWidgets
        doc = Gui.ActiveDocument.Document
        only = _selected_op_labels(doc)
        try:
            sim.simulate(doc, only=only or None)
            sim.animate(doc, only=only or None)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(None, "LatheCAM simulation failed", str(e))
            return
        try:
            Gui.SendMsgToActiveView("ViewFit")
        except Exception:
            pass


class CmdProfileFromSel:
    """Fill an op's ProfileString from selected faces/edges of the part."""

    def GetResources(self):
        return {"MenuText": "Profile from selection\u2026",
                "ToolTip": "Build the ProfileString from faces/edges selected "
                           "in the 3D view (cylinders, cones, fillets and "
                           "shoulders are recognised). Selecting the whole "
                           "part object auto-detects the outer silhouette.",
                "Pixmap": ""}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        from PySide import QtWidgets
        from . import profile_extract
        doc = Gui.ActiveDocument.Document
        target = None
        picks = []       # (obj, [Face3, Edge5, ...])
        auto_shapes = []  # whole objects -> auto silhouette
        for s in Gui.Selection.getSelectionEx():
            subs = list(s.SubElementNames or [])
            if not subs and hasattr(s.Object, "ProfileString"):
                target = s.Object
            elif subs and hasattr(s.Object, "Shape"):
                picks.append((s.Object, subs))
            elif hasattr(s.Object, "Shape"):
                auto_shapes.append(s.Object)
        setups = [o for o in doc.Objects if hasattr(o, "ViewPlacement")]
        setup = setups[0] if setups else None
        try:
            if picks:
                segs = []
                for obj, subs in picks:
                    sh = profile_extract.lathe_shape(obj, setup)
                    segs.extend(profile_extract.segments(sh, subs))
                text = profile_extract.profile_from_segments(segs)
            elif auto_shapes:
                sh = profile_extract.lathe_shape(auto_shapes[0], setup)
                text = profile_extract.auto_profile(sh)
            else:
                QtWidgets.QMessageBox.warning(
                    None, "LatheCAM profile",
                    "Select faces or edges of the part in the 3D view "
                    "(or click the whole part object to auto-detect the "
                    "outer silhouette).")
                return
        except ValueError as e:
            QtWidgets.QMessageBox.critical(None, "LatheCAM profile", str(e))
            return
        if target is None:
            ops = [o for o in doc.Objects if hasattr(o, "ProfileString")]
            if not ops:
                QtWidgets.QMessageBox.warning(
                    None, "LatheCAM", "No LatheCAM op found - create one first.")
                return
            labels = [o.Label for o in ops]
            label, ok = QtWidgets.QInputDialog.getItem(
                None, "LatheCAM profile", "Fill profile into op:",
                labels, 0, False)
            if not ok:
                return
            target = ops[labels.index(label)]
        pts = [tuple(float(x) for x in p.strip().split(","))
               for p in text.split(";") if p.strip()]
        zmin = min(p[1] for p in pts)
        with _transaction(doc, "Profile from selection"):
            target.ProfileString = text
            if hasattr(target, "ZEnd"):
                target.ZEnd = zmin
        doc.recompute()
        QtWidgets.QMessageBox.information(
            None, "LatheCAM profile",
            f"{target.Label}\n\nProfileString:\n{text}\n\n"
            f"{len(pts)} points; ZEnd set to {zmin:.4g}.")


class CmdSimulateChoose:
    def GetResources(self):
        return {"MenuText": "Simulate (choose ops)\u2026",
                "ToolTip": "Pick which operations to animate, then simulate "
                            "only those",
                "Pixmap": ""}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        from PySide import QtWidgets
        doc = Gui.ActiveDocument.Document
        try:
            only = _choose_ops_dialog(doc)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(None, "LatheCAM chooser failed", str(e))
            return
        if not only:
            return
        try:
            sim.simulate(doc, only=only)
            sim.animate(doc, only=only)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(None, "LatheCAM simulation failed", str(e))
            return
        try:
            Gui.SendMsgToActiveView("ViewFit")
        except Exception:
            pass


def make_op_command(op_type):
    # FreeCAD 1.1 calls methods on whatever object addCommand receives:
    # pass an INSTANCE, not a class (a class gives unbound functions ->
    # "GetResources() missing 1 required positional argument: 'self'").
    return type(f"CmdOp_{op_type}", (CmdOp,), {"op_type": op_type})()


_TOOL_COLS = [
    ("Name", "name", "str"),
    ("T#", "tool_number", "int"),
    ("Kind", "kind", "kind"),
    ("Width", "width", "float"),
    ("Nose R", "nose_radius", "float"),
    ("Max depth", "max_depth", "float"),
    ("RPM", "rpm", "float"),
    ("Feed", "feed", "float"),
    ("Finish feed", "finish_feed", "float"),
    ("Pitch", "thread_pitch", "float"),
    ("Z ref", "z_reference", "zref"),
    ("Spindle", "spindle_dir", "dir"),
    ("Note", "note", "str"),
]


class CmdToolLibrary:
    """Edit the per-user tool library (names, T numbers, geometry, feeds)."""

    def GetResources(self):
        return {"MenuText": "Tool Library",
                "ToolTip": "Edit tool names, T numbers and cutting data "
                           "(saved to your user tools.json)",
                "Pixmap": ""}

    def IsActive(self):
        return True

    def Activated(self):
        from PySide import QtWidgets, QtCore
        import json
        from .program import load_tools, user_tool_library_path, user_tool_dir

        tools = sorted(load_tools().values(), key=lambda t: t.tool_number)

        dlg = QtWidgets.QDialog()
        dlg.setWindowTitle("LatheCAM tool library")
        dlg.resize(820, 360)
        lay = QtWidgets.QVBoxLayout(dlg)
        tbl = QtWidgets.QTableWidget(len(tools), len(_TOOL_COLS), dlg)
        tbl.setHorizontalHeaderLabels([c[0] for c in _TOOL_COLS])
        tbl.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)

        def fill(row, t):
            for col, (_hdr, attr, kind) in enumerate(_TOOL_COLS):
                if kind in ("kind", "zref", "dir"):
                    cb = QtWidgets.QComboBox()
                    if kind == "kind":
                        cb.addItems(["turn", "bore", "groove", "part", "thread"])
                    elif kind == "zref":
                        cb.addItems(["left", "right", "centre"])
                    else:
                        cb.addItems(["cw", "ccw"])
                    val = getattr(t, attr, "") if t else ""
                    idx = cb.findText(str(val))
                    cb.setCurrentIndex(max(idx, 0))
                    tbl.setCellWidget(row, col, cb)
                else:
                    val = getattr(t, attr, "") if t else ""
                    it = QtWidgets.QTableWidgetItem(str(val))
                    if kind == "int":
                        it.setData(QtCore.Qt.DisplayRole, str(int(float(val or 0))))
                    tbl.setItem(row, col, it)

        for r, t in enumerate(tools):
            fill(r, t)

        def add_row():
            r = tbl.rowCount()
            tbl.insertRow(r)
            fill(r, type("T", (), {"name": f"Tool{r+1}", "tool_number": r + 1,
                                   "kind": "turn", "width": 3.0,
                                   "nose_radius": 0.4, "max_depth": 20.0,
                                   "rpm": 800.0, "feed": 0.15,
                                   "finish_feed": 0.0,
                                   "thread_pitch": 0.0, "z_reference": "left",
                                   "spindle_dir": "cw", "note": ""})())

        def del_row():
            r = tbl.currentRow()
            if r >= 0:
                tbl.removeRow(r)

        btns = QtWidgets.QHBoxLayout()
        for label, fn in (("Add tool", add_row), ("Remove selected", del_row)):
            b = QtWidgets.QPushButton(label, dlg)
            b.clicked.connect(fn)
            btns.addWidget(b)
        btns.addStretch(1)
        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel,
            parent=dlg)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        btns.addWidget(bb)

        lay.addWidget(tbl)
        lay.addLayout(btns)
        note = QtWidgets.QLabel(
            f"Saved to: {user_tool_library_path()}\n"
            "T# must be unique; it is what the machine changes to (T.. M06).",
            dlg)
        note.setWordWrap(True)
        lay.insertWidget(0, note)

        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        out = []
        for r in range(tbl.rowCount()):
            rec = {}
            for col, (_hdr, attr, kind) in enumerate(_TOOL_COLS):
                if kind in ("kind", "zref", "dir"):
                    rec[attr] = tbl.cellWidget(r, col).currentText()
                else:
                    txt = (tbl.item(r, col).text() if tbl.item(r, col) else "").strip()
                    if kind == "int":
                        rec[attr] = int(float(txt or 0))
                    elif kind == "float":
                        rec[attr] = float(txt or 0)
                    else:
                        rec[attr] = txt
            if not rec["name"]:
                continue
            out.append(rec)
        nums = [t["tool_number"] for t in out]
        if len(nums) != len(set(nums)):
            QtWidgets.QMessageBox.critical(
                None, "Duplicate T number", "Each tool needs a unique T#.")
            return
        os.makedirs(user_tool_dir(), exist_ok=True)
        with open(user_tool_library_path(), "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
        # live-refresh the ToolName dropdowns in every open document
        try:
            import FreeCAD as _App
            from .objects import refresh_tool_enums
            for _d in _App.listDocuments().values():
                refresh_tool_enums(_d)
        except Exception:
            pass
        QtWidgets.QMessageBox.information(
            None, "Tool library saved",
            f"{len(out)} tools written to\n{user_tool_library_path()}")


class CmdAbout:
    """Show the running LatheCAM version and where it is loaded from."""

    def GetResources(self):
        return {"MenuText": "About LatheCAM",
                "ToolTip": "Show the installed LatheCAM version and "
                           "package location",
                "Pixmap": ""}

    def IsActive(self):
        return True

    def Activated(self):
        from PySide import QtWidgets
        import FreeCAD
        import LatheCAM
        import os
        pkg = os.path.dirname(os.path.abspath(LatheCAM.__file__))
        fc_ver = ".".join(v.strip() for v in FreeCAD.Version()[0:3])
        QtWidgets.QMessageBox.about(
            None, "About LatheCAM",
            "<h3>LatheCAM %s</h3>"
            "<p>Turning CAM for Masso G3 lathes.</p>"
            "<p><b>Loaded from:</b><br><small>%s</small></p>"
            "<p><b>FreeCAD</b> %s &nbsp;|&nbsp; <b>Repo:</b> "
            "github.com/martin323232/LatheCAM</p>"
            % (LatheCAM.__version__, pkg, fc_ver))
