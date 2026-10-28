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
from . import profile_extract
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
                "Pixmap": ""}

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
                "Pixmap": ""}

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
    builtins._lathecam_autovis = True
    return obs


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

    def addSelection(self, doc, obj, sub, pnt=None):
        self._highlight(doc, obj)

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


# ------------------------------------------------------ simulation panel

_SIM_PANEL = {}  # live dock + widgets (issue #8); empty when closed


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


def _open_sim_panel(doc):
    """Create or raise the simulation dock (issue #8): transport, speed,
    scrubbable timeline and per-op checkboxes over a seekable SimPlayer."""
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
    _SIM_PANEL.update({"dock": dock, "doc": doc, "list": lst,
                       "slider": slider, "status": status, "speed": speed,
                       "play": play_btn, "pause": pause_btn,
                       "stop": stop_btn,
                       "player": player, "syncing": False, "bulk": False})
    player.update_material = True
    player.ensure_marker()

    def checked_labels():
        out = []
        for i in range(lst.count()):
            it = lst.item(i)
            if it.checkState() == QtCore.Qt.Checked:
                out.append(str(it.data(QtCore.Qt.UserRole)))
        return out

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
        if not labels:
            p.pause()
            _SIM_PANEL["syncing"] = True
            slider.setValue(0)
            _SIM_PANEL["syncing"] = False
            status.setText("No operations selected.")
            return
        try:
            p.set_ops(labels)
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
    panel["player"].pause()
    panel["player"] = player
    panel["doc"] = doc
    panel["bulk"] = True
    _fill_op_list(panel["list"], ops)
    panel["bulk"] = False
    player.update_material = True
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
                "Pixmap": ""}

    def IsActive(self):
        return Gui.ActiveDocument is not None

    def Activated(self):
        _open_sim_panel(Gui.ActiveDocument.Document)


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
