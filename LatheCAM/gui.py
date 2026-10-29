"""LatheCAM GUI commands (only imported when FreeCADGui is up)."""

import math
import os
from contextlib import contextmanager
from datetime import datetime

from PySide import QtCore, QtGui, QtWidgets  # safe: this module needs the GUI

import FreeCAD
import FreeCADGui as Gui

from . import objects
from . import vis
from . import sim
from . import program
from . import profile_extract

_ICON_DIR = os.path.join(os.path.dirname(__file__), "Resources", "icons")


def _icon(name):
    """Toolbar/menu icon path (issue #11: QtSvg renders these natively)."""
    return os.path.join(_ICON_DIR, name + ".svg")
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
                "Pixmap": _icon("setup")}

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


# Ops whose generator understands a ProfileString.
PROFILE_OPS = ("od_rough", "od_finish", "id_rough", "id_finish")


def _selection_face_picks():
    """[(object, [Face names])] for faces currently selected in the 3D view."""
    picks = []
    for sel in Gui.Selection.getSelectionEx():
        if sel.Object is None or not hasattr(sel.Object, "Shape"):
            continue
        subs = (list(getattr(sel, "SubElementNames", ()) or ())
                or list(getattr(sel, "AsSubElements", ()) or ()))
        faces = [s for s in subs if s.split(".")[-1].startswith("Face")]
        if faces:
            picks.append((sel.Object, faces))
    return picks


class CmdOp:
    """Creates one op of a fixed type; .op_type set at registration."""

    op_type = "od_rough"

    def GetResources(self):
        return {"MenuText": f"Op: {self.op_type}",
                "ToolTip": f"Add a {self.op_type} operation (seeds "
                           "TargetDiameter from the selected face; if the "
                           "selection contains a cone, sphere or fillet the "
                           "ProfileString is filled too, so the tool follows "
                           "that form instead of cutting a straight cylinder)",
                "Pixmap": _icon("op_" + self.op_type)}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        doc = Gui.ActiveDocument.Document
        picks = _selection_face_picks()
        seed = None
        if self.op_type in ("od_rough", "od_finish", "id_rough", "id_finish"):
            seed = _selected_cyl_face(self.op_type)
        if objects.collect_program(doc)[0] is None:
            Gui.Selection.clearSelection()
            with _transaction(doc, "Create Lathe Setup"):
                objects.create_setup(doc)
        setup = objects.collect_program(doc)[0]
        # A non-cylindrical selection must become a profile, or the op silently
        # machines a straight bore and leaves cones/ball seats untouched.
        prof = None
        if (picks and self.op_type in PROFILE_OPS
                and profile_extract.selection_needs_profile(picks)):
            try:
                prof = profile_extract.profile_text(picks, setup)
            except ValueError as e:
                FreeCAD.Console.PrintError(
                    f"LatheCAM: could not build a profile from the selection "
                    f"({e}); creating a plain cylinder op instead.\n")
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
            if prof:
                obj.ProfileString = prof
                obj.ZEnd = profile_extract.profile_z_end(prof)
            elif self.op_type in PROFILE_OPS and seed is None:
                FreeCAD.Console.PrintWarning(
                    f"LatheCAM: {obj.Label} was created without a face "
                    "selection \u2014 it cuts a straight cylinder. Select the "
                    "faces/edges on the part and use 'Profile from "
                    "selection\u2026' or the Operation panel to give it the "
                    "real form.\n")
            if picks:
                # remember which geometry this op was derived from (issue #5);
                # clicking the op in the tree lights these faces up
                src_obj, src_faces = picks[0]
                try:
                    obj.SourceFace = (src_obj, src_faces)
                except Exception:
                    pass
        Gui.Selection.addSelection(obj)


def _default_post_path(doc):
    """Default Post dialog path (issue #9): <DocName>_<stamp>.nc.

    Saved documents default to the document's own folder next to the
    .FCStd; unsaved or still-unnamed documents fall back to the old
    ~/lathe_program_<stamp>.nc. The timestamp stays so repeated posts
    never overwrite silently.
    """
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    name = (getattr(doc, "Label", "") or doc.Name or "").strip()
    for ch in '\\/:*?"<>|':
        name = name.replace(ch, "_")
    name = name.strip(". ")  # Windows: no trailing dots/spaces
    if not name or name.lower().startswith("unnamed"):
        name = "lathe_program"
    folder = (os.path.dirname(doc.FileName) if doc.FileName
              else os.path.expanduser("~"))
    return os.path.join(folder, f"{name}_{stamp}.nc")


class CmdPost:
    def GetResources(self):
        return {"MenuText": "Post (Masso G3)",
                "ToolTip": "Generate G-code for all Lathe ops",
                "Pixmap": _icon("post")}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        from PySide import QtGui, QtWidgets
        doc = Gui.ActiveDocument.Document
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            None, "Post G-code", _default_post_path(doc),
            "G-code (*.nc *.ngc *.tap);;All files (*)")
        if not path:
            return
        try:
            gcode = program.post_document(doc, path)
            n = len(gcode.splitlines())
            missing = [o.Label for o in doc.Objects
                       if getattr(o, "LatheRole", "") == "op"
                       and getattr(o, "OpType", "") in PROFILE_OPS
                       and not str(getattr(o, "ProfileString", "") or "").strip()]
            msg = f"Wrote {n} lines to\n{path}"
            warns = [ln[2:].strip() for ln in gcode.splitlines()
                     if ln.startswith("; WARNING:")]
            if warns:
                extra = ("\u26a0 Post-time warnings:\n  "
                         + "\n  ".join(warns))
                FreeCAD.Console.PrintWarning("LatheCAM: " + extra + "\n")
                msg += "\n\n" + extra
            if missing:
                extra = ("\u26a0 Ops without a profile cut straight "
                         "cylinders \u2014 if that is wrong, assign faces/"
                         "edges (Operation panel):\n  " + "\n  ".join(missing))
                FreeCAD.Console.PrintWarning("LatheCAM: " + extra + "\n")
                msg += "\n\n" + extra
            QtWidgets.QMessageBox.information(None, "LatheCAM", msg)
        except Exception as e:  # noqa: BLE001 - show any post error
            QtWidgets.QMessageBox.critical(None, "LatheCAM post failed", str(e))


class CmdVisualize:
    def GetResources(self):
        return {"MenuText": "Show toolpaths",
                "ToolTip": "Draw rapid/feed/thread toolpaths and stock in the 3D view",
                "Pixmap": _icon("vis")}

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
            # HiddenVisPaths is written BY visualize(); hashing it would
            # make every redraw look like a program change and loop.
            if p in ("Label", "Label2", "HiddenVisPaths"):
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
    closer = SimPanelCloser()   # keeps the sim panel honest about closed docs
    app_module.addDocumentObserver(closer)
    builtins._lathecam_autovis = True
    return obs


class SimPanelCloser:
    """Reset the simulation panel when its document is closed (issue #8).

    Without this the dock keeps showing the closed file's checklist and
    status, and reopening the panel against another document crashed
    while restoring the dead document's tree state (ReferenceError).
    """

    def __init__(self):
        _OBSERVERS.append(self)

    def slotDeletedDocument(self, doc):
        try:
            name = doc.Name
        except Exception:
            return
        if _SIM_PANEL.get("doc_name") == name:
            _panel_doc_closed(_SIM_PANEL)


def observer_armed():
    import builtins
    return bool(getattr(builtins, "_lathecam_autovis", False))


class SourceFaceObserver:
    """Lights up an op's source faces when the op is clicked in the tree.

    Gui.Selection observer protocol: ``doc``/``obj`` arrive as name strings,
    ``sub`` as a tuple of sub-names. The highlight is cosmetic and never
    tracked state — the user's next click replaces the whole selection, so
    there is nothing to clear up.
    """

    def __init__(self):
        self._guard = False

    def _highlight(self, doc, obj):
        if self._guard or not obj:
            return
        try:
            op = FreeCAD.getDocument(doc).getObject(obj)
            src = objects.source_faces(op) if op is not None else None
            if not src:
                return
            part, faces = src
            self._guard = True
            try:
                # addSelection takes the DocumentObject, not a name string
                Gui.Selection.addSelection(part, faces)
            finally:
                self._guard = False
        except Exception:
            pass  # highlight is cosmetic; never break selection handling

    # A tree click arrives as clearSelection + addSelection(doc, obj, sub,
    # pnt); selection replacements (e.g. the 3D view) arrive as
    # setSelection(doc, obj, sub). Handle both.
    def setSelection(self, doc, obj, sub):
        self._highlight(doc, obj)
        if not self._guard:
            self._track_op(doc, obj)
            _op_panel_pick_names(doc, obj, sub)

    def addSelection(self, doc, obj, sub, pnt=None):
        self._highlight(doc, obj)
        if not self._guard:
            self._track_op(doc, obj)
            _op_panel_pick_names(doc, obj, sub)

    def _track_op(self, docname, objname):
        """Op clicked in the tree -> the operation panel follows (issue #16)."""
        try:
            op = FreeCAD.getDocument(docname).getObject(objname)
        except Exception:
            return
        if op is None or getattr(op, "LatheRole", "") != "op":
            return
        _op_panel_open_soon(op)

    def rmSelection(self, doc, obj, sub, pnt=None):
        pass

    def clearSelection(self, doc):
        pass


