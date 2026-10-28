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


def _select(per_op, only):
    """(cut_per_op, anim_per_op) for an op-label filter.

    anim_per_op is just the selected ops. cut_per_op is every op up to and
    INCLUDING the last selected one, so the shown material is the part as it
    actually looks when those ops run — animating the thread tool alone
    against raw stock would show it plunging into solid material.
    """
    if not only:
        return per_op, per_op
    sel = set(only)
    idx = [i for i, (label, _b, _m) in enumerate(per_op) if label in sel]
    if not idx:
        raise ValueError("none of the selected ops belong to this document")
    return per_op[:max(idx) + 1], [per_op[i] for i in idx]


def simulate(doc, group_name="Simulation", only=None):
    """Compute finished part from toolpath; create/replace Sim_part.

    only = iterable of op labels to restrict the run to (see _select).
    """
    stock, per_op = motions_for_document(doc)
    cut, _anim = _select(per_op, only)
    profile = finished_envelope(stock, cut)
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


# ---------------------------------------------------------------- player

SPEEDS = {"rapid": 120.0, "feed": 18.0, "thread": 8.0}  # mm/s display speed


class Segment:
    """One moving tool move on the animation timeline (radii in x)."""

    __slots__ = ("op", "label", "move", "n_moves", "x0", "z0", "x1", "z1",
                 "kind", "boring", "w", "feed", "note")

    def __init__(self, op, label, move, n_moves, x0, z0, x1, z1, kind,
                 boring, w, feed, note):
        self.op = op          # index into the player's op list
        self.label = label    # op Label
        self.move = move      # 1-based motion index within the op
        self.n_moves = n_moves
        self.x0, self.z0 = x0, z0
        self.x1, self.z1 = x1, z1
        self.kind = kind
        self.boring = boring
        self.w = w            # display seconds at speed 1
        self.feed = feed      # mm/rev (thread: pitch)
        self.note = note


def gcode_readout(seg):
    """G-code-ish text for the segment under the scrub slider."""
    cmd = {"rapid": "G0", "feed": "G1", "thread": "G32"}.get(seg.kind, "G1")
    txt = f"{cmd} X{seg.x1 * 2.0:.3f} Z{seg.z1:.3f}"   # X = diameter
    if seg.kind == "feed" and seg.feed:
        txt += f" F{seg.feed:.2f}"
    elif seg.kind == "thread" and seg.feed:
        txt += f" F{seg.feed:.3f}"
    if seg.note:
        txt += f"  ({seg.note})"
    return txt


