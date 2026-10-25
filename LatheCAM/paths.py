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
import math
from typing import List

from .model import Stock, Tool, Op, thread_depth, thread_passes


@dataclass
class Motion:
    kind: str          # rapid | feed | dwell | thread | cw | ccw
    x: float
    z: float
    feed: float = 0.0
    note: str = ""
    radius: float = 0.0   # A: arc radius (mm) for kind "cw"/"ccw"


def _feed(op: Op, tool: Tool) -> float:
    if op.feed:
        return op.feed
    # B: finish ops use the tool's dedicated finish feed when set
    if op.type.endswith("finish") and getattr(tool, "finish_feed", 0.0):
        return tool.finish_feed
    return tool.feed


def _rpm(op: Op, tool: Tool) -> float:
    return op.rpm if op.rpm is not None else tool.rpm


def _approach(stock: Stock) -> List[Motion]:
    return [Motion("rapid", stock.safe_x(), stock.safe_z(), note="safe approach")]


def _retract(stock: Stock) -> List[Motion]:
    return [Motion("rapid", stock.safe_x(), stock.safe_z(), note="retract safe")]


def _entry_z(stock: Stock, z: float) -> float:
    """Entry Z for an OD op starting at nominal Z: keep the entry point out
    of the facing allowance slab (B3) — if it would land inside the slab,
    lift it above the allowance instead."""
    ze = z + stock.safe_z_clearance
    if 0 < ze <= stock.face_allowance:
        ze = stock.face_allowance + stock.safe_z_clearance
    return ze


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
    # start enters in the air in front of the face (above any facing allowance)
    z_entry = op.z_start if op.z_start < 0 else _entry_z(stock, op.z_start)
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

def _apply_chamfers(op: Op, pts: List[tuple], stock: Stock) -> List[tuple]:
    """C: 45-degree chamfer at the profile start/end corner (45° = 2c in
    diameter per c in Z). Start corner (face -> first OD): replace
    (d0,z0) with (d0-2c, z0) and insert (d0, z0-c) — the chamfer line runs
    on the part, never in mid-air. End corner (last OD -> shoulder):
    replace (dN,zN) with (dN, zN+c) and append (dN+2c, zN). The adjacent
    segment must be axial and the chamfer must fit inside it."""
    cs = getattr(op, "chamfer_start", 0.0) or 0.0
    ce = getattr(op, "chamfer_end", 0.0) or 0.0
    if cs <= 1e-9 and ce <= 1e-9:
        return pts
    if len(pts) < 2:
        raise ValueError("chamfer needs a profile with at least 2 points")
    pts = list(pts)
    if cs > 1e-9:
        (d0, z0, _r0), (d1, z1, _r1) = pts[0], pts[1]
        if abs(d1 - d0) > 1e-9:
            raise ValueError("chamfer_start: first profile segment must be "
                             "axial (constant diameter) at the face corner")
        if not (0 < cs <= z0 - z1 + 1e-9):
            raise ValueError(f"chamfer_start {cs} does not fit the first "
                             f"segment length {z0 - z1:.3f}")
        if d0 - 2 * cs <= 0:
            raise ValueError(f"chamfer_start {cs} crosses the centre line")
        pts = [(d0 - 2 * cs, z0, 0.0), (d0, z0 - cs, 0.0)] + pts[1:]
    if ce > 1e-9:
        (dN, zN, rN) = pts[-1]
        (_dP, zP, _rP) = pts[-2]
        if abs(pts[-1][0] - pts[-2][0]) > 1e-9:
            raise ValueError("chamfer_end: last profile segment must be "
                             "axial (constant diameter) at the shoulder")
        if rN != 0.0:
            raise ValueError("chamfer_end: cannot chamfer after an arc endpoint")
        if not (0 < ce <= zP - zN + 1e-9):
            raise ValueError(f"chamfer_end {ce} does not fit the last "
                             f"segment length {zP - zN:.3f}")
        if zN + ce > 1e-9:
            raise ValueError("chamfer_end would run past the face (Z0)")
        if dN + 2 * ce > stock.diameter + 1e-9:
            raise ValueError(f"chamfer_end would exceed stock diameter "
                             f"{stock.diameter:g}")
        pts = pts[:-1] + [(dN, zN + ce, 0.0), (dN + 2 * ce, zN, 0.0)]
    return pts