def arm_face_observer():
    """Register the source-face highlight observer once per FreeCAD session.

    The highlight is cosmetic: if the selection API is unavailable (test
    harness mocks, non-standard builds) the workbench still initializes.
    """
    import builtins
    if getattr(builtins, "_lathecam_faceobs", None) is not None:
        return None
    obs = SourceFaceObserver()
    try:
        Gui.Selection.addObserver(obs)
    except (AttributeError, RuntimeError):
        return None
    builtins._lathecam_faceobs = obs  # keep-alive: PySide does not own it
    return obs


def face_observer_armed():
    import builtins
    return getattr(builtins, "_lathecam_faceobs", None) is not None


class CmdRegenerate:
    def GetResources(self):
        return {"MenuText": "Regenerate toolpaths",
                "ToolTip": "Recompute the toolpaths from the current op "
                            "parameters and redraw them (use after editing "
                            "op/Setup/tool values)",
                "Pixmap": _icon("regen")}

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
                "Pixmap": _icon("sim")}

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
                "Pixmap": _icon("profile_sel")}

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
            if picks:
                faces = [s for s in picks[0][1]
                         if s.split(".")[-1].startswith("Face")]
                if faces:
                    try:
                        target.SourceFace = (picks[0][0], faces)
                    except Exception:
                        pass
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
                "Pixmap": _icon("sim_choose")}

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


# ------------------------------------------------------ simulation panel

_SIM_PANEL = {}  # live dock + widgets (issue #8); empty when closed


def _doc_alive(doc):
    """True if the wrapper still points at an open document (issue #8).

    A closed document's Python wrapper raises ReferenceError on any
    attribute access; the panel used to hold such a wrapper and crash
    when reopened against another document.
    """
    if doc is None:
        return False
    try:
        name = doc.Name
    except Exception:
        return False
    return name in FreeCAD.listDocuments()


def _panel_doc_closed(panel):
    """The panel's document was closed: drop every stale reference.

    The dock keeps its widgets but shows "Document closed."; reopening
    the panel against another document rebinds cleanly (issue #8).
    """
    p = panel.get("player")
    if p is not None:
        try:
            p.pause()
        except Exception:
            pass
    panel["doc"] = None
    panel["doc_name"] = None
    panel["player"] = None
    panel["vis_snapshot"] = None
    lst = panel.get("list")
    if lst is not None:
        try:
            lst.clear()
        except Exception:
            pass
    st = panel.get("status")
    if st is not None:
        try:
            st.setText("Document closed.")
        except Exception:
            pass


def _fill_op_list(lst, ops):
    """Rebuild the checklist; op Label rides on UserRole for retrieval."""
    from PySide import QtCore, QtWidgets
    lst.clear()
    for o in ops:
        it = QtWidgets.QListWidgetItem(f"{o.Label}   ({getattr(o, 'OpType', '')})")
        it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
        it.setCheckState(QtCore.Qt.Checked)
        it.setData(QtCore.Qt.UserRole, o.Label)
        lst.addItem(it)


def _panel_vis_features(doc):
    """The per-op drawn toolpath objects (issue #7 Vis_* features).

    Vis_stock carries no LatheOp and is excluded: it is context, not a path.
    """
    return [o for o in doc.Objects
            if o.TypeId == "Part::Feature"
            and getattr(o, "LatheRole", "") == "vis"
            and getattr(o, "LatheOp", "")]


def _panel_checked_labels(panel):
    """Op labels currently ticked in the panel checklist."""
    from PySide import QtCore
    lst = panel.get("list")
    out = []
    if lst is None:
        return out
    for i in range(lst.count()):
        it = lst.item(i)
        if it.checkState() == QtCore.Qt.Checked:
            out.append(str(it.data(QtCore.Qt.UserRole)))
    return out


def _panel_apply_paths(panel):
    """Mirror the checklist onto the drawn toolpaths (issue #8).

    While the panel is open it owns toolpath visibility: every Vis_* path
    follows its op's checkbox and Setup.HiddenVisPaths is kept in sync so
    an auto-redraw cannot revert the view. The tree's own state was
    snapshotted at open and is restored when the panel closes.
    """
    doc = panel.get("doc")
    if not _doc_alive(doc):
        return
    labels = set(_panel_checked_labels(panel))
    setup = objects.collect_program(doc)[0]
    can_store = setup is not None and "HiddenVisPaths" in setup.PropertiesList
    hidden = set(str(x) for x in (getattr(setup, "HiddenVisPaths", None) or [])) \
        if setup is not None else set()
    for o in _panel_vis_features(doc):
        show = o.LatheOp in labels
        if can_store:
            (hidden.discard if show else hidden.add)(o.Label)
        vo = getattr(o, "ViewObject", None)
        if vo is not None:
            try:
                vo.Visibility = show
            except Exception:
                pass
    if can_store:
        try:
            setup.HiddenVisPaths = sorted(hidden)
        except Exception:
            pass


def _panel_snapshot_paths(panel):
    """Remember the tree's own toolpath visibility for restore-on-close."""
    doc = panel.get("doc")
    if not _doc_alive(doc):
        return
    setup = objects.collect_program(doc)[0]
    panel["vis_snapshot"] = vis._hidden_labels(doc, setup)


def _panel_restore_paths(panel):
    """Give the tree its visibility state back when the panel closes."""
    snap = panel.get("vis_snapshot")
    doc = panel.get("doc")
    if snap is None or not _doc_alive(doc):
        return
    setup = objects.collect_program(doc)[0]
    if setup is not None and "HiddenVisPaths" in setup.PropertiesList:
        try:
            setup.HiddenVisPaths = sorted(snap)
        except Exception:
            pass
    for o in _panel_vis_features(doc):
        vo = getattr(o, "ViewObject", None)
        if vo is not None:
            try:
                vo.Visibility = o.Label not in snap
            except Exception:
                pass


