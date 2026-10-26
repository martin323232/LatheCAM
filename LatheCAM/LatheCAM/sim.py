"""Lathe simulation: material-removal result + animated tool marker.

The cut is axisymmetric, so the finished part follows from the toolpath
alone, but OD and ID cuts work opposite ways:

  * OD turning leaves the SMALLEST radius the tool ever reached at z
  * boring GROWS the bore: the LARGEST radius the ID tool reached at z,
    starting from the pre-drilled bore radius

The result is the annulus between the two profiles, revolved 360 deg into
the solid "Sim_part" (solid, not a plug, when a bore exists). Optionally a
tool marker is animated along the path (GUI only).
"""

import FreeCAD as App
import Part
from FreeCAD import Vector

from .vis import motions_for_document, view_placement

SIM_PREFIX = "Sim_"
_ANIM = {}  # keeps timer + state alive between calls


def _cut_segments(per_op):
    """[(r0, z0, r1, z1, kind, boring)] for every moving cut (feed/thread)."""
    segs = []
    for _label, boring, motions in per_op:
        prev = None
        for mo in motions:
            if mo.kind in ("feed", "thread") and prev is not None:
                if abs(prev[0] - mo.x) > 1e-9 or abs(prev[1] - mo.z) > 1e-9:
                    segs.append((prev[0] / 2.0, prev[1], mo.x / 2.0, mo.z,
                                 mo.kind, boring))
            prev = (mo.x, mo.z)
    return segs


def finished_envelope(stock, per_op, dz=0.05):
    """[(z, r_outer, r_bore)] profile of the finished part, face (z=0) to back.

    OD moves shrink r_outer (min radius reached); boring moves grow r_bore
    (max radius reached, from the pre-drilled bore radius). r_bore is
    clamped to r_outer.
    """
    import math
    n = int(round(stock.length / dz)) + 1
    outer = [stock.diameter / 2.0] * n
    bore = [stock.bore_diameter / 2.0] * n
    segs = _cut_segments(per_op)
    for x0, z0, x1, z1, _kind, boring in segs:
        zlo, zhi = min(z0, z1), max(z0, z1)
        if abs(z1 - z0) < 1e-9:  # pure radial move: cuts one plane
            i = int(round(-z0 / dz))
            if 0 <= i < n:
                if boring:
                    bore[i] = max(bore[i], max(x0, x1))
                else:
                    outer[i] = min(outer[i], min(x0, x1))
            continue
        i0 = max(0, int(math.floor(-zhi / dz)))
        i1 = min(n - 1, int(round(-zlo / dz)))
        for i in range(i0, i1 + 1):
            z = -i * dz
            r = x0 + (z - z0) * (x1 - x0) / (z1 - z0)
            if boring:
                if r > bore[i]:
                    bore[i] = r
            elif r < outer[i]:
                outer[i] = r
    return [(-i * dz, outer[i], min(bore[i], outer[i])) for i in range(n)]


def envelope_solid(profile):
    """Revolve the (z, r_outer, r_bore) profile around the lathe axis (Z).

    Solid when every r_bore is 0; otherwise an annulus whose inner wall
    follows r_bore — the boring result.
    """
    outer = [Vector(r, 0.0, z) for z, r, _b in profile]
    pts = [outer[0]]
    for p in outer[1:]:
        if (p - pts[-1]).Length > 1e-9:
            pts.append(p)
    if all(abs(b) <= 1e-9 for _z, _r, b in profile):
        pts.append(Vector(0.0, 0.0, pts[-1].z))  # close to axis at back
        pts.append(Vector(0.0, 0.0, pts[0].z))   # up the axis to face
    else:
        inner = [Vector(b, 0.0, z) for z, _r, b in profile]
        for p in reversed(inner):               # back to face along the bore
            if (p - pts[-1]).Length > 1e-9:
                pts.append(p)
    pts.append(pts[0])  # makePolygon does not auto-close; an open wire
    # yields an invalid face and a wrong-volume revolve
    wire = Part.makePolygon(pts)
    face = Part.Face(wire)
    return face.revolve(Vector(0, 0, 0), Vector(0, 0, 1), 360)


def simulate(doc, group_name="Simulation"):
    """Compute finished part from toolpath; create/replace Sim_part. Returns obj."""
    stock, per_op = motions_for_document(doc)
    profile = finished_envelope(stock, per_op)
    solid = envelope_solid(profile)

    old = doc.getObject(SIM_PREFIX + "part")
    if old:
        doc.removeObject(SIM_PREFIX + "part")
    obj = doc.addObject("Part::Feature", SIM_PREFIX + "part")
    obj.Label = "Simulated result"
    obj.Shape = solid
    obj.Placement = view_placement(doc)
    obj.addProperty("App::PropertyString", "LatheRole", "Lathe").LatheRole = "sim"
    vo = getattr(obj, "ViewObject", None)
    if vo is not None:
        try:
            obj.ViewObject.ShapeColor = (0.20, 0.75, 0.35)
            obj.ViewObject.Transparency = 30
        except Exception:
            pass
    grp = doc.getObject(SIM_PREFIX + group_name)
    if grp is None:
        grp = doc.addObject("App::DocumentObjectGroup", SIM_PREFIX + group_name)
        grp.Label = group_name
    grp.addObject(obj)
    doc.recompute()
    return obj


