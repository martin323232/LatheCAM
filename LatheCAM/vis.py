"""Toolpath visualization: motions -> colored Part edges in the 3D view.

Builds one Part::Feature compound per (op, motion kind) in the XZ lathe
plane (x = radius, z = axial), grouped per op so individual ops' paths can
be hidden in the tree (issue #7). Colors stay per kind. GUI-safe: ViewObject
styling is applied only when a ViewObject exists (headless mode skips it).
"""

import re

import FreeCAD as App
import Part
from FreeCAD import Vector

from .paths import generate
from .program import load_tools
from .objects import collect_program

# kind -> (RGB, DrawStyle)
STYLE = {
    "rapid": ((0.85, 0.15, 0.15), "Dashed"),
    "feed": ((0.10, 0.60, 0.20), "Solid"),
    "thread": ((0.15, 0.30, 0.90), "Solid"),
    "dwell": ((0.95, 0.70, 0.10), "Dotted"),
}


def motions_for_document(doc):
    """(stock, [(op_label, boring, [Motion])]) for the whole document.

    boring=True marks ID-type ops (id_*, internal thread) so sim can grow
    the bore instead of shrinking the outer envelope.
    """
    setup, ops = collect_program(doc)
    if setup is None:
        raise ValueError("no Lathe::Setup object in document")
    if not ops:
        raise ValueError("no Lathe::Op objects in document")
    # same library resolution as post_document: Setup.ToolLibraryFile wins
    tools = load_tools(str(getattr(setup, "ToolLibraryFile", "")).strip() or None)
    stock = setup.Proxy.to_stock()
    out = []
    for o in ops:
        op = o.Proxy.to_op()
        tool = tools.get(op.tool)
        if tool is None:
            raise ValueError(f"tool {op.tool!r} not in library")
        boring = (op.type.startswith("id_")
                  or (op.type == "thread" and getattr(op, "thread_inside", False)))
        out.append((o.Label, boring, generate(op, tool, stock)))
    return stock, out


def view_placement(doc):
    """Setup's ViewPlacement (identity when absent) — aligns vis/sim output
    with the model: lathe coords have Z = spindle axis, Z0 at the face."""
    setup, _ops = collect_program(doc)
    if setup is not None and hasattr(setup, "ViewPlacement"):
        return setup.ViewPlacement
    return App.Placement()


def _edge(x0, z0, x1, z1):
    return Part.makeLine(Vector(x0 / 2.0, 0.0, z0), Vector(x1 / 2.0, 0.0, z1))


def _safe_name(label):
    """Object-Name-safe form of an op label (Names allow alphanumerics/_)."""
    s = re.sub(r"[^A-Za-z0-9]+", "_", str(label)).strip("_")
    return s or "op"


def motions_to_edges(per_op):
    """{(op_label, kind): [edges]} — each Motion is a move TO (x, z) from
    the previous point. Grouped per op so each op's path is a separate
    object in the tree (issue #7)."""
    out = {}
    for label, _boring, motions in per_op:
        prev = None
        for mo in motions:
            if mo.kind == "dwell":
                continue  # no movement
            same = prev is not None and abs(prev[0] - mo.x) < 1e-9 \
                and abs(prev[1] - mo.z) < 1e-9
            if prev is not None and mo.kind in STYLE and not same:
                out.setdefault((label, mo.kind), []).append(
                    _edge(prev[0], prev[1], mo.x, mo.z))
            prev = (mo.x, mo.z)
    return out


def _hidden_labels(doc, setup):
    """Labels of vis objects the user hid: current tree state merged over
    the list stored on Setup (so hides survive rebuilds and reopen)."""
    hidden = set(str(x) for x in (getattr(setup, "HiddenVisPaths", None) or []))
    for o in doc.Objects:
        if o.TypeId != "Part::Feature" or not o.Name.startswith("Vis_"):
            continue
        vo = getattr(o, "ViewObject", None)
        if vo is None:
            continue  # headless: nothing to read, stored list stands
        try:
            shown = vo.Visibility
        except Exception:
            continue
        if shown:
            hidden.discard(o.Label)
        else:
            hidden.add(o.Label)
    return hidden


