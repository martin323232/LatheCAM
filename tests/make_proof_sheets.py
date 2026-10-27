"""Proof: does the generated toolpath actually lie on the extracted profile?

For every shape in both batteries: extract the profile, generate a FINISH op
from it, then (a) measure the max distance from every cutting endpoint to the
extracted profile polyline, and (b) render an overlay sheet.

freecadcmd: freecadcmd make_proof_sheets.py
Writes /tmp/proof_od.png, /tmp/proof_id.png and prints DEV lines.
Marker: PROOF DONE
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

F = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
try:
    font = ImageFont.truetype(F, 15)
    font_s = ImageFont.truetype(F, 12)
    font_b = ImageFont.truetype(F, 17)
except OSError:
    font = font_s = font_b = ImageFont.load_default()

CW, CH = 620, 400


def cyl(r, z0, z1):
    return Part.makeCylinder(r, z0 - z1, V(0, 0, z1), V(0, 0, 1))


def circle_edge(solid, r, z, tol=1e-6):
    for e in solid.Edges:
        c = e.Curve
        if (isinstance(c, Part.Circle) and abs(c.Radius - r) < tol
                and abs(c.Center.z - z) < tol and abs(c.Center.x) < tol):
            return e
    raise AssertionError(f"no circle edge r={r} z={z}")


def faces_where(shape, pred):
    subs = [f"Face{i+1}" for i, f in enumerate(shape.Faces) if pred(f)]
    return px.profile_from_segments(px.segments(shape, subs))


inner = lambda lim: (lambda f: f.BoundBox.XMax < lim)

_s1 = cyl(10, 0, -10).fuse(cyl(7, -10, -25))
_s2 = _s1.makeFillet(2.0, [circle_edge(_s1, 7.0, -10.0)])
_b3 = cyl(8, 0, -20)
_s3 = _b3.makeFillet(3.0, [circle_edge(_b3, 8.0, 0.0)])
_s4 = Part.makeCone(10.0, 6.0, 20.0, V(0, 0, 0), V(0, 0, -1))
_s7 = cyl(10, 0, -25).cut(cyl(11, -8, -11).cut(cyl(7, -8, -11)))
_b8 = cyl(10, 0, -20)
_s8 = _b8.makeChamfer(1.5, [circle_edge(_b8, 10.0, 0.0)])
_t1 = cyl(12, 0, -6).fuse(cyl(9, -6, -14)).fuse(cyl(6, -14, -20)).fuse(cyl(4, -20, -26))
_b = cyl(8, 0, -30)
_t2 = _b.cut(cyl(9, -12, -14).cut(cyl(5, -12, -14)))
_t2 = _t2.makeChamfer(1.5, [circle_edge(_t2, 8.0, 0.0)])
_t3 = Part.makeCone(10.0, 7.0, 12.0, V(0, 0, 0), V(0, 0, -1)).fuse(cyl(7, -12, -22))
_t4 = cyl(5, 0, -8).fuse(cyl(3, -8, -16)).fuse(cyl(5, -16, -20))
OD = [("S1 stepped shaft", px.auto_profile(_s1)),
      ("S2 shoulder fillet R2", px.auto_profile(_s2)),
      ("S3 end round R3", px.auto_profile(_s3)),
      ("S4 taper", px.auto_profile(_s4)),
      ("S7 groove", px.auto_profile(_s7)),
      ("S8 face chamfer", px.auto_profile(_s8)),
      ("T1 four-step", px.auto_profile(_t1)),
      ("T2 groove + chamfer", px.auto_profile(_t2)),
      ("T3 taper + step", px.auto_profile(_t3)),
      ("T4 spigot", px.auto_profile(_t4))]

_s5 = cyl(15, 0, -30).cut(cyl(8, 2, -32)).cut(cyl(11, 0, -12))
_s6 = _s5.makeFillet(1.5, [circle_edge(_s5, 11.0, -12.0)])
_t6 = cyl(15, 0, -30).cut(cyl(6, 2, -20))
_t6 = _t6.makeFillet(2.0, [circle_edge(_t6, 6.0, -20.0)])
_t7 = cyl(15, 0, -30).cut(cyl(8, 2, -32)).cut(cyl(11, -10, -12))
_t8 = cyl(15, 0, -25).cut(Part.makeCone(10.0, 6.0, 5.0, V(0, 0, 0), V(0, 0, -1))) \
        .cut(cyl(6, 0, -27))
_t9 = cyl(16, 0, -34).cut(cyl(6, 2, -36)).cut(cyl(9, 0, -8)).cut(cyl(12, 0, -4))
ID = [("S5 counterbore", faces_where(_s5, inner(12.0))),
      ("S6 bore fillet R1.5", faces_where(_s6, inner(12.0))),
      ("T6 blind bore + R2", faces_where(_t6, inner(12.0))),
      ("T7 internal groove", faces_where(_t7, inner(12.0))),
      ("T8 countersink", faces_where(_t8, inner(12.0))),
      ("T9 two counterbores", faces_where(_t9, inner(13.0)))]


def parse(prof):
    return [tuple(float(x) for x in p.split(",")) for p in prof.split(";")]


def pt_seg(p, a, b):
    px_, pz = p
    ax, az = a
    bx, bz = b
    dx, dz = bx - ax, bz - az
    L2 = dx * dx + dz * dz
    if L2 < 1e-15:
        return math.hypot(px_ - ax, pz - az)
    t = max(0.0, min(1.0, ((px_ - ax) * dx + (pz - az) * dz) / L2))
    return math.hypot(px_ - (ax + t * dx), pz - (az + t * dz))


def dev_to_profile(pts, p):
    xy = [(a[0], a[1]) for a in pts]      # profile rows may carry an R field
    return min(pt_seg(p, xy[i], xy[i + 1]) for i in range(len(xy) - 1))


def build(prof, is_id):
    pts = parse(prof)
    ds = [p[0] for p in pts]
    z_end = min(p[1] for p in pts)
    if is_id:
        bore, target = min(ds), max(ds)
        stock = Stock(diameter=bore + 18.0, length=abs(z_end) + 4,
                      bore_diameter=bore)
        tool = Tool("bor", 3, "bore", width=3.0, max_depth=abs(z_end),
                    rpm=900, feed=0.1)
        op = Op("id_finish", "bor", "p", z_start=0.0, z_end=z_end,
                target_diameter=target, profile=pts)
    else:
        stock = Stock(diameter=max(ds) + 3.0, length=abs(z_end) + 4)
        tool = Tool("fin", 2, "turn", rpm=1200, feed=0.1)
        op = Op("od_finish", "fin", "p", z_start=0.0, z_end=z_end,
                target_diameter=min(ds), profile=pts)
    return paths.generate(op, tool, stock), stock, pts


def cell(title, prof, is_id):
    img = Image.new("RGB", (CW, CH), "white")
    d = ImageDraw.Draw(img)
    d.text((8, 6), title, font=font_b, fill="#111")
    d.text((8, 27), prof if len(prof) < 76 else prof[:73] + "...",
           font=font_s, fill="#083")
    mv, stock, pts = build(prof, is_id)
    # only the profile-following passes must lie on the profile; engage /
    # retract / lift-out moves deliberately leave it
    cut = [m for m in mv if m.kind in ("feed", "cw", "ccw")
           and m.note.startswith("profile")]
    allcut = [m for m in mv if m.kind in ("feed", "cw", "ccw")]
    dev = max(dev_to_profile(pts, (m.x, m.z)) for m in cut) if cut else 9e9
    ok = dev < 0.02
    d.text((8, 45), f"{len(cut)}/{len(allcut)} profile passes, max deviation "
                    f"from profile: {dev:.4f} mm", font=font,
           fill="#070" if ok else "#c00")

    dmax = max(p[0] for p in pts) / 2.0
    zmin = min(p[1] for p in pts)
    ox, oy = CW - 90, CH // 2 + 20
    sx = (CW - 130) / max(abs(zmin), 1e-6)
    sy = (CH - 130) / (2 * dmax)

    def P(x, z):
        return (ox + z * sx, oy - (x / 2.0) * sy)

    # stock blank (clamped to the cell)
    y0 = max(oy - (stock.diameter / 2) * sy, 60)
    y1 = min(oy + (stock.diameter / 2) * sy, CH - 30)
    x0 = max(ox + (-stock.length) * sx, 8)
    d.rectangle([x0, y0, min(ox + 3 * sx, CW - 8), y1], outline="#ccc", width=1)
    d.line([(x0, oy), (min(ox + 3 * sx, CW - 8), oy)], fill="#bbb", width=1)

    prof_px = [P(p[0], p[1]) for p in pts]
    d.line(prof_px, fill="#0a0", width=4)
    d.line([(a, 2 * oy - b) for a, b in prof_px], fill="#0a0", width=4)
    prev = None
    for m in mv:
        p = P(m.x, m.z)
        if prev and m.kind == "rapid":
            d.line([prev, p], fill="#9cf", width=1)
        if prev and m.kind in ("feed", "cw", "ccw"):
            d.line([prev, p], fill="#c00", width=2)
        prev = p
    for cx, cy in prof_px:
        d.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], outline="#060")
    d.text((8, CH - 22), "green = extracted profile   red = finish toolpath  "
                         "  blue = rapids", font=font_s, fill="#444")
    return img, dev, ok


def sheet(items, path, is_id=False, cols=2):
    rows = (len(items) + cols - 1) // cols
    out = Image.new("RGB", (CW * cols, CH * rows), "white")
    worst = 0.0
    for i, (title, prof) in enumerate(items):
        img, dev, ok = cell(title, prof, is_id)
        worst = max(worst, dev)
        print(f"DEV {title}: {dev:.4f} mm {'OK' if ok else 'TOO BIG'}",
              flush=True)
        out.paste(img, ((i % cols) * CW, (i // cols) * CH))
    out.save(path)
    print("wrote", path, out.size, f"worst={worst:.4f} mm", flush=True)


sheet(OD, "/tmp/proof_od.png")
sheet(ID, "/tmp/proof_id.png", is_id=True)
print("PROOF DONE", flush=True)
