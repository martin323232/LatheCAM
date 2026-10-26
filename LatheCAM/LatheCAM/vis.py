"""Toolpath visualization: motions -> colored Part edges in the 3D view.

Builds one Part::Feature compound per motion kind (rapid/feed/thread/dwell)
in the XZ lathe plane (x = radius, z = axial). GUI-safe: ViewObject styling
is applied only when a ViewObject exists (headless mode skips it).
"""

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


def motions_to_edges(per_op):
    """{kind: [edges]} — each Motion is a move TO (x, z) from the previous point."""
    edges = {k: [] for k in STYLE}
    for _label, _boring, motions in per_op:
        prev = None
        for mo in motions:
            if mo.kind == "dwell":
                continue  # no movement
            same = prev is not None and abs(prev[0] - mo.x) < 1e-9 \
                and abs(prev[1] - mo.z) < 1e-9
            if prev is not None and mo.kind in edges and not same:
                edges[mo.kind].append(_edge(prev[0], prev[1], mo.x, mo.z))
            prev = (mo.x, mo.z)
    return {k: v for k, v in edges.items() if v}


def visualize(doc, group_name="Toolpath"):
    """Create/replace colored toolpath objects (+ translucent stock). Returns objects."""
    stock, per_op = motions_for_document(doc)
    vp = view_placement(doc)

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
    for kind, segs in edges.items():
        name = "Vis_" + kind
        old = doc.getObject(name)
        if old:
            doc.removeObject(name)
        obj = doc.addObject("Part::Feature", name)
        obj.Shape = Part.Compound(segs)
        obj.Placement = vp
        obj.addProperty("App::PropertyString", "LatheRole", "Lathe").LatheRole = "vis"
        style(obj, *STYLE[kind])
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

    for o in created:
        grp.addObject(o)
    doc.recompute()
    return created
