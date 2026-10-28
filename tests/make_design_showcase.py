"""Six NEW lathe designs, full pipeline, one showcase sheet.

For each design: build solid -> extract profile (auto OD or face-picked ID)
-> od/id rough + finish -> CollisionChecker -> post NC -> measure deviation.

freecadcmd make_design_showcase.py
Writes /tmp/designs.png and /tmp/nc/*.nc. Marker: SHOWCASE DONE
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "LatheCAM"))
import Part
from FreeCAD import Vector as V
from PIL import Image, ImageDraw, ImageFont

from LatheCAM import profile_extract as px
from LatheCAM.model import Op, Stock, Tool
from LatheCAM import paths
from LatheCAM.post import Block, post_program

F = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
font = ImageFont.truetype(F, 15)
font_s = ImageFont.truetype(F, 12)
font_b = ImageFont.truetype(F, 17)
font_m = ImageFont.truetype(F, 11)

CW, CH = 640, 430
KW = {"depth_per_" + "pass": 1.5, "stock_to_leave": 0.25}


def cyl(r, z0, z1):
    return Part.makeCylinder(r, z0 - z1, V(0, 0, z1), V(0, 0, 1))


def ring(r_out, r_in, z0, z1):
    return cyl(r_out, z0, z1).cut(cyl(r_in, z0 + 0.001, z1 - 0.001))


def circle_edge(solid, r, z, tol=1e-6):
    for e in solid.Edges:
        c = e.Curve
        if (isinstance(c, Part.Circle) and abs(c.Radius - r) < tol
                and abs(c.Center.z - z) < tol and abs(c.Center.x) < tol):
            return e
    raise AssertionError(f"no circle edge r={r} z={z}")


inner = lambda lim: (lambda f: f.BoundBox.XMax < lim)

# ---------------- D1 V-belt pulley (OD + ID) ----------------
_d1 = cyl(25, 0, -20)
_g1 = cyl(25.001, -4, -10).cut(
    Part.makeCone(17, 25.0, 6.0, V(0, 0, -10), V(0, 0, 1)))
_g2 = cyl(25.001, -10, -16).cut(
    Part.makeCone(17, 25.0, 6.0, V(0, 0, -10), V(0, 0, -1)))
_d1 = _d1.cut(_g1).cut(_g2)
_d1 = _d1.cut(cyl(10, 1, -21))
_d1 = _d1.makeChamfer(1.5, [circle_edge(_d1, 10.0, 0.0),
                            circle_edge(_d1, 10.0, -20.0)])
# ---------------- D2 spindle: journals, undercuts, thread prep ----------------
_d2 = cyl(8, 0, -14).fuse(cyl(11, -14, -26)).fuse(cyl(7, -26, -40)) \
        .fuse(cyl(6.25, -40, -46))
_d2 = _d2.cut(ring(8.1, 6.0, -12, -14))          # undercut at big shoulder
_d2 = _d2.cut(ring(7.1, 5.0, -38, -40))          # thread relief
_d2 = _d2.makeChamfer(1.5, [circle_edge(_d2, 8.0, 0.0)])
# ---------------- D3 hydraulic fitting: thread prep, O-ring, JIC cone -------
_d3 = cyl(6.75, 0, -15.5).fuse(cyl(9, -15.5, -30)) \
        .fuse(Part.makeCone(9.0, 6.0, 8.0, V(0, 0, -30), V(0, 0, -1)))
_d3 = _d3.cut(ring(9.1, 5.0, -14, -15.5))        # relief
_d3 = _d3.cut(ring(10.0, 7.2, -18, -21))         # O-ring groove on OD
_d3 = _d3.cut(cyl(4, 1, -31))
_d3 = _d3.cut(cyl(6, 0, -5))                     # counterbore
_d3 = _d3.makeChamfer(0.6, [circle_edge(_d3, 6.75, 0.0)])
# ---------------- D4 flanged bushing ----------------
_d4 = cyl(20, 0, -8).fuse(cyl(12, -8, -35))
_d4 = _d4.cut(cyl(8, 1, -36))
_d4 = _d4.cut(cyl(9.5, 0, -6))                   # counterbore
_d4 = _d4.cut(ring(10.0, 8.0, -20, -23))         # internal groove
_d4 = _d4.makeChamfer(1.5, [circle_edge(_d4, 20.0, 0.0),
                            circle_edge(_d4, 12.0, -35.0)])
# ---------------- D5 taper mandrel ----------------
_d5 = cyl(10, 0, -5).fuse(Part.makeCone(10.0, 6.0, 40.0, V(0, 0, -5),
                                        V(0, 0, -1)))
_d5 = _d5.makeChamfer(1.0, [circle_edge(_d5, 6.0, -45.0)])
# ---------------- D6 ball-seat plug (ID) ----------------
_d6 = cyl(15, 0, -25).cut(cyl(5, 1, -26))
_d6 = _d6.cut(Part.makeCone(8.0, 5.0, 5.196, V(0, 0, 0), V(0, 0, -1)))
_d6 = _d6.cut(Part.makeSphere(6.0, V(0, 0, -18)))

DESIGNS = [
    ("D1 V-belt pulley — OD", px.auto_profile(_d1), False, _d1),
    ("D2 spindle — OD", px.auto_profile(_d2), False, _d2),
    ("D3 hydraulic fitting — OD", px.auto_profile(_d3), False, _d3),
    ("D4 flanged bushing — OD", px.auto_profile(_d4), False, _d4),
    ("D5 taper mandrel — OD", px.auto_profile(_d5), False, _d5),
    ("D1 pulley — bore", px.profile_from_segments(
        px.segments(_d1, [f"Face{i+1}" for i, f in enumerate(_d1.Faces)
                          if inner(12.0)(f)])), True, _d1),
    ("D4 bushing — bore", px.profile_from_segments(
        px.segments(_d4, [f"Face{i+1}" for i, f in enumerate(_d4.Faces)
                          if inner(11.0)(f)])), True, _d4),
    ("D6 ball-seat plug — bore", px.profile_from_segments(
        px.segments(_d6, [f"Face{i+1}" for i, f in enumerate(_d6.Faces)
                          if inner(13.0)(f)])), True, _d6),
]


def parse(prof):
    return [tuple(float(x) for x in p.split(",")) for p in prof.split(";")]


def pt_seg(p, a, b):
    px_, pz = p
    ax, az = a
    dx, dz = b[0] - ax, b[1] - az
    L2 = dx * dx + dz * dz
    if L2 < 1e-15:
        return math.hypot(px_ - ax, pz - az)
    t = max(0.0, min(1.0, ((px_ - ax) * dx + (pz - az) * dz) / L2))
    return math.hypot(px_ - (ax + t * dx), pz - (az + t * dz))


def dev_to_profile(pts, p):
    xy = [(a[0], a[1]) for a in pts]
    return min(pt_seg(p, xy[i], xy[i + 1]) for i in range(len(xy) - 1))


def build(prof, is_id):
    pts = parse(prof)
    ds = [p[0] for p in pts]
    z_end = min(p[1] for p in pts)
    if is_id:
        bore, target = min(ds), max(ds)
        stock = Stock(diameter=bore + 18.0, length=abs(z_end) + 4,
                      bore_diameter=bore)
        rough_t = Tool("bor-r", 3, "bore", width=3.0, max_depth=abs(z_end),
                       rpm=800, feed=0.15)
        fin_t = Tool("bor", 3, "bore", width=3.0, max_depth=abs(z_end),
                     rpm=900, feed=0.1)
        rough = Op("id_rough", "bor-r", "p", z_start=0.0, z_end=z_end,
                   target_diameter=target, profile=pts, **KW)
        fin = Op("id_finish", "bor", "p", z_start=0.0, z_end=z_end,
                 target_diameter=target, profile=pts)
        boring = True
    else:
        stock = Stock(diameter=max(ds) + 3.0, length=abs(z_end) + 4)
        rough_t = Tool("OD-rough", 1, "turn", rpm=800, feed=0.2)
        fin_t = Tool("OD-finish", 2, "turn", nose_radius=0.8, rpm=1200,
                     feed=0.1)
        rough = Op("od_rough", "OD-rough", "p", z_start=0.0, z_end=z_end,
                   target_diameter=min(ds), profile=pts, **KW)
        fin = Op("od_finish", "OD-finish", "p", z_start=0.0, z_end=z_end,
                 target_diameter=min(ds), profile=pts)
        boring = False
    mv_r = paths.generate(rough, rough_t, stock)
    mv_f = paths.generate(fin, fin_t, stock)
    chk = paths.CollisionChecker(stock)
    chk.add(mv_r, boring=boring)
    chk.add(mv_f, boring=boring)
    blk = Block("(SHOWCASE - NOT MACHINE PROVEN - DRY RUN FIRST)",
                fin_t.tool_number, fin_t.rpm, mv_r + mv_f,
                rpm_ceiling=3000.0, tool_name=fin_t.name)
    nc = post_program([blk], tool_change=True, tool_change_prompt=False,
                      cycle_output=True, feed_mode="mm_per_min",
                      program_number=200)
    return mv_r, mv_f, stock, pts, chk, nc


def cell(title, prof, is_id, solid):
    img = Image.new("RGB", (CW, CH), "white")
    d = ImageDraw.Draw(img)
    d.text((8, 6), title, font=font_b, fill="#111")
    d.text((8, 27), prof if len(prof) < 84 else prof[:81] + "...",
           font=font_m, fill="#083")
    mv_r, mv_f, stock, pts, chk, nc = build(prof, is_id)
    cut = [m for m in mv_f if m.kind in ("feed", "cw", "ccw")
           and m.note.startswith("profile")]
    dev = max(dev_to_profile(pts, (m.x, m.z)) for m in cut) if cut else 9e9
    ok = dev < 0.02 and not chk.problems
    d.text((8, 44), f"{len(cut)} profile passes, deviation {dev:.4f} mm, "
                    f"NC {len(nc.splitlines())} lines, "
                    f"{'collision clean' if not chk.problems else 'COLLISION!'}",
           font=font, fill="#070" if ok else "#c00")

    dmax = max(p[0] for p in pts) / 2.0
    zmin = min(p[1] for p in pts)
    ox, oy = CW - 90, CH // 2 + 30
    sx = (CW - 140) / max(abs(zmin), 1e-6)
    sy = (CH - 150) / (2 * dmax)

    def P(x, z):
        return (ox + z * sx, oy - (x / 2.0) * sy)

    y0 = max(oy - (stock.diameter / 2) * sy, 62)
    y1 = min(oy + (stock.diameter / 2) * sy, CH - 34)
    x0 = max(ox + (-stock.length) * sx, 8)
    d.rectangle([x0, y0, min(ox + 3 * sx, CW - 8), y1], outline="#ddd")
    d.line([(x0, oy), (min(ox + 3 * sx, CW - 8), oy)], fill="#ccc")

    prof_px = [P(p[0], p[1]) for p in pts]
    for prev, cur in zip(prof_px, prof_px[1:]):   # hatched section
        d.polygon([prev, cur, (cur[0], 2 * oy - cur[1]),
                   (prev[0], 2 * oy - prev[1])], fill="#eef")
    prev = None
    for m in mv_r:
        p = P(m.x, m.z)
        if prev and m.kind in ("feed", "cw", "ccw"):
            d.line([prev, p], fill="#f80", width=1)
        prev = p
    prev = None
    for m in mv_f:
        p = P(m.x, m.z)
        if prev and m.kind == "rapid":
            d.line([prev, p], fill="#9cf")
        if prev and m.kind in ("feed", "cw", "ccw"):
            d.line([prev, p], fill="#c00", width=2)
        prev = p
    d.line(prof_px, fill="#0a0", width=3)
    d.line([(a, 2 * oy - b) for a, b in prof_px], fill="#0a0", width=3)
    d.text((8, CH - 20), "green = extracted profile   orange = roughing   "
                         "red = finish   blue = rapids", font=font_s,
           fill="#444")
    print(f"DEV {title}: {dev:.4f} mm, NC {len(nc.splitlines())} lines, "
          f"{'clean' if not chk.problems else chk.problems}", flush=True)
    return img, ok, nc


os.makedirs("/tmp/nc", exist_ok=True)
cols, rows = 2, 4
out = Image.new("RGB", (CW * cols, CH * rows), "white")
allok = True
for i, (t, prof, is_id, solid) in enumerate(DESIGNS):
    img, ok, nc = cell(t, prof, is_id, solid)
    allok = allok and ok
    out.paste(img, ((i % cols) * CW, (i // cols) * CH))
    fname = "/tmp/nc/" + t.split(" ")[0].lower() + ".nc"
    with open(fname, "w") as fh:
        fh.write("; profile: " + prof + "\n")
        fh.write(nc)
out.save("/tmp/designs.png")
print("wrote /tmp/designs.png", out.size, "ALL OK" if allok else "FAILURES",
      flush=True)
print("PROFILES:", flush=True)
for t, prof, is_id, _ in DESIGNS:
    print(f"  {t}: {prof}", flush=True)
print("SHOWCASE DONE", flush=True)
