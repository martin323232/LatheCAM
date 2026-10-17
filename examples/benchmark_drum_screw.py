"""Lathe benchmark: drum_screw_magnet_nfc internal finish vs the proven file.

The whole profile is INTERNAL (boring): tool 8 CCGT, mounted upside down
(M04), cutting from the face: entry chamfer, D16.25 counterbore, step,
8.75 deg taper, D11 bore, bottom-face back-chamfer. The profile is
transcribed from the BobCAD output, so the move-by-move match proves
LatheCAM's post formatting and path discipline (entry/exit order, arc
output, number format) — NOT that LatheCAM generates this path from
part geometry.

The job is built as a real id_finish op and run through the same
CollisionChecker post_document uses (boring=True); the benchmark fails
if the checker reports anything. Exit discipline is compared against the
reference: after the last cut the tool withdraws axially through the
bore (G0 Z..) before any rapid X move.

Run: ~/tools/freecad/squashfs-root/usr/bin/freecadcmd examples/benchmark_drum_screw.py
"""

import os
import re
import sys

import FreeCAD  # noqa: F401  (freecadcmd)

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _cand in (os.path.join(_ROOT, "LatheCAM"), os.path.dirname(_ROOT)):
    if os.path.isfile(os.path.join(_cand, "LatheCAM", "__init__.py")):
        sys.path.insert(0, _cand)
        break

from LatheCAM.model import Stock, Op                    # noqa: E402
from LatheCAM.paths import generate, CollisionChecker  # noqa: E402
from LatheCAM.post import Block, post_program          # noqa: E402
from LatheCAM.program import load_tools                # noqa: E402

REF = os.path.join(_ROOT, "tests", "reference", "drum_screw_magnet_nfc.nc")
OUT = os.path.join(_HERE, "parts", "drum_screw_magnet_nfc_lathecam.nc")
HEADER = "(BENCHMARK OUTPUT - NOT A MACHINE-PROVEN PROGRAM - DRY RUN FIRST)"

# Transcribed from the proven BobCAD output: straights as D,Z; the four
# R.397 corner arcs as D,Z,R (R>0 -> G02, matching the reference G02 words).
PROFILE = ("16.815,0; 16.482,-0.166; 16.25,-0.447,0.397; 16.25,-5.55; "
           "15.913,-5.55; 15.444,-5.627,0.397; 15.309,-5.676; "
           "14.994,-5.936,0.397; 11,-18.917; 11,-20.65; 7.194,-20.65; "
           "6.808,-20.7,0.397; 6.101,-20.346")

MOVE = re.compile(r"^(G0?1|G0?[23])\b")
CUT = re.compile(r"^G0?[123]\b")


def parse_profile(text):
    pts = []
    for tok in text.split(";"):
        parts = [p.strip() for p in tok.strip().split(",")]
        pts.append(tuple(float(p) for p in parts))
    return pts


def parse_moves(path_or_text, is_text=False):
    """Cutting moves (G1/G2/G3) with modal X/Z carry-forward, F stripped."""
    text = path_or_text if is_text else open(
        path_or_text, encoding="utf-8", errors="replace").read()
    moves, x, z = [], None, None
    for line in text.splitlines():
        line = line.strip()
        if not MOVE.match(line):
            continue
        kind = line.split()[0]
        kind = {"G01": "G1", "G02": "G2", "G03": "G3"}.get(kind, kind)
        words = dict(re.findall(r"([XZR])\s*(-?[\d.]+)", line))
        if "X" in words:
            x = float(words["X"])
        if "Z" in words:
            z = float(words["Z"])
        r = float(words["R"]) if "R" in words else None
        moves.append((kind, x, z, r))
    return moves