def _open_sim_panel(doc):
    """Create or raise the simulation dock (issue #8): transport, speed,
    scrubbable timeline and per-op checkboxes over a seekable SimPlayer.

    While open, the checklist also drives toolpath display: checked ops
    show their drawn path, unchecked ops hide theirs (issue #8 follow-up);
    closing the dock restores the tree's own visibility state."""
    from PySide import QtCore, QtWidgets
    ops = [o for o in doc.Objects if getattr(o, "LatheRole", "") == "op"]
    if not ops:
        QtWidgets.QMessageBox.information(
            None, "LatheCAM", "No LatheCAM operations in document.")
        return None

    existing = _SIM_PANEL.get("dock")
    if existing is not None:
        try:
            existing.show()
            existing.raise_()
            if _SIM_PANEL.get("doc") is not doc:
                _sim_panel_rebind(_SIM_PANEL, doc)
            else:
                # reopened after a close: the tree may have changed while
                # the panel was away - re-snapshot, then let the checklist
                # (still holding its previous ticks) drive the display.
                _panel_snapshot_paths(_SIM_PANEL)
                _panel_apply_paths(_SIM_PANEL)
            return existing
        except RuntimeError:
            _SIM_PANEL.clear()      # C++ side deleted (session teardown)

    try:
        player = sim.SimPlayer(doc)
    except Exception as e:  # noqa: BLE001 - no setup/ops/tool
        QtWidgets.QMessageBox.critical(None, "LatheCAM simulation failed", str(e))
        return None

    main = Gui.getMainWindow()
    dock = QtWidgets.QDockWidget("LatheCAM \u2014 Simulation", main)
    dock.setObjectName("LatheCAM_SimPanel")
    dock.setAllowedAreas(QtCore.Qt.AllDockWidgetAreas)
    body = QtWidgets.QWidget(dock)
    dock.setWidget(body)
    main.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
    v = QtWidgets.QVBoxLayout(body)
    v.setContentsMargins(8, 8, 8, 8)
    v.setSpacing(6)

    v.addWidget(QtWidgets.QLabel("Operations:"))
    lst = QtWidgets.QListWidget(body)
    lst.setMaximumHeight(140)
    _fill_op_list(lst, ops)
    v.addWidget(lst)

    row = QtWidgets.QHBoxLayout()
    all_btn = QtWidgets.QPushButton("All")
    none_btn = QtWidgets.QPushButton("None")
    row.addWidget(all_btn)
    row.addWidget(none_btn)
    row.addStretch(1)
    v.addLayout(row)

    tl = QtWidgets.QHBoxLayout()
    play_btn = QtWidgets.QPushButton("\u25b6 Play")
    pause_btn = QtWidgets.QPushButton("\u2759\u2759 Pause")
    stop_btn = QtWidgets.QPushButton("\u25a0 Stop")
    speed = QtWidgets.QComboBox()
    for txt, mult in (("0.25x", 0.25), ("0.5x", 0.5), ("1x", 1.0),
                      ("2x", 2.0), ("4x", 4.0), ("8x", 8.0)):
        speed.addItem(txt, mult)
    speed.setCurrentIndex(2)
    for b in (play_btn, pause_btn, stop_btn):
        tl.addWidget(b)
    tl.addStretch(1)
    tl.addWidget(QtWidgets.QLabel("Speed"))
    tl.addWidget(speed)
    v.addLayout(tl)

    slider = QtWidgets.QSlider(QtCore.Qt.Horizontal, body)
    slider.setRange(0, 1000)
    slider.setToolTip("Scrub the timeline: drag to move the tool anywhere "
                      "in the program")
    v.addWidget(slider)

    status = QtWidgets.QLabel("\u2014")
    status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    v.addWidget(status)

    _SIM_PANEL.clear()
    _SIM_PANEL.update({"dock": dock, "doc": doc, "doc_name": doc.Name,
                       "list": lst,
                       "slider": slider, "status": status, "speed": speed,
                       "play": play_btn, "pause": pause_btn,
                       "stop": stop_btn,
                       "player": player, "syncing": False, "bulk": False,
                       "vis_snapshot": None})
    player.enable_continuous()          # issue #13: material follows every move
    player.ensure_marker()
    _panel_snapshot_paths(_SIM_PANEL)   # tree state to restore on close
    _panel_apply_paths(_SIM_PANEL)      # all checked: show every drawn path

    def checked_labels():
        return _panel_checked_labels(_SIM_PANEL)

    def update_status(t):
        p = _SIM_PANEL.get("player")
        if p is None or not p.segments:
            status.setText("\u2014")
            return
        s, _f = p.segment_at(t)
        if s is None:
            status.setText("\u2014")
            return
        status.setText(
            f"Op {s.op + 1}/{len(p.op_labels)} \u00b7 {s.label} \u00b7 "
            f"move {s.move}/{s.n_moves} \u00b7 {sim.gcode_readout(s)}")
        if not _SIM_PANEL["syncing"] and p.total > 0:
            _SIM_PANEL["syncing"] = True
            slider.setValue(int(round(t / p.total * 1000)))
            _SIM_PANEL["syncing"] = False

    player.on_position = update_status
    _SIM_PANEL["update_status"] = update_status

    def rebuild_timeline():
        p = _SIM_PANEL.get("player")
        if p is None:
            return
        labels = checked_labels()
        _panel_apply_paths(_SIM_PANEL)   # checklist owns the drawn paths
        if not labels:
            p.pause()
            _SIM_PANEL["syncing"] = True
            slider.setValue(0)
            _SIM_PANEL["syncing"] = False
            status.setText("No operations selected.")
            return
        try:
            p.set_ops(labels)
            if not p.continuous:
                sim.simulate(_SIM_PANEL["doc"], only=labels)
            p.seek(0.0)
        except Exception as e:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(
                None, "LatheCAM simulation failed", str(e))

    def on_item_changed(_item):
        if not _SIM_PANEL.get("bulk"):
            rebuild_timeline()
    lst.itemChanged.connect(on_item_changed)

    def set_all(state):
        _SIM_PANEL["bulk"] = True
        for i in range(lst.count()):
            lst.item(i).setCheckState(
                QtCore.Qt.Checked if state else QtCore.Qt.Unchecked)
        _SIM_PANEL["bulk"] = False
        rebuild_timeline()
    all_btn.clicked.connect(lambda: set_all(True))
    none_btn.clicked.connect(lambda: set_all(False))

    def on_slider(value):
        if _SIM_PANEL["syncing"]:
            return
        p = _SIM_PANEL.get("player")
        if p is None or p.total <= 0:
            return
        p.pause()
        try:
            p.seek(value / 1000.0 * p.total)
        except RuntimeError:
            status.setText("Document closed.")
    slider.valueChanged.connect(on_slider)

    def do_play():
        p = _SIM_PANEL.get("player")
        if p is not None:
            try:
                p.play()
            except RuntimeError:
                status.setText("Document closed.")
    play_btn.clicked.connect(do_play)
    pause_btn.clicked.connect(lambda: _SIM_PANEL.get("player") and
                              _SIM_PANEL["player"].pause())
    stop_btn.clicked.connect(lambda: _SIM_PANEL.get("player") and
                             _SIM_PANEL["player"].stop())

    def on_speed(idx):
        p = _SIM_PANEL.get("player")
        if p is not None:
            p.set_speed(speed.itemData(idx))
    speed.currentIndexChanged.connect(on_speed)

    def on_visibility(visible):
        if not visible:
            p = _SIM_PANEL.get("player")
            if p is not None:
                p.pause()
            _panel_restore_paths(_SIM_PANEL)   # tree gets its state back
    dock.visibilityChanged.connect(on_visibility)

    try:
        sim.simulate(doc)
    except Exception as e:  # noqa: BLE001
        QtWidgets.QMessageBox.critical(None, "LatheCAM simulation failed", str(e))
    player.seek(0.0)
    dock.show()
    try:
        Gui.SendMsgToActiveView("ViewFit")
    except Exception:
        pass
    return dock


def _sim_panel_rebind(panel, doc):
    """Point an open panel at another document (issue #8)."""
    ops = [o for o in doc.Objects if getattr(o, "LatheRole", "") == "op"]
    try:
        player = sim.SimPlayer(doc)
    except Exception:
        return                      # new doc has no valid program: keep old
    _panel_restore_paths(panel)     # old doc gets its tree state back
    old = panel.get("player")
    if old is not None:
        try:
            old.pause()
        except Exception:
            pass                      # old doc closed: nothing to pause
    panel["player"] = player
    panel["doc"] = doc
    panel["doc_name"] = doc.Name
    panel["bulk"] = True
    _fill_op_list(panel["list"], ops)
    panel["bulk"] = False
    _panel_snapshot_paths(panel)    # new doc: remember its tree state
    _panel_apply_paths(panel)       # checklist drives the new doc's display
    player.enable_continuous()      # issue #13
    player.on_position = panel.get("update_status")
    player.ensure_marker()
    try:
        sim.simulate(doc)
    except Exception:
        pass
    player.seek(0.0)


class CmdSimPanel:
    def GetResources(self):
        return {"MenuText": "Simulation panel\u2026",
                "ToolTip": "Open the simulation dock: play/pause/stop, "
                           "0.25x\u20138x speed, a scrubbable timeline and "
                           "per-op checkboxes (the plain Simulate button "
                           "stays the quick one-shot path)",
                "Pixmap": _icon("sim_panel")}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        _open_sim_panel(Gui.ActiveDocument.Document)


# ------------------------------------------------------ operation panel

_OP_PANEL = {}  # live dock + widgets (issue #16); empty when closed


def _face_desc(obj, sub):
    """'Face7 — sphere R6' style label for the assignment list."""
    leaf = str(sub).split(".")[-1]
    try:
        f = obj.getSubObject(sub)
    except Exception:
        return leaf
    if leaf.startswith("Edge"):
        try:
            kind = f.Curve.TypeId.split("::")[-1].replace("Geom", "").lower()
            return f"{leaf} \u2014 {kind}"
        except Exception:
            return leaf
    try:
        s = f.Surface
        t = getattr(s, "TypeId", "")
        if t == "Part::GeomCylinder":
            return f"{leaf} \u2014 \u00d8{2.0 * s.Radius:.4g} cylinder"
        if t == "Part::GeomCone":
            return f"{leaf} \u2014 cone"
        if t == "Part::GeomSphere":
            return f"{leaf} \u2014 sphere R{s.Radius:.4g}"
        if t == "Part::GeomPlane":
            return f"{leaf} \u2014 plane/shoulder"
        return f"{leaf} \u2014 {t.split('::')[-1].lower()}"
    except Exception:
        return leaf


def _op_panel_picks(panel):
    """Sub-names currently listed in the assignment list."""
    from PySide import QtCore
    lst = panel.get("list")
    out = []
    if lst is None:
        return out
    for i in range(lst.count()):
        out.append(str(lst.item(i).data(QtCore.Qt.UserRole)))
    return out


