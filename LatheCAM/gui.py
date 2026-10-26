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


class CmdOp:
    """Creates one op of a fixed type; .op_type set at registration."""

    op_type = "od_rough"

    def GetResources(self):
        return {"MenuText": f"Op: {self.op_type}",
                "ToolTip": f"Add a {self.op_type} operation",
                "Pixmap": ""}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        doc = Gui.ActiveDocument.Document
        if objects.collect_program(doc)[0] is None:
            Gui.Selection.clearSelection()
            with _transaction(doc, "Create Lathe Setup"):
                objects.create_setup(doc)
        Gui.Selection.clearSelection()
        with _transaction(doc, f"Create Op {self.op_type}"):
            obj = objects.create_op(doc, self.op_type)
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
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(None, "LatheCAM visualize failed", str(e))
            return
        try:
            Gui.SendMsgToActiveView("ViewFit")
        except Exception:
            pass


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
        QtWidgets.QMessageBox.information(
            None, "Tool library saved",
            f"{len(out)} tools written to\n{user_tool_library_path()}")
