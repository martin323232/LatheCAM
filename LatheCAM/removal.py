"""Continuous material-removal simulation (issue #13).

The cut is axisymmetric, so the stock is fully described by two radius
profiles over Z: ``outer[i]`` (smallest radius an OD cut has reached at
that Z) and ``bore[i]`` (largest radius a boring cut has reached). A
simulation run walks the program's cutting moves in order and updates
those two arrays — no booleans, no voxels — and snapshots the state after
every cutting move so any point in the timeline can be restored in O(1).

Tool model (v1, before issue #14's real insert polygons):
  * groove / part / thread tools cut a Z-band of ``tool.width`` placed by
    ``tool.z_reference`` (left | centre | right) around the programmed Z.
    This is what gives a groove floor its real width — the point-tool
    envelope in sim.py could only ever cut a slit.
  * turn / bore tools stay point-tools in Z (same semantics as the
    finished-envelope sim, so removed volumes stay comparable).
  * A turn-tool radial move that reaches the axis is a facing cut: it
    removes the whole slab in FRONT of the plane (Z >= Z_plane), which is
    how a facing allowance actually disappears.

Pure stdlib (math + array): importable from plain python3 for tests and
from FreeCAD for the player. X is DIAMETER at every public boundary,
radii inside.
"""

import math
from array import array

from .paths import _arc_samples

CUT_KINDS = ("feed", "thread", "cw", "ccw")
BAND_KINDS = ("groove", "part", "thread")   # tools whose width is a Z-band


def insert_band(tool):
    """(z_lo, z_hi) offsets of the insert band around the programmed Z,
    or None for point-tools (turn/bore in v1)."""
    kind = getattr(tool, "kind", "turn")
    if kind not in BAND_KINDS:
        return None
    w = max(float(getattr(tool, "width", 0.0) or 0.0), 0.0)
    if w <= 1e-9:
        return None
    ref = getattr(tool, "z_reference", "centre")
    if ref == "left":
        return (0.0, w)
    if ref == "right":
        return (-w, 0.0)
    return (-w / 2.0, w / 2.0)


class Envelope:
    """(outer, bore) radius arrays over Z samples, face side first.

    Z samples run from +face_allowance down to -length: the facing
    allowance slab is part of the stock and facing must visibly remove it.
    """

    __slots__ = ("dz", "fa", "n", "outer", "bore")

    def __init__(self, stock, dz=0.05, outer=None, bore=None):
        self.dz = dz
        self.fa = max(float(getattr(stock, "face_allowance", 0.0) or 0.0), 0.0)
        self.n = int(round((stock.length + self.fa) / dz)) + 1
        self.outer = outer if outer is not None else \
            array("d", [stock.diameter / 2.0] * self.n)
        self.bore = bore if bore is not None else \
            array("d", [stock.bore_diameter / 2.0] * self.n)

    # ---- index helpers --------------------------------------------------

    def z_of(self, i):
        return self.fa - i * self.dz

    def i_of(self, z):
        return int(round((self.fa - z) / self.dz))

    def _clamp(self, i):
        return max(0, min(self.n - 1, i))

    def copy(self):
        return envelope_from(self, array("d", self.outer),
                             array("d", self.bore))

    # ---- cutting --------------------------------------------------------

    def cut(self, x0, z0, x1, z1, boring=False, band=None):
        """Apply one cutting move (radii, mm). Arcs must be pre-sampled."""
        if abs(x1 - x0) < 1e-12 and abs(z1 - z0) < 1e-12:
            return    # zero-length (e.g. partial move at frac=0): no cut
        axial = abs(z1 - z0) > 1e-9
        radial = abs(x1 - x0) > 1e-9
        if band is not None:
            lo, hi = band
            if not axial:                      # plunge / kerf at one Z
                self._slab(z0 + lo, z0 + hi, x0, x1, boring)
            else:               # axial feed or angled ramp: swept Z span
                self._slab(min(z0, z1) + lo, max(z0, z1) + hi,
                           x0, x1, boring)
            return
        # point tool (turn / bore)
        if not axial:
            if not boring and min(x0, x1) <= 1e-9:
                self.face_off(z0)             # facing: remove slab in front
                return
            i = self._clamp(self.i_of(z0))
            if boring:
                self.bore[i] = max(self.bore[i], max(x0, x1))
            else:
                self.outer[i] = min(self.outer[i], min(x0, x1))
            return
        if not radial:                         # pure axial feed
            i0 = self._clamp(self.i_of(max(z0, z1)))
            i1 = self._clamp(self.i_of(min(z0, z1)))
            if boring:
                r = max(x0, x1)
                b = self.bore
                for i in range(i0, i1 + 1):
                    if r > b[i]:
                        b[i] = r
            else:
                r = min(x0, x1)
                o = self.outer
                for i in range(i0, i1 + 1):
                    if r < o[i]:
                        o[i] = r
            return
        # angled point move: interpolate radius along Z (as sim.py does)
        i0 = self._clamp(self.i_of(max(z0, z1)))
        i1 = self._clamp(self.i_of(min(z0, z1)))
        for i in range(i0, i1 + 1):
            z = self.z_of(i)
            r = x0 + (z - z0) * (x1 - x0) / (z1 - z0)
            if boring:
                self.bore[i] = max(self.bore[i], r)
            else:
                self.outer[i] = min(self.outer[i], r)

    def _slab(self, z_lo, z_hi, x0, x1, boring):
        """Band tool: over Z in [z_lo, z_hi] the cut floor (OD) is
        min(x0, x1); for boring the ceiling is max(x0, x1)."""
        i0 = self._clamp(self.i_of(z_hi))
        i1 = self._clamp(self.i_of(z_lo))
        if boring:
            r = max(x0, x1)
            b = self.bore
            for i in range(i0, i1 + 1):
                if r > b[i]:
                    b[i] = r
        else:
            r = min(x0, x1)
            o = self.outer
            for i in range(i0, i1 + 1):
                if r < o[i]:
                    o[i] = r

    def face_off(self, z_plane):
        """Facing: everything in front of the plane (Z >= z_plane) is gone."""
        i1 = self._clamp(self.i_of(z_plane))
        o = self.outer
        for i in range(0, i1 + 1):
            o[i] = 0.0

    # ---- results ----------------------------------------------------------

    def profile(self):
        """[(z, r_outer, r_bore)] face-side first — same shape as
        sim.finished_envelope output."""
        dz, fa, n = self.dz, self.fa, self.n
        o, b = self.outer, self.bore
        return [(fa - i * dz, o[i], min(b[i], o[i])) for i in range(n)]

    def volume(self):
        """Revolved volume of the remaining annulus (mm^3), trapezoid over
        the profile so an untouched stock integrates to exactly pi r^2 L."""
        dz = self.dz
        o, b = self.outer, self.bore
        vals = [o[i] * o[i] - min(b[i], o[i]) ** 2 for i in range(self.n)]
        total = sum(vals) - 0.5 * (vals[0] + vals[-1])
        return math.pi * dz * total