def _op_panel_set_picking(on):
    """Toggle 3D pick mode: every face/edge clicked joins the list."""
    panel = _OP_PANEL
    if not panel:
        return
    panel["picking"] = bool(on)
    btn = panel.get("pick")
    if btn is not None:
        btn.setText("\u2713 Done picking" if on else "Pick edges/faces")
    st = panel.get("status")
    if st is not None:
        st.setText("Pick mode: click faces/edges in the 3D view \u2014 they "
                   "are added to the list." if on else "\u2014")


def _op_panel_current_op():
    """The op the panel currently shows, or None (doc closed / op deleted)."""
    panel = _OP_PANEL
    if not panel or panel.get("op_name") is None:
        return None
    doc = panel.get("doc")
    if not _doc_alive(doc):
        return None
    op = doc.getObject(panel["op_name"])
    if op is None:
        _op_panel_reset()
    return op


def _tool_geo_text(tool):
    """One-line geometry readout for the panel (issue #17)."""
    if tool is None:
        return ""
    if tool.kind == "bore":
        bits = [f"bar \u00d8{tool.bar_diameter:g}"] if tool.bar_diameter else []
        if tool.bar_overhang:
            bits.append(f"\u00d7{tool.bar_overhang:g}")
        bits.append(f"R{tool.nose_radius:g}")
        return " \u00b7 ".join(bits)
    if tool.kind == "thread":
        return f"P{tool.thread_pitch:g} \u00b7 w{tool.width:g}"
    if tool.kind in ("part", "groove"):
        return f"w{tool.width:g} \u00b7 R{tool.nose_radius:g} \u00b7 z:{tool.z_reference}"
    return f"R{tool.nose_radius:g}"


def _op_panel_fill_tools(panel, op):
    """Populate the tool combo for this op type (issue #17).

    Tools whose kind matches the op sort first; the rest are dimmed but
    selectable. A broken user library (duplicate T#) degrades to the
    built-in library with a status warning instead of crashing.
    """
    from PySide import QtCore, QtGui
    from .program import load_tools
    from .model import Tool
    from .objects import KIND_FOR_OP
    sel = panel.get("tool")
    if sel is None:
        return
    want = KIND_FOR_OP.get(getattr(op, "OpType", ""), "turn")
    lib_err = ""
    try:
        tools = list(load_tools().values())
    except ValueError as e:
        lib_err = str(e)
        tools = list(Tool.default_library())
    tools.sort(key=lambda t: (t.kind != want, t.tool_number, t.name))
    sel.blockSignals(True)
    sel.clear()
    cur = str(getattr(op, "ToolName", ""))
    cur_idx = 0
    for i, t in enumerate(tools):
        sel.addItem(f"T{t.tool_number} {t.name}")
        sel.setItemData(i, t.name, QtCore.Qt.UserRole)
        if t.kind != want:
            sel.setItemData(i, QtGui.QColor("#909090"),
                            QtCore.Qt.ForegroundRole)
        if t.name == cur:
            cur_idx = i
    # current tool missing from the library: keep it visible at the top
    if cur and cur not in [t.name for t in tools]:
        sel.insertItem(0, f"{cur} (not in library)")
        sel.setItemData(0, cur, QtCore.Qt.UserRole)
        sel.setItemData(0, QtGui.QColor("#b3261e"), QtCore.Qt.ForegroundRole)
        cur_idx = 0
    sel.setCurrentIndex(cur_idx)
    sel.blockSignals(False)
    geo = panel.get("tool_geo")
    if geo is not None:
        by_name = {t.name: t for t in tools}
        geo.setText(_tool_geo_text(by_name.get(cur)))
    if lib_err:
        st = panel.get("status")
        if st is not None:
            st.setText(f"Tool library problem: {lib_err} \u2014 showing "
                       "built-in tools only.")


def _op_panel_tool_changed():
    """User picked another tool: write op.ToolName (issue #17)."""
    panel = _OP_PANEL
    sel = panel.get("tool")
    if sel is None or sel.currentIndex() < 0:
        return
    from PySide import QtCore
    name = sel.itemData(sel.currentIndex(), QtCore.Qt.UserRole)
    op = _op_panel_current_op()
    if op is None or not name or str(op.ToolName) == str(name):
        return
    doc = op.Document
    with _transaction(doc, f"Set tool {name}"):
        op.ToolName = name
    geo = panel.get("tool_geo")
    if geo is not None:
        try:
            from .program import load_tools
            geo.setText(_tool_geo_text(load_tools().get(name)))
        except ValueError:
            from .model import Tool
            geo.setText(_tool_geo_text(
                {t.name: t for t in Tool.default_library()}.get(name)))
    st = panel.get("status")
    if st is not None:
        st.setText(f"Tool set to {name} \u2014 press Apply to op to "
                   "regenerate the toolpath.")


def _op_panel_show(panel, op):
    """Rebind the panel's widgets to another op (ends any pick mode)."""
    from PySide import QtCore, QtWidgets
    _op_panel_set_picking(False)
    panel["op_name"] = op.Name
    panel["doc"] = op.Document
    panel["doc_name"] = op.Document.Name
    panel["src_obj"] = None
    head = panel.get("head")
    if head is not None:
        head.setText(f"<b>{op.Label}</b> &nbsp;\u00b7&nbsp; type "
                     f"<b>{getattr(op, 'OpType', '')}</b>")
    _op_panel_fill_tools(panel, op)
    prof = str(getattr(op, "ProfileString", "") or "")
    pre = panel.get("prof")
    if pre is not None:
        if getattr(op, "OpType", "") == "part":
            pre.setPlainText("(part-off: cuts at the bore diameter \u2014 "
                             "no profile needed)")
        else:
            pre.setPlainText(prof if prof
                             else "(no profile \u2014 straight cylinder cut)")
    warn = panel.get("warn")
    if warn is not None:
        warn.setVisible(getattr(op, "OpType", "") in PROFILE_OPS and not prof)
    lst = panel.get("list")
    if lst is not None:
        lst.clear()
        src = objects.source_faces(op)
        if src is not None:
            part, faces = src
            panel["src_obj"] = part
            for f in faces:
                it = QtWidgets.QListWidgetItem(_face_desc(part, f))
                it.setData(QtCore.Qt.UserRole, f)
                lst.addItem(it)
    st = panel.get("status")
    if st is not None:
        st.setText("\u2014")


def _op_panel_reset():
    """The shown op vanished: blank the panel instead of pointing at a ghost."""
    panel = _OP_PANEL
    if not panel:
        return
    panel["op_name"] = None
    panel["src_obj"] = None
    _op_panel_set_picking(False)
    for key in ("head", "warn", "status"):
        w = panel.get(key)
        if w is not None:
            try:
                w.setText("<i>Operation deleted</i>" if key == "head"
                          else ("" if key == "warn" else "\u2014"))
            except Exception:
                pass
    pre = panel.get("prof")
    if pre is not None:
        try:
            pre.setPlainText("")
        except Exception:
            pass
    lst = panel.get("list")
    if lst is not None:
        try:
            lst.clear()
        except Exception:
            pass


def _op_panel_add_picks(obj, subs):
    """Pick-mode sink: add clicked Face*/Edge* sub-names to the list."""
    panel = _OP_PANEL
    if not panel or not panel.get("picking"):
        return
    from PySide import QtCore, QtWidgets
    lst = panel.get("list")
    if lst is None or obj is None:
        return
    have = set(_op_panel_picks(panel))
    added = []
    for sub in subs or ():
        leaf = str(sub).split(".")[-1]
        if not (leaf.startswith("Face") or leaf.startswith("Edge")):
            continue
        if (panel.get("src_obj") is not None
                and panel["src_obj"].Name != obj.Name):
            panel["status"].setText(
                "All picks must come from the same part object.")
            continue
        if sub in have:
            continue
        it = QtWidgets.QListWidgetItem(_face_desc(obj, sub))
        it.setData(QtCore.Qt.UserRole, sub)
        lst.addItem(it)
        added.append(sub)
    if added:
        panel["src_obj"] = obj
        panel["status"].setText(
            f"Added {len(added)} pick(s); click more or press Apply.")


