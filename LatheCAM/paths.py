"""Toolpath generators: op + tool + stock -> list of Motion.

Motion kinds:
  rapid  G0 linear move (X diameter, Z)
  feed   G1 linear move at feed mm/rev
  dwell  G4 P seconds
  thread G32 spindle-synchronised threading pass (F = pitch)

Every generator starts and ends with the tool at a safe position
(X >= safe_x or retracted, Z >= safe_z) so ops can be chained.
"""

from dataclasses import dataclass
from typing import List

from .model import Stock, Tool, Op, thread_depth, thread_passes


@dataclass
class Motion:
    kind: str
    x: float
    z: float
    feed: float = 0.0
    note: str = ""


def _feed(op: Op, tool: Tool) -> float:
    return op.feed if op.feed is not None else tool.feed


def _rpm(op: Op, tool: Tool) -> float:
    return op.rpm if op.rpm is not None else tool.rpm


def _approach(stock: Stock) -> List[Motion]:
    return [Motion("rapid", stock.safe_x(), stock.safe_z(), note="safe approach")]


def _retract(stock: Stock) -> List[Motion]:
    return [Motion("rapid", stock.safe_x(), stock.safe_z(), note="retract safe")]


def _z_offset(tool: Tool, w: float) -> float:
    """Z shift so the kerf stays centred on the nominal op Z given where the
    machine offset is measured (tool.z_reference: left = chuck-side corner,
    right = face-side corner, centre = middle of the insert)."""
    ref = getattr(tool, "z_reference", "centre")
    if ref == "left":
        return -w / 2.0
    if ref == "right":
        return w / 2.0
    return 0.0


# ---------------------------------------------------------------- face