def visualize(doc, group_name="Toolpath"):
    """Create/replace per-op colored toolpath objects (+ translucent stock).

    One Part::Feature per (op, kind) inside a per-op subgroup, so Space on
    an op's object toggles just that op's path. Hidden paths are remembered
    on Setup.HiddenVisPaths and re-applied after the rebuild. Returns objects.
    """
    stock, per_op = motions_for_document(doc)
    setup, _ops = collect_program(doc)
    vp = view_placement(doc)

    # read hide state BEFORE the old objects are deleted
    hidden = _hidden_labels(doc, setup)

    # remove previous visualization objects (group + features share the Vis_ prefix)
    for o in list(doc.Objects):
        if o.Name.startswith("Vis_") or getattr(o, "LatheRole", "") == "vis":
            doc.removeObject(o.Name)
    grp = doc.addObject("App::DocumentObjectGroup", "Vis_" + group_name)
    grp.Label = group_name

    def style(obj, rgb, draw_style, width=2.0):
        vo = getattr(obj, "ViewObject", None)
        if vo is not None:
            try:
                obj.ViewObject.LineColor = rgb
                obj.ViewObject.LineWidth = width
                obj.ViewObject.DrawStyle = draw_style
            except Exception:
                pass

    created = []
    edges = motions_to_edges(per_op)
    op_groups = {}   # op_label -> (subgroup, safe name)
    used_safe = set()
    for (op_label, kind), segs in edges.items():
        if op_label in op_groups:
            sub, safe = op_groups[op_label]
        else:
            base = safe = _safe_name(op_label)
            n = 2
            while safe in used_safe:   # two distinct ops sanitize alike
                safe = f"{base}_{n}"
                n += 1
            used_safe.add(safe)
            sub = doc.addObject("App::DocumentObjectGroup", "Vis_Op_" + safe)
            sub.Label = str(op_label)
            grp.addObject(sub)
            op_groups[op_label] = (sub, safe)
        obj = doc.addObject("Part::Feature", "Vis_" + safe + "_" + kind)
        obj.Label = f"{op_label} \u00b7 {kind}"
        obj.Shape = Part.Compound(segs)
        obj.Placement = vp
        obj.addProperty("App::PropertyString", "LatheRole", "Lathe").LatheRole = "vis"
        obj.addProperty("App::PropertyString", "LatheOp", "Lathe").LatheOp = str(op_label)
        style(obj, *STYLE[kind])
        sub.addObject(obj)
        created.append(obj)

    # translucent stock cylinder for context
    old = doc.getObject("Vis_stock")
    if old:
        doc.removeObject("Vis_stock")
    cyl = Part.makeCylinder(stock.diameter / 2.0, stock.length,
                            Vector(0, 0, -stock.length), Vector(0, 0, 1))
    so = doc.addObject("Part::Feature", "Vis_stock")
    so.Shape = cyl
    so.Placement = vp
    so.addProperty("App::PropertyString", "LatheRole", "Lathe").LatheRole = "vis"
    vo = getattr(so, "ViewObject", None)
    if vo is not None:
        try:
            so.ViewObject.Transparency = 80
            so.ViewObject.ShapeColor = (0.7, 0.7, 0.7)
            so.ViewObject.DisplayMode = "Shaded"
        except Exception:
            pass
    created.append(so)
    grp.addObject(so)  # per-op features live in their subgroups, not here

    # translucent source parts: the toolpath runs inside the part, an
    # opaque solid hides it (users read that as "the path does not go
    # down"). Any visible Part::Feature that is not a vis object.
    for o in doc.Objects:
        if o.TypeId == "Part::Feature" and getattr(o, "LatheRole", "") != "vis":
            vo = getattr(o, "ViewObject", None)
            if vo is not None:
                try:
                    if getattr(vo, "Visibility", False):
                        vo.Transparency = 70
                except Exception:
                    pass

    # re-apply the user's hides (headless: no ViewObject, silently skipped)
    for o in created:
        vo = getattr(o, "ViewObject", None)
        if vo is not None and o.Label in hidden:
            try:
                vo.Visibility = False
            except Exception:
                pass

    # persist the hide list on Setup so it survives rebuilds and reopen
    if setup is not None:
        if "HiddenVisPaths" not in setup.PropertiesList:
            try:  # documents created before 0.3.23: migrate on first redraw
                setup.addProperty("App::PropertyStringList", "HiddenVisPaths",
                                  "View")
            except Exception:
                setup = None
    if setup is not None:
        try:
            setup.HiddenVisPaths = sorted(hidden)
        except Exception:
            pass

    doc.recompute()
    return created