def _op_panel_apply():
    """Write the list back onto the op: SourceFace + ProfileString."""
    panel = _OP_PANEL
    from PySide import QtWidgets
    if not panel or panel.get("op_name") is None:
        return
    doc = panel.get("doc")
    if not _doc_alive(doc):
        QtWidgets.QMessageBox.warning(None, "LatheCAM", "Document closed.")
        return
    op = doc.getObject(panel["op_name"])
    if op is None:
        _op_panel_reset()
        return
    subs = _op_panel_picks(panel)
    if not subs:
        panel["status"].setText(
            "Nothing assigned \u2014 use Pick edges/faces first.")
        return
    obj = panel.get("src_obj")
    if obj is None:
        panel["status"].setText("Pick mode lost its part object \u2014 "
                                "pick again.")
        return
    setup = objects.collect_program(doc)[0]
    try:
        text = None
        if getattr(op, "OpType", "") in PROFILE_OPS:
            segs = profile_extract.segments(
                profile_extract.lathe_shape(obj, setup), subs)
            text = profile_extract.profile_from_segments(segs)
        with _transaction(doc, "Operation panel apply"):
            try:
                op.SourceFace = (obj, subs)
            except Exception:
                pass
            if text:
                op.ProfileString = text
                if hasattr(op, "ZEnd"):
                    op.ZEnd = profile_extract.profile_z_end(text)
        doc.recompute()
        # the user just edited this op \u2014 make sure its result is visible:
        # un-hide its toolpaths (the sim checklist may have hidden them)
        setup2 = objects.collect_program(doc)[0]
        if setup2 is not None and "HiddenVisPaths" in setup2.PropertiesList:
            hidden = [str(x) for x in (setup2.HiddenVisPaths or [])]
            kept = [x for x in hidden if not x.startswith(op.Label + " \u00b7 ")]
            if len(kept) != len(hidden):
                setup2.HiddenVisPaths = sorted(kept)
        for v in doc.Objects:
            if getattr(v, "LatheRole", "") == "vis" \
                    and getattr(v, "LatheOp", "") == op.Label:
                vo = getattr(v, "ViewObject", None)
                if vo is not None:
                    try:
                        vo.Visibility = True
                    except Exception:
                        pass
    except ValueError as e:
        QtWidgets.QMessageBox.critical(None, "LatheCAM profile", str(e))
        return
    _op_panel_set_picking(False)
    _op_panel_show(panel, op)
    FreeCAD.Console.PrintStatus(
        f"LatheCAM: {op.Label} geometry updated from {len(subs)} pick(s).\n")


def _delete_ops(doc, names):
    """Remove ops + their drawn toolpaths; the part is never touched."""
    labels = {doc.getObject(n).Label for n in names
              if doc.getObject(n) is not None}
    with _transaction(doc, "Delete operation"):
        for o in list(doc.Objects):
            if getattr(o, "LatheRole", "") == "vis" \
                    and getattr(o, "LatheOp", "") in labels:
                doc.removeObject(o.Name)
        for o in list(doc.Objects):
            if o.TypeId == "App::DocumentObjectGroup" \
                    and o.Label in labels and o.Name.startswith("Vis_Op_"):
                doc.removeObject(o.Name)
        for n in names:
            if doc.getObject(n) is not None:
                doc.removeObject(n)
    if _OP_PANEL.get("op_name") in names:
        _op_panel_reset()
    if _SIM_PANEL.get("doc") is doc and _SIM_PANEL.get("list") is not None:
        _SIM_PANEL["bulk"] = True
        _fill_op_list(_SIM_PANEL["list"],
                      [o for o in doc.Objects
                       if getattr(o, "LatheRole", "") == "op"])
        _SIM_PANEL["bulk"] = False
    refresh_if_changed(doc, force=True)


def _op_panel_pick_names(docname, objname, sub):
    """Route a selection-callback pick into the panel while picking.

    The callback's ``sub`` is empty for 3D-view clicks (the framework only
    passes sub-names for tree picks), so pull the real sub-element names
    from the selection itself — same source 'Profile from selection' uses.
    """
    if not _OP_PANEL.get("picking"):
        return
    try:
        obj = FreeCAD.getDocument(docname).getObject(objname)
    except Exception:
        return
    if obj is None:
        return
    for s in Gui.Selection.getSelectionEx():
        if s.Object is None or s.Object.Name != objname:
            continue
        subs = [x for x in (s.SubElementNames or [])
                if x.split(".")[-1][:4] in ("Face", "Edge")]
        if subs:
            _op_panel_add_picks(obj, subs)


def _op_panel_open_soon(op):
    """Raise/rebind the panel for op, deferred out of the selection callback."""
    if _OP_PANEL.get("op_name") == op.Name and _OP_PANEL.get("dock") is not None:
        return
    if not getattr(FreeCAD, "GuiUp", False):
        return
    try:
        from PySide import QtCore
        QtCore.QTimer.singleShot(0, lambda: _open_op_panel(op))
    except Exception:
        pass


def _open_op_panel(op):
    """Create or raise the operation dock (issue #16).

    Shows the op's profile and assigned faces/edges; Pick mode collects
    3D clicks into the list; Apply writes SourceFace + ProfileString back
    onto the op. Clicking an op in the tree opens it too, so geometry can
    be fixed after the op was created with a wrong (or missing) selection.
    """
    from PySide import QtCore, QtWidgets
    try:
        op.Document.Name
    except Exception:
        return                       # document vanished (deferred open)
    existing = _OP_PANEL.get("dock")
    if existing is not None:
        try:
            existing.show()
            existing.raise_()
            _op_panel_show(_OP_PANEL, op)
            return existing
        except RuntimeError:
            _OP_PANEL.clear()        # C++ side deleted (session teardown)

    main = Gui.getMainWindow()
    dock = QtWidgets.QDockWidget("LatheCAM \u2014 Operation", main)
    dock.setObjectName("LatheCAM_OpPanel")
    dock.setAllowedAreas(QtCore.Qt.AllDockWidgetAreas)
    body = QtWidgets.QWidget(dock)
    dock.setWidget(body)
    main.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)
    v = QtWidgets.QVBoxLayout(body)
    v.setContentsMargins(8, 8, 8, 8)
    v.setSpacing(6)

    head = QtWidgets.QLabel("\u2014")
    head.setTextFormat(QtCore.Qt.RichText)
    v.addWidget(head)

    # Tool selector (issue #17): per-op tool choice, filtered by op type.
    trow = QtWidgets.QHBoxLayout()
    trow.addWidget(QtWidgets.QLabel("Tool:"))
    tool_sel = QtWidgets.QComboBox()
    tool_sel.setToolTip(
        "Cutting tool for this operation. Tools that suit the op type are "
        "listed first; others are dimmed but still selectable.")
    trow.addWidget(tool_sel, 1)
    tool_geo = QtWidgets.QLabel("")
    tool_geo.setStyleSheet("color:#888;")
    trow.addWidget(tool_geo)
    v.addLayout(trow)

    warn = QtWidgets.QLabel(
        "No profile: this op cuts a straight cylinder. Pick the real "
        "faces/edges to make the tool follow the form.")
    warn.setWordWrap(True)
    warn.setStyleSheet("background:#fff3cd;color:#7a5b00;padding:4px 6px;"
                       "border:1px solid #e0c96f;border-radius:3px;")
    warn.hide()
    v.addWidget(warn)

    v.addWidget(QtWidgets.QLabel("Current profile:"))
    prof = QtWidgets.QPlainTextEdit(body)
    prof.setReadOnly(True)
    prof.setMaximumHeight(56)
    v.addWidget(prof)

    v.addWidget(QtWidgets.QLabel("Assigned geometry:"))
    lst = QtWidgets.QListWidget(body)
    lst.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
    lst.setMaximumHeight(120)
    v.addWidget(lst)

    row = QtWidgets.QHBoxLayout()
    pick_btn = QtWidgets.QPushButton("Pick edges/faces")
    rm_btn = QtWidgets.QPushButton("Remove selected")
    row.addWidget(pick_btn)
    row.addWidget(rm_btn)
    row.addStretch(1)
    v.addLayout(row)

    apply_btn = QtWidgets.QPushButton("Apply to op")
    v.addWidget(apply_btn)

    del_btn = QtWidgets.QPushButton("Delete operation")
    del_btn.setStyleSheet("color:#b3261e;")
    v.addWidget(del_btn)

    status = QtWidgets.QLabel("\u2014")
    status.setWordWrap(True)
    status.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    v.addWidget(status)

    _OP_PANEL.clear()
    _OP_PANEL.update({"dock": dock, "doc": op.Document,
                      "doc_name": op.Document.Name, "op_name": None,
                      "head": head, "warn": warn, "prof": prof,
                      "list": lst, "pick": pick_btn, "rm": rm_btn,
                      "apply": apply_btn, "delete": del_btn,
                      "status": status, "picking": False, "src_obj": None,
                      "tool": tool_sel, "tool_geo": tool_geo})

    def on_tool(idx):
        _op_panel_tool_changed()
    tool_sel.currentIndexChanged.connect(on_tool)

    def on_pick():
        _op_panel_set_picking(not _OP_PANEL.get("picking"))
    pick_btn.clicked.connect(on_pick)

    def on_remove():
        panel = _OP_PANEL
        lstw = panel.get("list")
        if lstw is None:
            return
        for it in lstw.selectedItems():
            lstw.takeItem(lstw.row(it))
        panel["status"].setText("Removed; press Apply to store the change.")
    rm_btn.clicked.connect(on_remove)

    apply_btn.clicked.connect(_op_panel_apply)

    def on_delete():
        panel = _OP_PANEL
        doc = panel.get("doc")
        name = panel.get("op_name")
        if not _doc_alive(doc) or not name or doc.getObject(name) is None:
            return
        _delete_ops(doc, [name])
    del_btn.clicked.connect(on_delete)

    def on_visibility(visible):
        if not visible:
            _op_panel_set_picking(False)
    dock.visibilityChanged.connect(on_visibility)

    _op_panel_show(_OP_PANEL, op)
    dock.show()
    return dock