def face(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """Face the end of the stock: Z-stepped passes, each sweeping to centre.

    Cuts planes from Z=+face_depth down to Z=0; each plane plunges at the
    outer diameter then feeds across to the centre line.
    """
    m = _approach(stock)
    feed = _feed(op, tool)
    start_d = min(op.face_diameter or stock.diameter, stock.diameter)
    ap = op.depth_per_pass
    depth = op.face_depth
    z = depth
    while z > 1e-6:
        zl = max(z - ap, 0.0)
        m.append(Motion("rapid", start_d, stock.safe_z()))
        m.append(Motion("feed", start_d, zl, feed, f"plunge to Z{zl:.2f}"))
        m.append(Motion("feed", 0.0, zl, feed, "face sweep"))
        m.append(Motion("rapid", stock.safe_x(), zl, 0, "clear"))
        z = zl
    m += _retract(stock)
    return m


# ---------------------------------------------------------------- OD rough

def od_rough(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """Plunge-rough the OD: step down in X, cut full Z length each pass.

    Collision-safe re-entry (K1/K2): every pass starts from the safe point
    (safe_x, safe_z), moves Z at safe_x (outside the stock), then reaches
    the pass diameter radially. If that entry point is axially inside the
    stock the radial move is a FEED (plunge cut), never a rapid.
    """
    m = _approach(stock)
    feed = _feed(op, tool)
    clr = stock.safe_z_clearance
    final = op.target_diameter + 2 * op.stock_to_leave
    # mid-length start: plunge exactly at z_start so material in front of it
    # (a collar that must keep its stock diameter) is never touched; a face
    # start enters in the air in front of the face
    z_entry = op.z_start if op.z_start < 0 else op.z_start + clr
    entry_kind = "feed" if z_entry < 0 else "rapid"

    def pass_at(d: float, label: str) -> List[Motion]:
        mm = [Motion("rapid", stock.safe_x(), stock.safe_z())]
        mm.append(Motion("rapid", stock.safe_x(), z_entry))
        mm.append(Motion(entry_kind, d, z_entry, feed, f"{label} entry X{d:.2f}"))
        mm.append(Motion("feed", d, op.z_end, feed, f"{label} X{d:.2f}"))
        mm.append(Motion("rapid", stock.safe_x(), op.z_end, 0, "retract X"))
        return mm

    d = stock.diameter - 2 * op.depth_per_pass
    while d >= final - 1e-6:
        m += pass_at(d, "OD rough")
        d -= 2 * op.depth_per_pass
    # final roughing pass exactly at the leave diameter
    if final > 0.001:
        m += pass_at(final, "OD rough finish")
    m += _retract(stock)
    return m


# ---------------------------------------------------------------- OD finish

def od_finish(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """Single finishing pass along profile (or simple cylinder)."""
    m = _approach(stock)
    feed = _feed(op, tool)
    pts = op.profile or [(op.target_diameter, op.z_start),
                         (op.target_diameter, op.z_end)]
    d0, z0 = pts[0]
    z_entry = z0 + stock.safe_z_clearance
    m.append(Motion("rapid", stock.safe_x(), stock.safe_z()))
    m.append(Motion("rapid", stock.safe_x(), z_entry))
    # if the entry point is axially inside the stock, plunge radially on feed
    kind = "feed" if z_entry < 0 else "rapid"
    m.append(Motion(kind, d0, z_entry, feed, "plunge to profile start"))
    m.append(Motion("feed", d0, z0, feed, "engage"))
    for d, z in pts[1:]:
        m.append(Motion("feed", d, z, feed, "profile"))
    m.append(Motion("rapid", min(pts[-1][0] + 4, stock.safe_x()), pts[-1][1], 0, "clear"))
    m += _retract(stock)
    return m


# ---------------------------------------------------------------- ID (bore)

def id_rough(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """Rough a bore: enter below bore dia, step outwards in X.

    Standard boring direction (R5): each pass feeds out to the pass diameter
    at the entry Z, then cuts toward the chuck (-Z), retracts 0.5 mm inward
    and rapids back inside the hole. bore_back_cut=True reverses the axial
    cut direction (+Z, back-boring)."""
    m = _approach(stock)
    feed = _feed(op, tool)
    enter = max(stock.bore_diameter - 1.0, 1.0)
    z_in = op.z_start + stock.safe_z_clearance
    z_entry = min(z_in, 0.0)          # plunge into the bore mouth at enter X
    final = op.target_diameter - 2 * op.stock_to_leave
    if op.bore_back_cut:
        z_from, z_to = op.z_end, z_entry   # cut toward the face (+Z)
    else:
        z_from, z_to = z_entry, op.z_end   # cut toward the chuck (-Z)

    m.append(Motion("rapid", enter, stock.safe_z()))
    m.append(Motion("rapid", enter, z_in))
    if z_entry < z_in - 1e-9:
        m.append(Motion("feed", enter, z_entry, feed, "plunge into bore mouth"))
    if z_from < z_entry - 1e-9:
        m.append(Motion("rapid", enter, z_from, 0, "position at bore bottom"))

    ps = []
    d = stock.bore_diameter + 2 * op.depth_per_pass
    while d <= final + 1e-6:
        ps.append(d)
        d += 2 * op.depth_per_pass
    if final > stock.bore_diameter:
        ps.append(final)
    for k, d in enumerate(ps):
        label = "bore rough finish" if k == len(ps) - 1 else "bore rough"
        m.append(Motion("feed", d, z_from, feed, f"{label} X{d:.2f}"))
        m.append(Motion("feed", d, z_to, feed, f"{label} cut"))
        m.append(Motion("feed", max(d - 1.0, enter), z_to, feed, "retract inward"))
        m.append(Motion("rapid", enter, z_to, 0, "collapse in bore"))
        if k < len(ps) - 1:
            m.append(Motion("rapid", enter, z_from, 0, "return to entry"))
    if abs(z_to - z_entry) > 1e-9:
        m.append(Motion("rapid", enter, z_entry, 0, "withdraw in bore"))
    m += _retract(stock)
    return m


def id_finish(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """Single finishing bore pass to target diameter, cutting toward the
    chuck (-Z; +Z when bore_back_cut)."""
    m = _approach(stock)
    feed = _feed(op, tool)
    enter = max(stock.bore_diameter - 1.0, 1.0)
    z_in = op.z_start + stock.safe_z_clearance
    z_entry = min(z_in, 0.0)
    if op.bore_back_cut:
        z_from, z_to = op.z_end, z_entry
    else:
        z_from, z_to = z_entry, op.z_end
    m.append(Motion("rapid", enter, stock.safe_z()))
    m.append(Motion("rapid", enter, z_in))
    if z_entry < z_in - 1e-9:
        m.append(Motion("feed", enter, z_entry, feed, "plunge into bore mouth"))
    if z_from < z_entry - 1e-9:
        m.append(Motion("rapid", enter, z_from, 0, "position at bore bottom"))
    m.append(Motion("feed", op.target_diameter, z_from, feed, "bore out"))
    m.append(Motion("feed", op.target_diameter, z_to, feed, "bore finish"))
    m.append(Motion("feed", max(op.target_diameter - 1.0, enter), z_to, feed,
                    "retract inward"))
    m.append(Motion("rapid", enter, z_to, 0, "collapse in bore"))
    if abs(z_to - z_entry) > 1e-9:
        m.append(Motion("rapid", enter, z_entry, 0, "withdraw in bore"))
    m += _retract(stock)
    return m


# ---------------------------------------------------------------- groove

def groove(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """Plunge a groove at tool width; peck in X. Wider grooves (B5) are
    cut as adjacent grooves stepped along Z by the tool width. Programmed
    Z is offset for the tool's z_reference corner (R6); the kerf stays
    centred on the nominal op Z. After each centre the tool rapids
    straight out in X before moving in Z (R3)."""
    m = _approach(stock)
    feed = _feed(op, tool)
    w = max(tool.width, 0.5)
    dz = _z_offset(tool, w)
    # z_start is the face-side (higher) end, z_end the deeper end
    z_hi, z_lo = max(op.z_start, op.z_end), min(op.z_start, op.z_end)
    if z_hi - z_lo <= w + 1e-6:
        centres = [(z_hi + z_lo) / 2.0]
    else:
        centres = []
        zc = z_hi - w / 2.0
        while zc - w / 2.0 > z_lo + 1e-6:
            centres.append(zc)
            zc -= w
        centres.append(z_lo + w / 2.0)
    top = stock.diameter + 2.0
    bottom = op.groove_diameter
    depth = (top - bottom) / 2.0
    for zc in centres:
        zp = zc + dz
        cut = 0.0
        while cut < depth - 1e-6:
            cut = min(cut + op.peck, depth)
            x = top - 2 * cut
            m.append(Motion("rapid", x + 2 * op.peck, zp))
            m.append(Motion("feed", x, zp, feed, f"groove Z{zp:.1f} X{x:.2f}"))
            m.append(Motion("rapid", x + 2 * op.peck, zp, 0, "peck retract"))
        m.append(Motion("rapid", top, zp, 0, "clear groove"))
    m.append(Motion("rapid", stock.safe_x(), centres[0] + dz, 0, "clear"))
    m += _retract(stock)
    return m


# ---------------------------------------------------------------- part-off

def part(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """Part off at Z position: peck plunge to centre. The op Z is the kerf
    centre; programmed Z is offset for the tool's z_reference corner (R6)."""
    m = _approach(stock)
    feed = _feed(op, tool)
    zp = op.z_start + _z_offset(tool, max(tool.width, 0.5))
    top = stock.diameter + 2.0
    cut = 0.0
    half = top / 2.0
    while cut < half - 1e-6:
        cut = min(cut + op.peck, half)
        x = top - 2 * cut
        m.append(Motion("rapid", x + 2 * op.peck, zp))
        m.append(Motion("feed", x, zp, feed, f"part X{x:.2f}"))
        m.append(Motion("rapid", x + 2 * op.peck, zp, 0, "peck retract"))
    m.append(Motion("rapid", stock.safe_x(), zp, 0, "clear"))
    m += _retract(stock)
    return m


# ---------------------------------------------------------------- thread

def thread(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """Straight external thread with G32 passes, progressive infeed.

    Lead-in: engage X at Z = z_start + lead (>= 1 pitch), run G32 to
    z_end = z_start - thread_length, retract X, return.
    """
    m = _approach(stock)
    p = op.thread_pitch
    lead_in = op.thread_lead_in or max(3.0 * p, 2.0)
    lead_out = max(1.0 * p, 1.0)
    z_engage = op.z_start + lead_in
    z_cut_end = op.z_start - op.thread_length - lead_out
    safe = op.thread_major_diameter + 2.0
    depths = thread_passes(p)
    for i, d_in in enumerate(depths):
        depth = sum(depths[: i + 1])
        x = op.thread_major_diameter - 2 * depth
        m.append(Motion("rapid", safe, z_engage))
        m.append(Motion("feed", x, z_engage, 0.05, f"thread infeed {depth:.2f}"))
        m.append(Motion("thread", x, z_cut_end, p, f"thread pass {i+1}"))
        m.append(Motion("feed", safe, z_cut_end, 0.05, "retract X"))
    if op.thread_spring_pass:
        x = op.thread_major_diameter - 2 * thread_depth(p)
        m.append(Motion("rapid", safe, z_engage))
        m.append(Motion("feed", x, z_engage, 0.05, "spring pass"))
        m.append(Motion("thread", x, z_cut_end, p, "spring pass"))
        m.append(Motion("feed", safe, z_cut_end, 0.05))
    m += _retract(stock)
    return m


# ---------------------------------------------------------------- dispatch

GENERATORS = {
    "face": face,
    "od_rough": od_rough,
    "od_finish": od_finish,
    "id_rough": id_rough,
    "id_finish": id_finish,
    "groove": groove,
    "part": part,
    "thread": thread,
}


def generate(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    op.validate(stock)
    return GENERATORS[op.type](op, tool, stock)


# ------------------------------------------------------- rapid collision check

class CollisionChecker:
    """Find rapid moves that pass through remaining material (S1-S4).

    Axisymmetric material model: at each Z the remaining core reaches out
    to the smallest diameter any outside (OD) cut has reached so far
    (initially the stock surface); bored cuts raise the inner floor the
    same way. A rapid sample collides when it lands inside the remaining
    annulus while axially on the stock. Feed moves are cuts, not collisions.

    Feed moves must be fed in program order; set boring=True for moves of
    an internal (ID) operation.

    Tool width (S4): pass tool_width + z_reference for groove/part tools —
    the kerf [z, z+w] (left) / [z-w, z] (right) / [z-w/2, z+w/2] (centre)
    is recorded for cuts and checked for rapids, so a tool whose programmed
    centre is in the air but whose edge overlaps uncut material is caught.
    Turning/boring tools are sampled at the programmed point (their insert
    width is not a kerf; keeping it a point stays conservative).
    """

    def __init__(self, stock: Stock, tol: float = 0.1, dz: float = 0.05):
        self.stock = stock
        self.tol = tol
        self.dz = dz
        self.nb = int(stock.length / dz) + 2
        self.core = [stock.diameter] * self.nb
        self.hole = [stock.bore_diameter] * self.nb
        self.problems: List[str] = []
        self.prev = Motion("rapid", stock.safe_x(), stock.safe_z())
        self.i = 0
        self.width = 0.0
        self.zref = "centre"

    def _bucket(self, z: float) -> int:
        return int(-z / self.dz) + 1

    def _buckets(self, z: float):
        """Bucket indices covered by the tool kerf at programmed Z."""
        w = self.width
        if w <= 1e-9:
            lo = hi = z
        elif self.zref == "left":      # programmed Z = chuck-side corner
            lo, hi = z, z + w
        elif self.zref == "right":     # programmed Z = face-side corner
            lo, hi = z - w, z
        else:
            lo, hi = z - w / 2.0, z + w / 2.0
        lo = max(lo, -self.stock.length)
        hi = min(hi, 0.0)
        if lo > hi:
            return ()
        return range(self._bucket(hi), self._bucket(lo) + 1)

    def add(self, motions: List[Motion], boring: bool = False,
            tool_width: float = 0.0, z_reference: str = "centre") -> None:
        tol = self.tol
        self.width = max(tool_width, 0.0)
        self.zref = z_reference
        for mo in motions:
            i = self.i
            self.i += 1
            if mo.kind == "dwell":
                continue
            prev = self.prev
            steps = max(2, int(abs(mo.z - prev.z) / self.dz) * 2 + 1)
            if mo.kind in ("feed", "thread"):
                for s in range(steps + 1):
                    t = s / steps
                    z = prev.z + (mo.z - prev.z) * t
                    x = prev.x + (mo.x - prev.x) * t
                    for j in self._buckets(z):
                        if boring:
                            if x > self.hole[j]:
                                self.hole[j] = x
                        else:
                            if x < self.core[j]:
                                self.core[j] = x
            else:  # rapid
                hit = None
                for s in range(steps + 1):
                    t = s / steps
                    z = prev.z + (mo.z - prev.z) * t
                    x = prev.x + (mo.x - prev.x) * t
                    for j in self._buckets(z):
                        if self.hole[j] + tol < x < self.core[j] - tol:
                            hit = (x, z)
                            break
                    if hit:
                        break
                if hit:
                    self.problems.append(
                        f"motion #{i} ({mo.kind}: {mo.note or 'move'}) rapid "
                        f"passes through material at X{hit[0]:.2f} Z{hit[1]:.2f}")
            self.prev = mo


def check_collisions(motions: List[Motion], stock: Stock,
                     tol: float = 0.1, dz: float = 0.05,
                     boring: bool = False, tool_width: float = 0.0,
                     z_reference: str = "centre") -> List[str]:
    """One-shot check of a motion list (all moves treated as outside/OD
    unless boring=True). Returns list of warnings; empty = rapids clean."""
    c = CollisionChecker(stock, tol, dz)
    c.add(motions, boring=boring, tool_width=tool_width,
          z_reference=z_reference)
    return c.problems
