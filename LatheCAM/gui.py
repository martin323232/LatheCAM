"""LatheCAM GUI commands (only imported when FreeCADGui is up)."""

import os
from contextlib import contextmanager

import FreeCAD
import FreeCADGui as Gui

from . import objects
from .program import post_document
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
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            None, "Post G-code", os.path.expanduser("~/lathe_program.nc"),
            "G-code (*.nc *.ngc *.tap);;All files (*)")
        if not path:
            return
        try:
            gcode = post_document(doc, path)
            n = len(gcode.splitlines())
            QtWidgets.QMessageBox.information(
                None, "LatheCAM", f"Wrote {n} lines to\n{path}")
        except Exception as e:  # noqa: BLE001 - show any post error
            QtWidgets.QMessageBox.critical(None, "LatheCAM post failed", str(e))


def make_op_command(op_type):
    # FreeCAD 1.1 calls methods on whatever object addCommand receives:
    # pass an INSTANCE, not a class (a class gives unbound functions ->
    # "GetResources() missing 1 required positional argument: 'self'").
    return type(f"CmdOp_{op_type}", (CmdOp,), {"op_type": op_type})()