# ---------------------------------------------------------------- animation

def _flatten(per_op):
    """[(x0, z0, x1, z1, kind, boring)] every moving motion, radii."""
    segs = []
    for _label, _boring, motions in per_op:
        prev = None
        for mo in motions:
            if mo.kind != "dwell" and prev is not None:
                if abs(prev[0] - mo.x) > 1e-9 or abs(prev[1] - mo.z) > 1e-9:
                    segs.append((prev[0] / 2.0, prev[1], mo.x / 2.0, mo.z,
                                 mo.kind, _boring))
            prev = (mo.x, mo.z)
    return segs


def animate(doc, duration=12.0, step_ms=30):
    """Animate a tool marker along the full path (GUI only). Non-blocking."""
    if not App.GuiUp:
        return None
    import FreeCADGui as Gui
    stock, per_op = motions_for_document(doc)
    segs = _flatten(per_op)
    if not segs:
        return None

    speeds = {"rapid": 120.0, "feed": 18.0, "thread": 8.0}  # mm/s display speed
    weights = []
    for x0, z0, x1, z1, kind, _b in segs:
        length = ((x1 - x0) ** 2 + (z1 - z0) ** 2) ** 0.5
        weights.append(max(length / speeds.get(kind, 20.0), 0.01))
    total = sum(weights)
    scale = duration / total

    old = doc.getObject(SIM_PREFIX + "tool")
    if old:
        doc.removeObject(SIM_PREFIX + "tool")
    tip = Part.makeSphere(0.9)
    # On the machine the tool shank runs parallel to the spindle axis (Z),
    # extending from the tip back toward the face side (+Z).  An OD holder
    # sits just outside the cut surface; a boring-bar body sits inside the
    # bore with the tip projecting radially at its end.
    shank_od = Part.makeCylinder(0.35, 6.0, Vector(0.5, 0, 0), Vector(0, 0, 1))
    shank_id = Part.makeCylinder(0.35, 6.0, Vector(-1.2, 0, 0), Vector(0, 0, 1))
    shape_od = Part.Compound([tip, shank_od])
    shape_id = Part.Compound([tip, shank_id])
    marker = doc.addObject("Part::Feature", SIM_PREFIX + "tool")
    marker.Label = "Simulated tool"
    marker.Shape = shape_od
    vp = view_placement(doc)
    marker.addProperty("App::PropertyString", "LatheRole", "Lathe").LatheRole = "sim"
    vo = getattr(marker, "ViewObject", None)
    if vo is not None:
        try:
            marker.ViewObject.ShapeColor = (0.95, 0.30, 0.10)
        except Exception:
            pass
    doc.recompute()

    state = {"t": 0.0, "i": 0, "acc": 0.0, "boring": None}
    old = _ANIM.get("timer")
    if old is not None:            # S5: never leave a zombie timer running
        try:
            old.stop()
        except RuntimeError:
            pass                    # underlying C++ object already deleted
    timer = QtCore_QTimer()
    _ANIM["timer"] = timer
    _ANIM["state"] = state
    _ANIM["marker"] = marker
    _ANIM["doc"] = doc

    def place(x, z, boring):
        # assigning .Shape resets Placement — switch shape FIRST
        if state["boring"] != boring:
            marker.Shape = shape_id if boring else shape_od
            state["boring"] = boring
        marker.Placement = vp.multiply(
            App.Placement(Vector(x, 0.0, z), App.Rotation()))

    def tick():
        state["t"] += step_ms / 1000.0
        # find current segment
        t = state["t"]
        i = 0
        acc = 0.0
        while i < len(weights) and t > acc + weights[i] * scale:
            acc += weights[i] * scale
            i += 1
        if i >= len(segs):
            place(*segs[-1][2:4], segs[-1][5])
            timer.stop()
            _ANIM.pop("timer", None)
            return
        x0, z0, x1, z1, _k, boring = segs[i]
        f = (t - acc) / (weights[i] * scale)
        place(x0 + f * (x1 - x0), z0 + f * (z1 - z0), boring)

    timer.setInterval(step_ms)
    timer.timeout.connect(tick)
    place(segs[0][0], segs[0][1], segs[0][5])
    timer.start()
    return marker


def QtCore_QTimer():
    from PySide import QtCore
    return QtCore.QTimer()