class CmdOpPanel:
    def GetResources(self):
        return {"MenuText": "Operation panel\u2026",
                "ToolTip": "Open the operation dock: shows the op's profile "
                           "and assigned faces/edges, lets you pick more "
                           "geometry in the 3D view and apply it to the op "
                           "(clicking an op in the tree opens it too)",
                "Pixmap": _icon("op_panel")}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        from PySide import QtWidgets
        doc = Gui.ActiveDocument.Document
        ops = [o for o in doc.Objects if getattr(o, "LatheRole", "") == "op"]
        if not ops:
            QtWidgets.QMessageBox.information(
                None, "LatheCAM", "No LatheCAM operations in document.")
            return
        target = None
        for s in Gui.Selection.getSelectionEx():
            if s.Object is not None \
                    and getattr(s.Object, "LatheRole", "") == "op":
                target = s.Object
                break
        _open_op_panel(target or ops[0])


class CmdDeleteOp:
    """Safe op removal: ignores the 3D selection entirely (issue #16).

    The Delete key deletes whatever is selected anywhere \u2014 a picked face
    on the part means the whole body goes and every op's dependency dialog
    pops. This command only ever removes the op(s) selected in the tree,
    plus their drawn toolpaths.
    """

    def GetResources(self):
        return {"MenuText": "Delete operation",
                "ToolTip": "Remove the selected operation(s) and their "
                           "toolpaths \u2014 never the part, whatever is "
                           "selected in the 3D view",
                "Pixmap": _icon("delete_op")}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        from PySide import QtWidgets
        doc = Gui.ActiveDocument.Document
        ops = [s.Object for s in Gui.Selection.getSelectionEx()
               if s.Object is not None
               and getattr(s.Object, "LatheRole", "") == "op"]
        if not ops:
            QtWidgets.QMessageBox.information(
                None, "LatheCAM",
                "Select the operation(s) to delete in the tree first.")
            return
        _delete_ops(doc, [o.Name for o in ops])


class CmdSimClear:
    """Remove the simulation leftovers (Sim_ group, part, tool)."""

    def GetResources(self):
        return {"MenuText": "Clear simulation",
                "ToolTip": "Delete the simulation objects (Sim_Simulation, "
                           "Sim_part, Sim_tool). The toolpath drawings and "
                           "your operations are untouched",
                "Pixmap": _icon("sim")}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        doc = Gui.ActiveDocument.Document
        sims = [o for o in doc.Objects if o.Name.startswith("Sim_")]
        if not sims:
            return
        with _transaction(doc, "Clear simulation"):
            for o in list(doc.Objects):
                if o.TypeId == "App::DocumentObjectGroup" \
                        and o.Name.startswith("Sim_"):
                    for m in list(o.Group):
                        try:
                            o.Group = [x for x in o.Group
                                       if x.Name != m.Name]
                        except Exception:
                            pass
            for o in sims:
                if doc.getObject(o.Name) is not None:
                    doc.removeObject(o.Name)
        doc.recompute()
        FreeCAD.Console.PrintStatus(
            f"LatheCAM: removed {len(sims)} simulation object(s).\n")


def make_op_command(op_type):
    # FreeCAD 1.1 calls methods on whatever object addCommand receives:
    # pass an INSTANCE, not a class (a class gives unbound functions ->
    # "GetResources() missing 1 required positional argument: 'self'").
    return type(f"CmdOp_{op_type}", (CmdOp,), {"op_type": op_type})()


# issue #14: insert-profile fields carried alongside the table rows
_INS_KEYS = ("iso_code", "insert_shape", "insert_ic", "insert_thickness",
             "outer_radius", "lead_angle", "approach_angle", "relief_angle",
             "bar_diameter", "bar_overhang", "min_bore_d", "nose_radius")


def _tool_profile(t):
    """Insert-profile fields of a tool (or dummy row) as a JSON dict."""
    if t is None:
        return {}
    out = {}
    for k in _INS_KEYS:
        v = getattr(t, k, "")
        out[k] = v if isinstance(v, str) else float(v or 0)
    return out


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


class _ProfilePreview(QtWidgets.QWidget):
    """Live insert preview (issue #14): top view for OD, hole cross-
    section for boring — the mockup's right-hand panel."""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.ed = editor
        self.setFixedSize(340, 340)

    def paintEvent(self, evt):  # noqa: N802 (Qt naming)
        from PySide import QtGui
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        ed = self.ed
        ed.refresh()
        cx, cy = 170.0, 170.0
        if ed.mode == "bore":
            r_bar = ed.bar_d / 2.0
            r_bore = ed.bore_d / 2.0 if ed.bore_d > 0 else 0.0
            reach = max(r_bar + ed.outer_r, r_bore, 1.0)
            sc = 140.0 / reach
            p.setPen(QtGui.QPen(QtGui.QColor(0x5b, 0x70, 0x83), 2))
            p.setBrush(QtGui.QColor(0xdb, 0xe4, 0xec))
            if r_bore > 0:
                p.drawEllipse(QtCore.QPointF(cx, cy), r_bore * sc,
                               r_bore * sc)
            p.setBrush(QtGui.QColor(0x9f, 0xb2, 0xc4))
            p.drawEllipse(QtCore.QPointF(cx, cy), r_bar * sc, r_bar * sc)
            # insert sits on the bar: nose at +radial (up), body hangs down
            poly = [QtCore.QPointF(cx - y * sc, cy - (r_bar + x * sc) * sc)
                    for x, y in ed.polygon]
            p.setBrush(QtGui.QColor(0xe8, 0xa3, 0x3d))
            p.setPen(QtGui.QPen(QtGui.QColor(0x7a, 0x4d, 0x0f), 2))
            p.drawPolygon(QtGui.QPolygonF(poly))
            rr = (r_bar + ed.outer_r) * sc
            pen = QtGui.QPen(QtGui.QColor(0xb3, 0x26, 0x1e), 1.5)
            pen.setDashPattern([4, 3])
            p.setPen(pen)
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawEllipse(QtCore.QPointF(cx, cy), rr, rr)
            fits = ed.bore_d <= 0 or ed.min_bore_d <= ed.bore_d + 1e-9
            p.setPen(QtGui.QColor(0x2e, 0x7d, 0x32) if fits
                     else QtGui.QColor(0xb3, 0x26, 0x1e))
            p.drawText(QtCore.QRectF(0, 4, 340, 16), QtCore.Qt.AlignHCenter,
                       f"min bore \u00d8{ed.min_bore_d:.1f} mm"
                       + ("" if ed.bore_d <= 0 else
                          (f" \u2014 fits \u00d8{ed.bore_d:g}"
                           if fits else f" \u2014 DOES NOT FIT \u00d8{ed.bore_d:g}")))
            p.setPen(QtGui.QColor(0x55, 0x55, 0x55))
            p.drawText(QtCore.QRectF(0, 322, 340, 16), QtCore.Qt.AlignHCenter,
                       f"bar \u00d8{ed.bar_d:g} mm \u00b7 overhang "
                       f"{ed.overhang:g} mm \u00b7 current bore "
                       f"\u00d8{ed.bore_d:g} mm")
        else:
            rw = 15.0  # preview stock \u00d830, like the mockup
            reach = rw + ed.outer_r
            sc = 140.0 / reach
            p.setPen(QtGui.QPen(QtGui.QColor(0x5b, 0x70, 0x83), 2))
            p.setBrush(QtGui.QColor(0xdb, 0xe4, 0xec))
            p.drawEllipse(QtCore.QPointF(cx, cy), rw * sc, rw * sc)
            rr = (rw + ed.outer_r) * sc
            pen = QtGui.QPen(QtGui.QColor(0xb3, 0x26, 0x1e), 1.5)
            pen.setDashPattern([4, 3])
            p.setPen(pen)
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawEllipse(QtCore.QPointF(cx, cy), rr, rr)
            poly = [QtCore.QPointF(cx + (rw + x) * sc, cy - y * sc)
                    for x, y in ed.polygon]
            p.setBrush(QtGui.QColor(0xe8, 0xa3, 0x3d))
            p.setPen(QtGui.QPen(QtGui.QColor(0x7a, 0x4d, 0x0f), 2))
            p.drawPolygon(QtGui.QPolygonF(poly))
            if ed.re > 0:
                nose = min(ed.polygon, key=lambda q: q[0])
                p.setBrush(QtGui.QColor(0xff, 0xff, 0xff))
                p.setPen(QtGui.QPen(QtGui.QColor(0xb3, 0x26, 0x1e), 1.5))
                p.drawEllipse(QtCore.QPointF(cx + (rw + nose[0]) * sc,
                                             cy - nose[1] * sc),
                              max(ed.re * sc, 2.0), max(ed.re * sc, 2.0))
            p.setPen(QtGui.QColor(0x55, 0x55, 0x55))
            p.drawText(QtCore.QRectF(0, 322, 340, 16), QtCore.Qt.AlignHCenter,
                       f"stock \u00d8{rw * 2:g} \u00b7 sweep \u00d8"
                       f"{(rw + ed.outer_r) * 2:.1f} \u00b7 "
                       f"\u03bb{ed.lead:+g}\u00b0 \u00b7 "
                       f"\u03ba{ed.approach:g}\u00b7 relief "
                       f"{ed.relief:g}\u00b0")
        p.end()


