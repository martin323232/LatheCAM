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


# ---------------------------------------------------------------- face

def face(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """Face the end of the stock: cut at Z=0 from outer dia towards centre."""
    m = _approach(stock)
    start_d = min(op.face_diameter or stock.diameter, stock.diameter)
    ap = op.depth_per_pass
    d = start_d
    first = True
    while d > 0.001:
        if not first:
            m.append(Motion("rapid", min(d + 2 * ap, start_d), stock.safe_z()))
        first = False
        m.append(Motion("rapid", d, stock.safe_z()))
        m.append(Motion("feed", d, 0.0, _feed(op, tool), "plunge to face"))
        m.append(Motion("feed", max(d - 2 * ap, 0.0), 0.0, _feed(op, tool), "face cut"))
        d -= 2 * ap
    m += _retract(stock)
    return m


# ---------------------------------------------------------------- OD rough

def od_rough(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """Plunge-rough the OD: step down in X, cut full Z length each pass."""
    m = _approach(stock)
    feed = _feed(op, tool)
    final = op.target_diameter + 2 * op.stock_to_leave
    d = stock.diameter - 2 * op.depth_per_pass
    while d >= final - 1e-6:
        m.append(Motion("rapid", d, stock.safe_z()))
        m.append(Motion("rapid", d, op.z_start + stock.safe_z_clearance))
        m.append(Motion("feed", d, op.z_end, feed, f"OD rough X{d:.2f}"))
        m.append(Motion("rapid", d + 2 * op.depth_per_pass, op.z_end, 0, "clear"))
        d -= 2 * op.depth_per_pass
    # final roughing pass exactly at the leave diameter
    if final > 0.001:
        m.append(Motion("rapid", final, stock.safe_z()))
        m.append(Motion("rapid", final, op.z_start + stock.safe_z_clearance))
        m.append(Motion("feed", final, op.z_end, feed, f"OD rough finish X{final:.2f}"))
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
    m.append(Motion("rapid", d0, stock.safe_z()))
    m.append(Motion("rapid", d0, z0 + stock.safe_z_clearance))
    m.append(Motion("feed", d0, z0, feed, "engage"))
    for d, z in pts[1:]:
        m.append(Motion("feed", d, z, feed, "profile"))
    m.append(Motion("rapid", min(pts[-1][0] + 4, stock.safe_x()), pts[-1][1], 0, "clear"))
    m += _retract(stock)
    return m


# ---------------------------------------------------------------- ID (bore)

def id_rough(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """Rough a bore: enter below bore dia, step outwards in X."""
    m = _approach(stock)
    feed = _feed(op, tool)
    enter = stock.bore_diameter - 1.0
    final = op.target_diameter - 2 * op.stock_to_leave
    m.append(Motion("rapid", max(enter, 1.0), stock.safe_z()))
    m.append(Motion("feed", max(enter, 1.0), op.z_end, feed, "plunge to bore bottom"))
    d = stock.bore_diameter + 2 * op.depth_per_pass
    while d <= final + 1e-6:
        m.append(Motion("feed", d, op.z_end, feed, f"bore X{d:.2f}"))
        m.append(Motion("feed", d, op.z_start, feed, "withdraw"))
        if d + 2 * op.depth_per_pass <= final:
            m.append(Motion("feed", d, op.z_end, feed, "re-engage"))
        d += 2 * op.depth_per_pass
    if final > stock.bore_diameter:
        m.append(Motion("feed", final, op.z_end, feed, f"bore rough finish X{final:.2f}"))
        m.append(Motion("feed", final, op.z_start, feed))
    m.append(Motion("rapid", max(enter, 1.0), op.z_start, 0, "collapse"))
    m += _retract(stock)
    return m


def id_finish(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """Single finishing bore pass to target diameter."""
    m = _approach(stock)
    feed = _feed(op, tool)
    enter = stock.bore_diameter - 1.0
    m.append(Motion("rapid", max(enter, 1.0), stock.safe_z()))
    m.append(Motion("feed", max(enter, 1.0), op.z_end, feed, "plunge"))
    m.append(Motion("feed", op.target_diameter, op.z_end, feed, "bore out"))
    m.append(Motion("feed", op.target_diameter, op.z_start, feed, "bore finish"))
    m.append(Motion("rapid", max(enter, 1.0), op.z_start, 0, "collapse"))
    m += _retract(stock)
    return m


# ---------------------------------------------------------------- groove

def groove(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """Plunge a groove of tool width at Z centre; peck in X."""
    m = _approach(stock)
    feed = _feed(op, tool)
    zc = (op.z_start + op.z_end) / 2.0
    top = stock.diameter + 2.0
    bottom = op.groove_diameter
    depth = (top - bottom) / 2.0
    cut = 0.0
    while cut < depth - 1e-6:
        cut = min(cut + op.peck, depth)
        x = top - 2 * cut
        m.append(Motion("rapid", x + 2 * op.peck, zc))
        m.append(Motion("feed", x, zc, feed, f"groove Z{zc:.1f} X{x:.2f}"))
        m.append(Motion("rapid", x + 2 * op.peck, zc, 0, "peck retract"))
    m.append(Motion("rapid", stock.safe_x(), zc, 0, "clear"))
    m += _retract(stock)
    return m


# ---------------------------------------------------------------- part-off

def part(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """Part off at Z position: peck plunge to centre."""
    m = _approach(stock)
    feed = _feed(op, tool)
    top = stock.diameter + 2.0
    cut = 0.0
    half = top / 2.0
    while cut < half - 1e-6:
        cut = min(cut + op.peck, half)
        x = top - 2 * cut
        m.append(Motion("rapid", x + 2 * op.peck, op.z_start))
        m.append(Motion("feed", x, op.z_start, feed, f"part X{x:.2f}"))
        m.append(Motion("rapid", x + 2 * op.peck, op.z_start, 0, "peck retract"))
    m.append(Motion("rapid", stock.safe_x(), op.z_start, 0, "clear"))
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
    lead_in = max(2.0 * p, 2.0)
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