def od_finish(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """Single finishing pass along profile (or simple cylinder). A: profile
    points may carry a segment radius (D,Z,R): R>0 -> cw (G02), R<0 -> ccw
    (G03), applied to the segment ENDING at that point."""
    m = _approach(stock)
    feed = _feed(op, tool)
    raw = op.profile or [(op.target_diameter, op.z_start),
                         (op.target_diameter, op.z_end)]
    pts = [(p[0], p[1], p[2] if len(p) > 2 else 0.0) for p in raw]
    pts = _apply_chamfers(op, pts, stock)
    d0, z0, _ = pts[0]
    z_entry = _entry_z(stock, z0)
    m.append(Motion("rapid", stock.safe_x(), stock.safe_z()))
    m.append(Motion("rapid", stock.safe_x(), z_entry))
    # if the entry point is axially inside the stock, plunge radially on feed
    kind = "feed" if z_entry < 0 else "rapid"
    m.append(Motion(kind, d0, z_entry, feed, "plunge to profile start"))
    m.append(Motion("feed", d0, z0, feed, "engage"))
    prev = (d0, z0)
    for d, z, r in pts[1:]:
        if abs(r) > 1e-9:
            chord = math.hypot((d - prev[0]) / 2.0, z - prev[1])
            if abs(r) < chord / 2.0 - 1e-9:
                raise ValueError(
                    f"arc R={abs(r)} too small for chord {chord:.3f} "
                    f"(X{prev[0]} Z{prev[1]} -> X{d} Z{z})")
            m.append(Motion("cw" if r > 0 else "ccw", d, z, feed,
                            "profile arc", radius=r))
        else:
            m.append(Motion("feed", d, z, feed, "profile"))
        prev = (d, z)
    m.append(Motion("rapid", stock.safe_x(), pts[-1][1], 0, "clear radially"))
    m.append(Motion("rapid", stock.safe_x(), stock.safe_z(), 0, "withdraw"))
    return m


# ---------------------------------------------------------------- ID (bore)

def _id_rough_profile(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """Profile-aware bore roughing: layered passes over the finish profile.

    Pass k cuts the finish profile capped at diameter D_k = bore + 2*dpp*k
    (stepping by 2*dpp) and offset inward by 2*stock_to_leave, so every
    profile point keeps exactly stock_to_leave radial for the finish pass.
    Paths are polylines — arc entries belong to the finish pass. Boring
    discipline holds: X and Z never move together on rapids at Z < 0."""
    m = _approach(stock)
    feed = _feed(op, tool)
    enter = max(stock.bore_diameter - 1.0, 1.0)
    pts = [(p[0], p[1], p[2] if len(p) > 2 else 0.0) for p in op.profile]
    max_d = max(p[0] for p in pts)
    final = max_d - 2 * op.stock_to_leave
    if final <= stock.bore_diameter:
        raise ValueError("id_rough profile: profile max dia leaves nothing "
                         "to remove over the existing bore")
    passes = []
    d = stock.bore_diameter + 2 * op.depth_per_pass
    while d <= final + 1e-9:
        passes.append(d)
        d += 2 * op.depth_per_pass
    if not passes or final > passes[-1] + 1e-9:
        passes.append(final)
    z_from = op.z_start if op.z_start < 0 else 0.0
    # only profile points at or below the entry depth are cut by this op
    kept = [p for p in pts if p[1] <= z_from + 1e-9]
    if not kept:
        raise ValueError("id_rough profile: no profile points below ZStart")

    m.append(Motion("rapid", enter, stock.safe_z()))
    if op.z_start < 0:
        m.append(Motion("rapid", enter, z_from))
    else:
        m.append(Motion("rapid", enter, op.z_start + stock.safe_z_clearance))
        m.append(Motion("feed", enter, z_from, feed, "plunge into bore mouth"))

    for k, D in enumerate(passes):
        label = "profile rough" if k < len(passes) - 1 else "profile rough last"
        path = [(max(min(D, p[0] - 2 * op.stock_to_leave), enter), p[1])
                for p in kept]
        if path[0][1] < z_from - 1e-9:
            path.insert(0, (path[0][0], z_from))
        prev = path[0]
        m.append(Motion("feed", prev[0], prev[1], feed,
                        f"{label} out to X{prev[0]:.2f}"))
        for x, z in path[1:]:
            if abs(x - prev[0]) < 1e-9 and abs(z - prev[1]) < 1e-9:
                continue
            m.append(Motion("feed", x, z, feed, f"{label} cut"))
            prev = (x, z)
        if path[-1][0] > enter + 1e-9:
            m.append(Motion("rapid", enter, path[-1][1], 0,
                            "collapse in bore"))
        if k < len(passes) - 1:
            m.append(Motion("rapid", enter, z_from, 0, "return to entry"))
    m.append(Motion("rapid", enter, stock.safe_z(), 0, "lift out at enter dia"))
    m += _retract(stock)
    return m


def id_rough(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """Rough a bore: enter below bore dia, step outwards in X.

    Standard boring direction (R5): each pass feeds out to the pass diameter
    at the entry Z, then cuts toward the chuck (-Z), retracts 0.5 mm inward
    and rapids back inside the hole. bore_back_cut=True reverses the axial
    cut direction (+Z, back-boring).

    With a profile set, delegates to _id_rough_profile: layered passes that
    follow the finish profile (capped + offset), not just a cylinder."""
    if op.profile:
        return _id_rough_profile(op, tool, stock)
    m = _approach(stock)
    feed = _feed(op, tool)
    enter = max(stock.bore_diameter - 1.0, 1.0)
    if op.z_start < 0:
        # B1: z_start is inside the existing bore: rapid in at the enter
        # diameter and start cutting exactly at z_start. Material between
        # the face and z_start must keep its diameter.
        z_in = z_entry = op.z_start
    else:
        z_in = op.z_start + stock.safe_z_clearance
        z_entry = 0.0              # plunge into the bore mouth at enter X
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
    # B2: lift out of the hole at the enter diameter FIRST, then move out
    # in X; a diagonal retract from inside the bore cuts the bore mouth wall.
    m.append(Motion("rapid", enter, stock.safe_z(), 0, "lift out at enter dia"))
    m += _retract(stock)
    return m


def id_finish(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """Single finishing bore pass.

    With a profile (same D,Z[,R] format as od_finish; R>0 -> G02, R<0 ->
    G03) the tool follows the profile inside the bore. Entry and exit keep
    the boring discipline: no move changes X and Z at the same time while
    Z < 0 except the profile cut moves — the tool always withdraws
    axially through the bore before moving out in X.

    Empty profile = single bore pass to target_diameter, cutting toward
    the chuck (-Z; +Z when bore_back_cut)."""
    m = _approach(stock)
    feed = _feed(op, tool)
    enter = max(stock.bore_diameter - 1.0, 1.0)
    if op.profile:
        pts = [(p[0], p[1], p[2] if len(p) > 2 else 0.0) for p in op.profile]
        d0, z0, _ = pts[0]
        m.append(Motion("rapid", stock.safe_x(), stock.safe_z()))
        if z0 >= 0:
            # starts at the face: rapid in at the first profile diameter
            m.append(Motion("rapid", d0, stock.safe_z()))
            m.append(Motion("feed", d0, z0, feed, "engage at face"))
        else:
            # starts inside the existing bore: rapid in at enter dia
            m.append(Motion("rapid", enter, stock.safe_z()))
            m.append(Motion("rapid", enter, z0))
            m.append(Motion("feed", d0, z0, feed, "feed out to profile start"))
        prev = (d0, z0)
        for d, z, r in pts[1:]:
            if abs(r) > 1e-9:
                chord = math.hypot((d - prev[0]) / 2.0, z - prev[1])
                if abs(r) < chord / 2.0 - 1e-9:
                    raise ValueError(
                        f"arc R={abs(r)} too small for chord {chord:.3f} "
                        f"(X{prev[0]} Z{prev[1]} -> X{d} Z{z})")
                m.append(Motion("cw" if r > 0 else "ccw", d, z, feed,
                                "profile arc", radius=r))
            else:
                m.append(Motion("feed", d, z, feed, "profile"))
            prev = (d, z)
        dN, zN = pts[-1][0], pts[-1][1]
        # exit: pull in to a diameter that clears the bore, lift out
        # axially through it, only then move out in X
        pool = [p[0] for p in pts]
        if stock.bore_diameter > 0:
            pool.append(stock.bore_diameter)
        x_out = max(min(pool) - 1.0, 0.5)
        if dN > x_out + 1e-9:
            m.append(Motion("feed", x_out, zN, feed, "retract inward"))
        m.append(Motion("rapid", x_out, stock.safe_z(), 0,
                        "lift out through bore"))
        m.append(Motion("rapid", stock.safe_x(), stock.safe_z(), 0, "clear"))
        return m
    if op.z_start < 0:
        # B1: rapid in inside the existing bore, cut starts at z_start
        z_in = z_entry = op.z_start
    else:
        z_in = op.z_start + stock.safe_z_clearance
        z_entry = 0.0
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
    # B2: lift out at the enter diameter before moving out in X
    m.append(Motion("rapid", enter, stock.safe_z(), 0, "lift out at enter dia"))
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
    """Straight thread with G32 passes, progressive infeed.

    External (default): engage X at Z = z_start + lead (>= 1 pitch), run G32
    to z_end = z_start - thread_length, retract X, return.
    Internal (thread_inside, E): cut inside an existing bore toward the
    chuck (-Z), like a standard right-hand internal threading bar.
    VERIFY: no proven reference program covers internal threading on this
    machine — dry-run mandatory before first use.
    """
    if getattr(op, "thread_inside", False):
        return _thread_internal(op, tool, stock)
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


def _thread_internal(op: Op, tool: Tool, stock: Stock) -> List[Motion]:
    """E: internal thread inside an existing bore, cutting toward the chuck
    (-Z). Infeed increases X from the minor diameter; each pass retracts
    inward (never a diagonal through material) and rapids back to the entry
    Z. B2 discipline on withdrawal: lift out at the enter diameter first.
    VERIFY — dry-run mandatory (no proven reference)."""
    p = op.thread_pitch
    lead_out = max(1.0 * p, 1.0)
    minor = op.thread_major_diameter - 2 * thread_depth(p)
    enter = max(stock.bore_diameter - 1.0, 1.0)
    z_engage = op.z_start
    z_cut_end = op.z_start - op.thread_length - lead_out
    m = _approach(stock)
    m.append(Motion("rapid", enter, stock.safe_z()))
    m.append(Motion("rapid", enter, z_engage, 0, "enter bore at thread start"))
    depths = thread_passes(p)
    for i in range(len(depths)):
        depth = sum(depths[: i + 1])
        x = minor + 2 * depth
        m.append(Motion("feed", x, z_engage, 0.05,
                        f"ithread infeed {depth:.2f}"))
        m.append(Motion("thread", x, z_cut_end, p, f"ithread pass {i + 1}"))
        m.append(Motion("feed", max(minor - 0.5, enter), z_cut_end, 0.05,
                        "retract inward"))
        m.append(Motion("rapid", enter, z_cut_end, 0, "collapse in bore"))
        if i < len(depths) - 1 or op.thread_spring_pass:
            m.append(Motion("rapid", enter, z_engage, 0, "return to entry"))
    if op.thread_spring_pass:
        x = minor + 2 * thread_depth(p)
        m.append(Motion("feed", x, z_engage, 0.05, "spring pass infeed"))
        m.append(Motion("thread", x, z_cut_end, p, "spring pass"))
        m.append(Motion("feed", max(minor - 0.5, enter), z_cut_end, 0.05))
        m.append(Motion("rapid", enter, z_cut_end, 0, "collapse in bore"))
    # B2 discipline: lift out at the enter diameter before moving out in X
    m.append(Motion("rapid", enter, stock.safe_z(), 0, "lift out at enter dia"))
    m += _retract(stock)
    return m

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

def _arc_samples(p1: Motion, p2: Motion, dz: float) -> List[tuple]:
    """A4: sample an arc motion (X-diameter endpoints) as a polyline in the
    (radius, Z) plane; returns (x_diameter, z) points including both ends.
    Center side is picked from the sweep direction (cw = clockwise in the
    X-Z plane as viewed with X right, Z toward the face)."""
    r = abs(p2.radius)
    x1, z1 = p1.x / 2.0, p1.z
    x2, z2 = p2.x / 2.0, p2.z
    dx, dzv = x2 - x1, z2 - z1
    c = math.hypot(dx, dzv)
    if c < 1e-12 or r < c / 2.0 - 1e-9:
        return [(p2.x, p2.z)]
    h = c / 2.0
    d = math.sqrt(max(r * r - h * h, 0.0))
    mx, mz = (x1 + x2) / 2.0, (z1 + z2) / 2.0
    ux, uz = dx / c, dzv / c
    if p2.kind == "cw":
        cx, cz = mx + d * uz, mz - d * ux
    else:
        cx, cz = mx - d * uz, mz + d * ux
    a1 = math.atan2(z1 - cz, x1 - cx)
    a2 = math.atan2(z2 - cz, x2 - cx)
    if p2.kind == "cw":
        while a2 >= a1:
            a2 -= 2 * math.pi
    else:
        while a2 <= a1:
            a2 += 2 * math.pi
    n = max(2, int(abs(a2 - a1) * r / dz) + 1)
    return [(2 * (cx + r * math.cos(a1 + (a2 - a1) * i / n)),
             cz + r * math.sin(a1 + (a2 - a1) * i / n))
            for i in range(n + 1)]


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
        # B3: material exists from Z=+face_allowance down to -length; the
        # bucket grid starts at the top of that allowance.
        self.fa = max(stock.face_allowance, 0.0)
        self.nb = int((stock.length + self.fa) / dz) + 2
        self.core = [stock.diameter] * self.nb
        self.hole = [stock.bore_diameter] * self.nb
        self.problems: List[str] = []
        self.prev = Motion("rapid", stock.safe_x(), stock.safe_z())
        self.i = 0
        self.width = 0.0
        self.zref = "centre"

    def _bucket(self, z: float) -> int:
        return int((self.fa - z) / self.dz) + 1

    def _buckets(self, z: float):
        """Bucket indices covered by the tool kerf at programmed Z."""
        if z > self.fa + 1e-9:
            return ()          # air in front of the face / facing allowance
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
        hi = min(hi, self.fa)
        if lo > hi:
            return ()
        return range(self._bucket(hi), self._bucket(lo) + 1)

    def add(self, motions: List[Motion], boring: bool = False,
            tool_width: float = 0.0, z_reference: str = "centre",
            nose_radius: float = 0.0) -> None:
        tol = self.tol
        self.width = max(tool_width, 0.0)
        self.zref = z_reference
        # N2: with nose compensation active the tool centre runs outside the
        # nominal path; record material conservatively (more left behind).
        off = 2.0 * max(nose_radius, 0.0)
        for mo in motions:
            i = self.i
            self.i += 1
            if mo.kind == "dwell":
                continue
            prev = self.prev
            steps = max(2, int(abs(mo.z - prev.z) / self.dz) * 2 + 1)
            if mo.kind in ("feed", "thread", "cw", "ccw"):
                if mo.kind in ("cw", "ccw"):
                    samples = _arc_samples(prev, mo, self.dz)
                else:
                    samples = [(prev.x + (mo.x - prev.x) * s / steps,
                                prev.z + (mo.z - prev.z) * s / steps)
                               for s in range(steps + 1)]
                for x, z in samples:
                    for j in self._buckets(z):
                        if boring:
                            xx = x - off
                            if xx > self.hole[j]:
                                self.hole[j] = xx
                        else:
                            xx = x + off
                            if xx < self.core[j]:
                                self.core[j] = xx
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
                     z_reference: str = "centre",
                     nose_radius: float = 0.0) -> List[str]:
    """One-shot check of a motion list (all moves treated as outside/OD
    unless boring=True). Returns list of warnings; empty = rapids clean."""
    c = CollisionChecker(stock, tol, dz)
    c.add(motions, boring=boring, tool_width=tool_width,
          z_reference=z_reference, nose_radius=nose_radius)
    return c.problems