class ProfileEditorDialog(QtWidgets.QDialog):
    """Tool Profile editor (issue #14): ISO quick-fill, shape gallery,
    dimension selects, lead-angle slider, live preview. OK exposes the
    resulting Tool insert fields via fields()."""

    def __init__(self, tool, parent=None):
        super().__init__(parent)
        from PySide import QtCore
        from . import inserts
        self.inserts = inserts
        self.setWindowTitle(f"Tool Profile \u2014 T{tool.tool_number} \u00b7 "
                            f"{tool.name}")
        self.mode = "bore" if tool.kind == "bore" else "od"
        self.letters = None          # (relief, tol, cb) from the last parse
        self.codes = {}              # ic/thk/re codes from the last parse
        self.shape = tool.insert_shape or ""
        self.ic = tool.insert_ic or 6.35
        self.thk = tool.insert_thickness or 2.38
        self.re = tool.nose_radius or 0.4
        self.relief = tool.relief_angle or 7.0
        self.lead = tool.lead_angle or 0.0
        self.bar_d = tool.bar_diameter or 10.0
        self.overhang = tool.bar_overhang or 40.0
        self.bore_d = 0.0
        self.outer_r = 0.0
        self.approach = 0.0
        self.min_bore_d = 0.0
        self.polygon = []
        self._fields = None

        lay = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        lay.addLayout(row)
        row.addLayout(left)
        self.preview = _ProfilePreview(self)
        row.addWidget(self.preview)

        # ISO quick-fill
        iso_box = QtWidgets.QGroupBox("ISO code (quick-fill)")
        iv = QtWidgets.QVBoxLayout(iso_box)
        ir = QtWidgets.QHBoxLayout()
        iv.addLayout(ir)
        self.iso_edit = QtWidgets.QLineEdit(tool.iso_code or "")
        self.iso_edit.setPlaceholderText("CCMT060208")
        self.iso_edit.returnPressed.connect(self._on_parse)
        b_parse = QtWidgets.QPushButton("Parse")
        b_parse.clicked.connect(self._on_parse)
        ir.addWidget(self.iso_edit)
        ir.addWidget(b_parse)
        self.anatomy = QtWidgets.QLabel("")
        iv.addWidget(self.anatomy)
        left.addWidget(iso_box)

        # shape gallery
        gal_box = QtWidgets.QGroupBox("Insert shape")
        grid = QtWidgets.QGridLayout(gal_box)
        self._shape_btns = {}
        for i, letter in enumerate(inserts.SHAPES):
            b = QtWidgets.QToolButton()
            b.setIcon(self._glyph(letter))
            b.setIconSize(QtCore.QSize(40, 30))
            b.setToolTip(inserts.SHAPES[letter][4])
            b.setCheckable(True)
            b.setChecked(letter == self.shape)
            b.clicked.connect(lambda _=False, L=letter: self._pick_shape(L))
            grid.addWidget(b, i // 4, i % 4)
            self._shape_btns[letter] = b
        left.addWidget(gal_box)

        # dimensions
        dim_box = QtWidgets.QGroupBox("Insert dimensions (mm)")
        dv = QtWidgets.QVBoxLayout(dim_box)
        self.ic_combo = self._dim_select(dv, "Inscribed \u00d8 (IC)",
                                         inserts.IC_MM, self.ic)
        self.thk_combo = self._dim_select(dv, "Thickness",
                                          inserts.THK_MM, self.thk)
        self.re_combo = self._dim_select(dv, "Nose R", inserts.RE_MM, self.re)
        self.out_label = QtWidgets.QLabel("")
        dv.addWidget(self.out_label)
        left.addWidget(dim_box)

        # angles
        ang_box = QtWidgets.QGroupBox("Angles")
        av = QtWidgets.QVBoxLayout(ang_box)
        lr = QtWidgets.QHBoxLayout()
        self.lead_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.lead_slider.setRange(-15, 95)
        self.lead_slider.setValue(int(round(self.lead)))
        self.lead_val = QtWidgets.QLabel(f"{self.lead:g}\u00b0")
        self.lead_slider.valueChanged.connect(self._on_lead)
        lr.addWidget(QtWidgets.QLabel("Side cutting edge \u03bb\u209b"))
        lr.addWidget(self.lead_slider)
        lr.addWidget(self.lead_val)
        av.addLayout(lr)
        self.appr_label = QtWidgets.QLabel("")
        self.relief_label = QtWidgets.QLabel("")
        av.addWidget(self.appr_label)
        av.addWidget(self.relief_label)
        left.addWidget(ang_box)

        # boring bar
        bore_box = QtWidgets.QGroupBox("Boring bar")
        bv = QtWidgets.QVBoxLayout(bore_box)
        self.bar_edit = QtWidgets.QLineEdit(f"{self.bar_d:g}")
        self.ovh_edit = QtWidgets.QLineEdit(f"{self.overhang:g}")
        self.bore_edit = QtWidgets.QLineEdit("")
        self.minbore_label = QtWidgets.QLabel("")
        for lbl, w in (("Bar \u00d8 (mm)", self.bar_edit),
                       ("Overhang (mm)", self.ovh_edit),
                       ("Current bore \u00d8 (mm)", self.bore_edit)):
            r = QtWidgets.QHBoxLayout()
            r.addWidget(QtWidgets.QLabel(lbl))
            r.addWidget(w)
            bv.addLayout(r)
            w.textChanged.connect(self._on_text)
        bv.addWidget(self.minbore_label)
        self.bore_box = bore_box
        bore_box.setVisible(self.mode == "bore")
        left.addWidget(bore_box)
        left.addStretch(1)

        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            parent=self)
        bb.accepted.connect(self._on_ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

        if tool.iso_code:
            p = inserts.parse_iso(tool.iso_code)
            if p:
                self.letters = (p["relief_letter"], p["tol_letter"],
                                p["cb_letter"])
                self.codes = {"ic_code": p["ic_code"],
                              "thk_code": p["thk_code"],
                              "re_code": p["re_code"]}
                self._show_anatomy(p["code"])
        self.refresh()

    # ---- widget helpers -----------------------------------------------

    @staticmethod
    def _glyph(letter):
        """Small shape polygon icon for the gallery (drawn, no assets)."""
        from PySide import QtGui, QtCore
        from . import inserts
        pm = QtGui.QPixmap(40, 30)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        n, _tip, _f, _rel, _name = inserts.SHAPES[letter]
        p.setBrush(QtGui.QColor(0xcf, 0xd8, 0xdc))
        p.setPen(QtGui.QPen(QtGui.QColor(0x45, 0x5a, 0x64), 2))
        if n == 0:
            p.drawEllipse(QtCore.QRectF(8, 3, 24, 24))
        else:
            pts = [QtCore.QPointF(20 + 11 * math.cos(-math.pi / 2
                                                     + i * 2 * math.pi / n),
                                  15 + 11 * math.sin(-math.pi / 2
                                                     + i * 2 * math.pi / n))
                   for i in range(n)]
            p.drawPolygon(QtGui.QPolygonF(pts))
        p.end()
        return QtGui.QIcon(pm)

    def _dim_select(self, layout, label, table, current):
        r = QtWidgets.QHBoxLayout()
        r.addWidget(QtWidgets.QLabel(label))
        cb = QtWidgets.QComboBox()
        best = None
        for code, mm in table.items():
            cb.addItem(f"{code} \u2192 {mm:g} mm", mm)
            if abs(mm - current) < 1e-6:
                best = cb.count() - 1
        cb.setCurrentIndex(best if best is not None else -1)
        cb.currentIndexChanged.connect(self._on_dim)
        r.addWidget(cb)
        r.addStretch(1)
        layout.addLayout(r)
        return cb

    @staticmethod
    def _combo_mm(cb):
        try:
            v = cb.currentData()
            return float(v.toDouble()[0]) if v is not None else 0.0
        except AttributeError:
            return float(v) if v is not None else 0.0

    # ---- state sync ----------------------------------------------------

    def refresh(self):
        """Recompute derived values + labels from the control state."""
        from . import inserts
        self.bar_d = self._f(self.bar_edit, 10.0)
        self.overhang = self._f(self.ovh_edit, 0.0)
        self.bore_d = self._f(self.bore_edit, 0.0)
        if self.shape:
            self.polygon = inserts.insert_polygon(self.shape, self.ic,
                                                  self.lead)
            self.outer_r = inserts.outer_radius(self.shape, self.ic,
                                                self.re, self.lead)
            self.approach = inserts.approach_angle(self.shape, self.lead)
        else:
            self.polygon, self.outer_r, self.approach = [], 0.0, 0.0
        self.min_bore_d = (inserts.min_bore_d(self.bar_d, self.outer_r)
                           if self.mode == "bore" and self.bar_d > 0
                           and self.shape else 0.0)
        self.out_label.setText(f"Outer radius (max): "
                               f"\u00d8{self.outer_r * 2:.2f} mm beyond nose")
        self.appr_label.setText(f"Approach \u03ba\u1d63: "
                                f"{self.approach:g}\u00b0 (computed)")
        self.relief_label.setText(f"Relief: {self.relief:g}\u00b0 "
                                  f"(from ISO letter)")
        self.minbore_label.setText(
            f"Min bore \u00d8: {self.min_bore_d:.1f} mm \u2014 the bar must "
            f"fit the hole!")
        self.update()

    def _f(self, edit, default):
        try:
            return float(edit.text())
        except (TypeError, ValueError):
            return default

    def _on_parse(self):
        if not self.parse_iso(self.iso_edit.text()):
            QtWidgets.QMessageBox.warning(
                self, "Unknown code",
                "Not a recognised ISO 1832 code (try CCMT060208, "
                "DCMT070204, VCMT110304)")

    def parse_iso(self, code):
        """Parse an ISO code into the controls. Public: the offscreen
        harness drives the editor through this. Returns True on success."""
        p = self.inserts.parse_iso(code)
        if p is None:
            return False
        self.shape = p["insert_shape"]
        self.ic = p["insert_ic"]
        self.thk = p["insert_thickness"]
        self.re = p["nose_radius"]
        self.relief = p["relief_angle"]
        self.letters = (p["relief_letter"], p["tol_letter"], p["cb_letter"])
        self.codes = {"ic_code": p["ic_code"], "thk_code": p["thk_code"],
                      "re_code": p["re_code"]}
        self.iso_edit.setText(p["code"])
        self._show_anatomy(p["code"])
        self._sync_combos()
        self._sync_gallery()
        self.refresh()
        return True

    def _show_anatomy(self, code):
        self.anatomy.setTextFormat(QtCore.Qt.RichText)
        cols = ["#b3261e", "#c77700", "#1565c0", "#6a1b9a", "#2e7d32"]
        spans = [code[:1], code[1:2], code[2:4], code[4:6], code[6:8]]
        self.anatomy.setText(
            "".join(f'<span style="color:{c};font-size:16pt">{s}</span>'
                    for c, s in zip(cols, spans))
            + '<span style="color:#666"> \u00b7 shape/relief/tol/CB '
              '\u00b7 IC/thk/nose-R</span>')

    def _sync_combos(self):
        for cb, val in ((self.ic_combo, self.ic), (self.thk_combo, self.thk),
                        (self.re_combo, self.re)):
            for i in range(cb.count()):
                try:
                    mm = float(cb.itemData(i).toDouble()[0])
                except AttributeError:
                    mm = float(cb.itemData(i))
                if abs(mm - val) < 1e-6:
                    cb.blockSignals(True)
                    cb.setCurrentIndex(i)
                    cb.blockSignals(False)
                    break

    def _sync_gallery(self):
        for letter, b in self._shape_btns.items():
            b.blockSignals(True)
            b.setChecked(letter == self.shape)
            b.blockSignals(False)

    def _pick_shape(self, letter):
        self.shape = letter
        self.relief = self.inserts.SHAPES[letter][3]
        self._sync_gallery()
        self._regen_code()
        self.refresh()

    def _on_lead(self, val):
        self.lead = float(val)
        self.lead_val.setText(f"{self.lead:g}\u00b0")
        self.refresh()

    def _on_dim(self):
        self.ic = self._combo_mm(self.ic_combo)
        self.thk = self._combo_mm(self.thk_combo)
        self.re = self._combo_mm(self.re_combo)
        # keep the code letters the user just picked (e.g. E3, not 03)
        self.codes = {"ic_code": self.ic_combo.currentText().split(" ")[0],
                      "thk_code": self.thk_combo.currentText().split(" ")[0],
                      "re_code": self.re_combo.currentText().split(" ")[0]}
        self._regen_code()
        self.refresh()

    def _on_text(self):
        self.refresh()

    def _regen_code(self):
        """Keep the ISO code in step with shape/dimension edits."""
        if not self.shape or self.letters is None:
            return
        code = self.inserts.iso_code(self.shape, self.letters[0],
                                     self.letters[1], self.letters[2],
                                     self.ic, self.thk, self.re,
                                     **self.codes)
        if code:
            self.iso_edit.blockSignals(True)
            self.iso_edit.setText(code)
            self.iso_edit.blockSignals(False)
            self._show_anatomy(code)

    # ---- result ---------------------------------------------------------

    def fields(self):
        """The insert fields this dialog produced (empty when no shape)."""
        self.refresh()
        if not self.shape:
            return {"iso_code": "", "insert_shape": "", "insert_ic": 0.0,
                    "insert_thickness": 0.0, "outer_radius": 0.0,
                    "lead_angle": 0.0, "approach_angle": 0.0,
                    "relief_angle": 0.0, "bar_diameter": 0.0,
                    "bar_overhang": 0.0, "min_bore_d": 0.0}
        return {"iso_code": self.iso_edit.text().strip().upper(),
                "insert_shape": self.shape,
                "insert_ic": self.ic, "insert_thickness": self.thk,
                "nose_radius": self.re,
                "outer_radius": self.outer_r,
                "lead_angle": self.lead,
                "approach_angle": self.approach,
                "relief_angle": self.relief,
                "bar_diameter": self.bar_d if self.mode == "bore" else 0.0,
                "bar_overhang": self.overhang if self.mode == "bore" else 0.0,
                "min_bore_d": self.min_bore_d}

    def _on_ok(self):
        self._fields = self.fields()
        self.accept()


class CmdToolLibrary:
    """Edit the per-user tool library (names, T numbers, geometry, feeds)."""

    def GetResources(self):
        return {"MenuText": "Tool Library",
                "ToolTip": "Edit tool names, T numbers and cutting data "
                           "(saved to your user tools.json)",
                "Pixmap": _icon("tools")}

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
            # issue #14: carry the insert-profile fields on the Name cell
            # (survives row add/remove) as JSON in UserRole
            it = tbl.item(row, 0)
            if it is not None:
                it.setData(QtCore.Qt.UserRole, json.dumps(_tool_profile(t)))

        def read_row(r):
            """Current table values of row r as a tool dict (no profile)."""
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
            return rec

        def read_profile(r):
            it = tbl.item(r, 0)
            raw = it.data(QtCore.Qt.UserRole) if it is not None else None
            try:
                return json.loads(str(raw)) if raw else {}
            except ValueError:
                return {}

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

        def edit_profile():
            """Open the Tool Profile editor (issue #14) for the selected row."""
            from .model import Tool
            r = tbl.currentRow()
            if r < 0:
                QtWidgets.QMessageBox.information(
                    dlg, "Tool profile", "Select a tool row first.")
                return
            rec = read_row(r)
            rec.update(read_profile(r))
            fields = Tool.__dataclass_fields__
            tool = Tool(**{k: v for k, v in rec.items() if k in fields})
            d = ProfileEditorDialog(tool, dlg)
            if d.exec_() != QtWidgets.QDialog.Accepted:
                return
            prof = d.fields()
            tbl.item(r, 0).setData(QtCore.Qt.UserRole, json.dumps(prof))
            # reflect the parsed nose radius in its table column
            nr = tbl.item(r, 4)
            if nr is not None and prof.get("nose_radius"):
                nr.setText(f"{prof['nose_radius']:g}")

        btns = QtWidgets.QHBoxLayout()
        for label, fn in (("Add tool", add_row), ("Remove selected", del_row),
                          ("Profile\u2026", edit_profile)):
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
            rec = read_row(r)
            prof = read_profile(r)
            if not rec["name"]:
                continue
            if prof.get("insert_shape") or prof.get("iso_code"):
                rec.update(prof)
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
                "Pixmap": _icon("about")}

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