def envelope_from(env, outer, bore):
    e = Envelope.__new__(Envelope)
    e.dz, e.fa, e.n = env.dz, env.fa, env.n
    e.outer, e.bore = outer, bore
    return e


class CutMove:
    """One cutting move after arc sampling, with its source motion."""

    __slots__ = ("x0", "z0", "x1", "z1", "boring", "band", "op", "motion")

    def __init__(self, x0, z0, x1, z1, boring, band, op, motion):
        self.x0, self.z0, self.x1, self.z1 = x0, z0, x1, z1
        self.boring = boring
        self.band = band
        self.op = op              # op index in the ops list
        self.motion = motion      # motion index within that op


class RemovalSim:
    """Walk a program once; snapshot the envelope after every cutting move.

    ops = [(label, boring, tool, motions)] — what
    vis.tool_motions_for_document returns (with the tool kept).
    """

    def __init__(self, stock, ops, dz=0.05):
        self.stock = stock
        self.dz = dz
        self.env0 = Envelope(stock, dz)
        self.moves = []
        self.snaps = []           # snaps[k] = (outer, bore) AFTER moves[k]
        self._prefix = {}         # (op, motion) -> last cut index up to it
        self._build(ops)

    def _build(self, ops):
        env = self.env0.copy()   # work envelope — env0 stays the raw stock
        for oi, (_label, boring, tool, motions) in enumerate(ops):
            band = insert_band(tool)
            prev = None           # (r, z) of last motion
            prev_mo = None        # matching Motion (for arc sampling)
            last = len(self.moves) - 1
            for mi, mo in enumerate(motions):
                if mo.kind in CUT_KINDS and prev is not None:
                    pts = [prev, (mo.x / 2.0, mo.z)]
                    if mo.kind in ("cw", "ccw") and prev_mo is not None:
                        arc = _arc_samples(prev_mo, mo, self.dz)
                        if len(arc) > 2:
                            pts = [(p[0] / 2.0, p[1]) for p in arc]
                    for (x0, z0), (x1, z1) in zip(pts, pts[1:]):
                        if abs(x1 - x0) < 1e-9 and abs(z1 - z0) < 1e-9:
                            continue
                        env.cut(x0, z0, x1, z1, boring, band)
                        self.moves.append(
                            CutMove(x0, z0, x1, z1, boring, band, oi, mi))
                        self.snaps.append(
                            (array("d", env.outer), array("d", env.bore)))
                    last = len(self.moves) - 1
                if mo.kind != "dwell":
                    prev = (mo.x / 2.0, mo.z)
                    prev_mo = mo
                self._prefix[(oi, mi)] = last

    # ---- queries ----------------------------------------------------------

    def cut_index(self, op, motion):
        """Last cutting-move index produced up to and including that
        motion (-1 = nothing cut yet)."""
        return self._prefix.get((op, motion), -1)

    def state_at(self, cut_idx, frac=1.0):
        """Envelope state after cutting move cut_idx (or partial frac of
        it). cut_idx = -1 -> untouched stock."""
        if cut_idx < 0:
            return self.env0.copy()
        outer, bore = self.snaps[cut_idx]
        if frac >= 1.0 - 1e-12:
            return envelope_from(self.env0, array("d", outer),
                                 array("d", bore))
        prev_o, prev_b = (self.snaps[cut_idx - 1] if cut_idx > 0
                          else (self.env0.outer, self.env0.bore))
        env = envelope_from(self.env0, array("d", prev_o), array("d", prev_b))
        m = self.moves[cut_idx]
        f = max(0.0, min(1.0, frac))
        env.cut(m.x0, m.z0, m.x0 + f * (m.x1 - m.x0),
                m.z0 + f * (m.z1 - m.z0), m.boring, m.band)
        return env

    def volume_after(self, cut_idx):
        if cut_idx < 0:
            return self.env0.volume()
        outer, bore = self.snaps[cut_idx]
        return envelope_from(self.env0, outer, bore).volume()