class SimPlayer:
    """Seekable, speed-controlled tool animation over a program.

    The position math (set_ops / seek / segment_at) is pure and works
    headless; play/pause/stop only drive seek() from a QTimer, so the
    panel's slider can move the tool both ways and at any speed. With
    update_material the Sim_part is recomputed at every op boundary
    while scrubbing (material follows the ops, tool position scrubs
    freely — issue #8 v1).
    """

    def __init__(self, doc, duration=12.0, step_ms=30):
        self.doc = doc
        self.duration = float(duration)
        self.step_ms = int(step_ms)
        self.speed = 1.0
        self.t = 0.0
        self.update_material = False
        self.on_position = None      # fn(t)          after every seek
        self.on_op_change = None     # fn(op_index, label)
        self.segments = []
        self.op_labels = []
        self.total = 0.0
        self._cum = []
        self._timer = None
        self._marker = None
        self._shape_od = None
        self._shape_id = None
        self._vp = view_placement(doc)
        self._boring = None
        self._op_index = None
        self.set_ops(None)

    # ---- pure position state (headless-safe) ------------------------

    def set_ops(self, only=None):
        """Rebuild the timeline for an op-label subset (None = all ops)."""
        _stock, per_op = motions_for_document(self.doc)
        _cut, anim = _select(per_op, only)
        segs = []
        for oi, (label, boring, motions) in enumerate(anim):
            prev = None
            n = len(motions)
            for mi, mo in enumerate(motions):
                if mo.kind != "dwell" and prev is not None and (
                        abs(prev[0] - mo.x) > 1e-9 or abs(prev[1] - mo.z) > 1e-9):
                    length = ((mo.x - prev[0]) ** 2 + (mo.z - prev[1]) ** 2) ** 0.5
                    w = max(length / SPEEDS.get(mo.kind, 20.0), 0.01)
                    segs.append(Segment(oi, label, mi + 1, n,
                                        prev[0] / 2.0, prev[1],
                                        mo.x / 2.0, mo.z,
                                        mo.kind, boring, w,
                                        mo.feed, mo.note))
                prev = (mo.x, mo.z)
        self.segments = segs
        self.op_labels = [label for label, _b, _m in anim]
        tot_w = sum(s.w for s in segs) or 1.0
        scale = self.duration / tot_w
        self._cum = []
        acc = 0.0
        for s in segs:
            s.w *= scale
            self._cum.append(acc)
            acc += s.w
        self.total = acc
        self.t = 0.0
        self._op_index = None

    def segment_at(self, t):
        """(Segment, fraction) at program time t — no side effects."""
        if not self.segments:
            return None, 0.0
        import bisect
        t = min(max(t, 0.0), self.total)
        i = max(0, bisect.bisect_right(self._cum, t) - 1)
        if t >= self.total:          # end of program: park at path end
            return self.segments[-1], 1.0
        s = self.segments[i]
        span = s.w
        f = (t - self._cum[i]) / span if span > 0 else 0.0
        return s, min(max(f, 0.0), 1.0)

    def seek(self, t):
        """Move the tool to program time t (clamped). Scrub-safe both ways."""
        if not self.segments:
            return
        self.t = min(max(t, 0.0), self.total)
        s, f = self.segment_at(self.t)
        if App.GuiUp and self._marker is None:
            self.ensure_marker()
        if self._marker is not None:
            self._place(s, f)
        if s.op != self._op_index:
            self._op_index = s.op
            if self.update_material:
                simulate(self.doc, only=[s.label])
            if self.on_op_change is not None:
                self.on_op_change(s.op, s.label)
        if self.on_position is not None:
            self.on_position(self.t)

    # ---- transport (GUI only) ----------------------------------------

    def play(self):
        if not self.segments or not App.GuiUp:
            return
        if self.t >= self.total - 1e-9:
            self.seek(0.0)
        if self._timer is None:
            from PySide import QtCore
            self._timer = QtCore.QTimer()
            self._timer.setInterval(self.step_ms)
            self._timer.timeout.connect(self._tick)
        self._timer.start()

    def pause(self):
        if self._timer is not None:
            try:
                self._timer.stop()
            except RuntimeError:
                self._timer = None

    def stop(self):
        self.pause()
        self.seek(0.0)

    def playing(self):
        return bool(self._timer is not None and self._timer.isActive())

    def set_speed(self, mult):
        """Wall-clock pacing only — geometry and total are untouched."""
        self.speed = float(mult)

    def _tick(self):
        try:
            self.seek(self.t + self.step_ms / 1000.0 * self.speed)
            if self.t >= self.total - 1e-9:
                self.pause()
        except RuntimeError:
            self.pause()          # document or marker deleted under us

    # ---- tool marker --------------------------------------------------

    def ensure_marker(self):
        """Create (or re-create after deletion) the tool marker object."""
        if self._marker is not None:
            try:
                self._marker.Name          # touch: raises if C++ object gone
                return self._marker
            except RuntimeError:
                self._marker = None
        doc = self.doc
        old = doc.getObject(SIM_PREFIX + "tool")
        if old:
            doc.removeObject(SIM_PREFIX + "tool")
        if self._shape_od is None:
            tip = Part.makeSphere(0.9)
            # On the machine the two holders approach differently: an OD
            # holder comes in radially from outside, so its shank runs
            # outward (+X) from the tip; a boring bar enters through the
            # bore, so its shank runs parallel to the spindle axis (Z)
            # back toward the face side (+Z).
            shank_od = Part.makeCylinder(0.35, 6.0, Vector(0.5, 0, 0),
                                         Vector(1, 0, 0))
            shank_id = Part.makeCylinder(0.35, 6.0, Vector(-1.2, 0, 0),
                                         Vector(0, 0, 1))
            self._shape_od = Part.Compound([tip, shank_od])
            self._shape_id = Part.Compound([tip, shank_id])
        marker = doc.addObject("Part::Feature", SIM_PREFIX + "tool")
        marker.Label = "Simulated tool"
        marker.Shape = self._shape_od
        marker.addProperty("App::PropertyString", "LatheRole",
                           "Lathe").LatheRole = "sim"
        vo = getattr(marker, "ViewObject", None)
        if vo is not None:
            try:
                marker.ViewObject.ShapeColor = (0.95, 0.30, 0.10)
            except Exception:
                pass
        doc.recompute()
        self._marker = marker
        self._boring = None
        return marker

    def _place(self, s, f):
        x = s.x0 + f * (s.x1 - s.x0)
        z = s.z0 + f * (s.z1 - s.z0)
        try:
            # assigning .Shape resets Placement — switch shape FIRST
            if self._boring != s.boring:
                self._marker.Shape = self._shape_id if s.boring else self._shape_od
                self._boring = s.boring
            self._marker.Placement = self._vp.multiply(
                App.Placement(Vector(x, 0.0, z), App.Rotation()))
        except RuntimeError:
            self._marker = None   # deleted (plain Simulate ran, doc closed)


def animate(doc, duration=12.0, step_ms=30, only=None):
    """Animate a tool marker along the path (GUI only). Non-blocking.

    only = iterable of op labels: animate just those operations.
    Thin wrapper over SimPlayer so the plain Simulate command and the
    simulation panel (issue #8) share one seekable implementation.
    """
    if not App.GuiUp:
        return None
    player = SimPlayer(doc, duration, step_ms)
    player.set_ops(only)
    if not player.segments:
        return None
    marker = player.ensure_marker()
    old = _ANIM.get("player")     # never leave a zombie timer running
    if old is not None:
        old.pause()
    _ANIM["player"] = player
    player.seek(0.0)
    player.play()
    return marker
