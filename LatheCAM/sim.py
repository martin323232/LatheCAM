"""Lathe simulation: material-removal result + animated tool marker.

For OD turning the cut is axisymmetric: at every axial position Z the tool
leaves the smallest radius it ever reached there. So the finished part can be
computed exactly from the toolpath alone:

    final_r(z) = min(stock_r, min radius of every feed/thread move at z)

The result is revolved 360 deg into a solid ("Sim_part"). Optionally a tool
marker is animated along the path (GUI only).
"""

import FreeCAD as App
import Part
from FreeCAD import Vector

from .vis import motions_for_document

SIM_PREFIX = "Sim_"
_ANIM = {}  # keeps timer + state alive between calls


def _cut_segments(per_op):
    """[(x0, z0, x1, z1, kind)] for every moving cut (feed/thread), radii."""
    segs = []
    for _label, motions in per_op:
        prev = None
        for mo in motions:
            if mo.kind in ("feed", "thread") and prev is not None:
                if abs(prev[0] - mo.x) > 1e-9 or abs(prev[1] - mo.z) > 1e-9:
                    segs.append((prev[0] / 2.0, prev[1], mo.x / 2.0, mo.z, mo.kind))
            prev = (mo.x, mo.z)
    return segs


def finished_envelope(stock, per_op, dz=0.05):
    """[(z, radius)] profile of the finished part, face (z=0) to back (-L)."""
    import math
    n = int(round(stock.length / dz)) + 1
    prof = [stock.diameter / 2.0] * n
    segs = _cut_segments(per_op)
    for x0, z0, x1, z1, _kind in segs:
        zlo, zhi = min(z0, z1), max(z0, z1)
        if abs(z1 - z0) < 1e-9:  # pure radial move: cuts one plane
            i = int(round(-z0 / dz))
            if 0 <= i < n:
                prof[i] = min(prof[i], min(x0, x1))
            continue
        i0 = max(0, int(math.floor(-zhi / dz)))
        i1 = min(n - 1, int(round(-zlo / dz)))
        for i in range(i0, i1 + 1):
            z = -i * dz
            t = (z - z0) / (z1 - z0)
            r = x0 + t * (x1 - x0)
            if r < prof[i]:
                prof[i] = r
    return [(-i * dz, prof[i]) for i in range(n)]


def envelope_solid(profile):
    """Revolve the (z, r) profile around the lathe axis (Z) into a solid."""
    pts = [Vector(r, 0.0, z) for z, r in profile]
    # drop consecutive duplicates
    wire_pts = [pts[0]]
    for p in pts[1:]:
        if (p - wire_pts[-1]).Length > 1e-9:
            wire_pts.append(p)
    wire_pts.append(Vector(0.0, 0.0, wire_pts[-1].z))  # close to axis at back
    wire_pts.append(Vector(0.0, 0.0, wire_pts[0].z))   # up the axis to face
    wire = Part.makePolygon(wire_pts)
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
    """[(x0, z0, x1, z1, kind)] every moving motion, radii."""
    segs = []
    for _label, motions in per_op:
        prev = None
        for mo in motions:
            if mo.kind != "dwell" and prev is not None:
                if abs(prev[0] - mo.x) > 1e-9 or abs(prev[1] - mo.z) > 1e-9:
                    segs.append((prev[0] / 2.0, prev[1], mo.x / 2.0, mo.z, mo.kind))
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
    for x0, z0, x1, z1, kind in segs:
        length = ((x1 - x0) ** 2 + (z1 - z0) ** 2) ** 0.5
        weights.append(max(length / speeds.get(kind, 20.0), 0.01))
    total = sum(weights)
    scale = duration / total

    old = doc.getObject(SIM_PREFIX + "tool")
    if old:
        doc.removeObject(SIM_PREFIX + "tool")
    tip = Part.makeSphere(0.9)
    arm = Part.makeCylinder(0.35, 6.0, Vector(0, 0, 0), Vector(1, 0, 0))
    marker = doc.addObject("Part::Feature", SIM_PREFIX + "tool")
    marker.Label = "Simulated tool"
    marker.Shape = Part.Compound([tip, arm])
    marker.addProperty("App::PropertyString", "LatheRole", "Lathe").LatheRole = "sim"
    vo = getattr(marker, "ViewObject", None)
    if vo is not None:
        try:
            marker.ViewObject.ShapeColor = (0.95, 0.30, 0.10)
        except Exception:
            pass
    doc.recompute()

    state = {"t": 0.0, "i": 0, "acc": 0.0}
    timer = QtCore_QTimer()
    _ANIM["timer"] = timer
    _ANIM["state"] = state
    _ANIM["marker"] = marker
    _ANIM["doc"] = doc

    def place(x, z):
        marker.Placement = App.Placement(Vector(x, 0.0, z), App.Rotation())

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
            place(*segs[-1][2:4])
            timer.stop()
            _ANIM.pop("timer", None)
            return
        x0, z0, x1, z1, _k = segs[i]
        f = (t - acc) / (weights[i] * scale)
        place(x0 + f * (x1 - x0), z0 + f * (z1 - z0))

    timer.setInterval(step_ms)
    timer.timeout.connect(tick)
    place(segs[0][0], segs[0][1])
    timer.start()
    return marker


def QtCore_QTimer():
    from PySide import QtCore
    return QtCore.QTimer()