def main():
    ok = True
    tools = load_tools(os.path.join(_HERE, "tools_masso_g3.json"))
    tool = tools["CCGT boring"]
    stock = Stock(diameter=18.0, length=33.0, bore_diameter=6.0,
                  max_rpm=2000.0)
    op = Op("id_finish", "CCGT boring", "drum screw bore finish",
            z_start=0.0, z_end=-20.7, profile=parse_profile(PROFILE))
    mv = generate(op, tool, stock)

    # C1: same CollisionChecker post_document uses, boring=True
    checker = CollisionChecker(stock)
    checker.add(mv, boring=True)
    if checker.problems:
        ok = False
        print("COLLISION CHECK FAILED:", flush=True)
        for p in checker.problems:
            print(f"  {p}", flush=True)
    else:
        print("collision check (boring=True): clean", flush=True)

    blk = Block("(MACHINE SETUP - 1-FEATURE LATHE TURNING-BASIC FINISH)",
                tool.tool_number, tool.rpm, mv, rpm_ceiling=2000.0,
                tool_name=tool.name, spindle_dir=tool.spindle_dir)
    gcode = post_program([blk], tool_change=True, tool_change_prompt=False,
                         cycle_output=True, feed_mode="mm_per_min",
                         program_number=100)
    with open(OUT, "w") as f:
        f.write(HEADER + "\n")
        f.write(gcode)
    print(f"posted {len(gcode.splitlines())} lines -> {OUT}", flush=True)

    # C2: cutting-move comparison (13 moves)
    ref_moves = parse_moves(REF)
    our_moves = parse_moves(gcode, is_text=True)
    matched, missed = 0, []
    for m in ref_moves:
        kind, x, z, r = m
        hit = any(
            k == kind and abs(a - x) < 1e-4 and abs(b - z) < 1e-4
            and ((r is None and rr is None)
                 or (r is not None and rr is not None and abs(rr - r) < 1e-4))
            for k, a, b, rr in our_moves)
        if hit:
            matched += 1
        else:
            missed.append(m)
    print(f"cutting moves: reference {len(ref_moves)}, ours {len(our_moves)}, "
          f"matched {matched}", flush=True)
    for m in missed:
        print(f"  MISS: {m}", flush=True)
    if matched != len(ref_moves) or missed:
        ok = False

    ours = [l.strip() for l in gcode.splitlines()
            if l.strip() and not l.startswith((";", "("))]
    expect = ["O0100", "G40 G18 G21 G90", "G53 G0 X0.", "G53 G0 Z0.",
              "T0008 M06", "G50 S2000", "G97 S2000 M04", "M08", "M64 P1",
              "M09", "M65 P1", "M05", "G53 G0 X0.", "G53 G0 Z0.", "M30"]
    it = iter(ours)
    ctl_ok = all(any(e == o for o in it) for e in expect)
    print(f"control lines in proven order: {'OK' if ctl_ok else 'MISMATCH'}",
          flush=True)
    ok = ok and ctl_ok

    # C3: exit discipline — after the last cutting move the first RAPID
    # must be axial (X unchanged, Z toward the face), like the reference
    # (G00 Z5. then G00 X17.522). The post emits modal X redundantly on
    # rapids, so compare positions, not words. The inward retract is a
    # feed move (A3), part of the cutting sequence.
    def parse_all(text):
        out, x, z = [], None, None
        for line in text.splitlines():
            line = line.strip()
            m = re.match(r"^(G0?[0-3])\b", line)
            if not m:
                continue
            kind = {"G01": "G1", "G02": "G2", "G03": "G3",
                    "G00": "G0"}.get(m.group(1), m.group(1))
            w = dict(re.findall(r"([XZR])\s*(-?[\d.]+)", line))
            if "X" in w:
                x = float(w["X"])
            if "Z" in w:
                z = float(w["Z"])
            out.append((kind, x, z))
        return out

    allmv = parse_all(gcode)
    i = max(j for j, (k, x, z) in enumerate(allmv)
            if k in ("G1", "G2", "G3"))
    x_after = allmv[i][1]
    rapids_after = [m for m in allmv[i + 1:] if m[0] == "G0"]
    c3 = bool(rapids_after) and abs(rapids_after[0][1] - x_after) < 1e-9 \
        and rapids_after[0][2] > 0
    c3b = len(rapids_after) > 1 and abs(rapids_after[1][2]
                                        - rapids_after[0][2]) < 1e-9
    print(f"exit: last feed ends X{x_after}; first rapid -> "
          f"X{rapids_after[0][1]} Z{rapids_after[0][2]} "
          f"({'axial, X unchanged' if c3 else 'DIAGONAL — WRONG'}); "
          f"second rapid radial: {'OK' if c3b else 'FAIL'}", flush=True)
    ok = ok and c3 and c3b

    print("\nlast 8 lines, ours vs reference:", flush=True)
    ref_lines = [l.rstrip() for l in open(REF) if l.strip()]
    our_lines = [l for l in gcode.splitlines() if l.strip()]
    for a, b in zip(our_lines[-8:], ref_lines[-8:]):
        print(f"  {a:<32} | {b.strip()}", flush=True)

    print(f"\nBENCHMARK: {'PASS' if ok else 'FAIL'}", flush=True)


main()
