"""Lathe benchmark: drum_screw_magnet_nfc finish program vs the proven file.

Reproduces the front-feature finish pass of drum_screw_magnet_nfc.nc (the
machine-proven BobCAD/Masso program) with LatheCAM's path generator and
post, then compares the cutting geometry move-by-move against the
reference (modal X/Z expanded, F words ignored).

Note: the last three moves (step to X7.194, R.397 arc, back-chamfer to
X6.101) are internal bore-bottom features; the proven program withdraws
axially through the part's pre-drilled centre hole. A LatheCAM document
od_finish op cannot mix OD and ID moves in one collision-checked op, so
this benchmark posts the path directly (generate + post_program) and
reports the match. For a real document, split those moves into an id op.

Run: ~/tools/freecad/squashfs-root/usr/bin/freecadcmd examples/benchmark_drum_screw.py
"""

import os
import re
import sys

import FreeCAD

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _cand in (os.path.join(_ROOT, "LatheCAM"), os.path.dirname(_ROOT)):
    if os.path.isfile(os.path.join(_cand, "LatheCAM", "__init__.py")):
        sys.path.insert(0, _cand)
        break

from LatheCAM.model import Stock, Tool, Op          # noqa: E402
from LatheCAM.paths import generate                 # noqa: E402
from LatheCAM.post import Block, post_program       # noqa: E402
from LatheCAM.program import load_tools             # noqa: E402

REF = os.path.join(_ROOT, "tests", "reference", "drum_screw_magnet_nfc.nc")
OUT = os.path.join(_HERE, "parts", "drum_screw_magnet_nfc_lathecam.nc")

# The proven path transcribed: straights as D,Z; the four R.397 corner arcs
# as D,Z,R (R>0 -> G02, matching the reference's G02 words).
PROFILE = ("16.815,0; 16.482,-0.166; 16.25,-0.447,0.397; 16.25,-5.55; "
           "15.913,-5.55; 15.444,-5.627,0.397; 15.309,-5.676; "
           "14.994,-5.936,0.397; 11,-18.917; 11,-20.65; 7.194,-20.65; "
           "6.808,-20.7,0.397; 6.101,-20.346")

MOVE = re.compile(r"^(G0?1|G0?[23])\b")


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
    tools = load_tools(os.path.join(_HERE, "tools_masso_g3.json"))
    tool = tools["CCGT boring"]
    stock = Stock(diameter=18.0, length=33.0, max_rpm=2000.0)
    pts = []
    for tok in PROFILE.split(";"):
        parts = [p.strip() for p in tok.strip().split(",")]
        pts.append(tuple(float(p) for p in parts))
    op = Op("od_finish", "CCGT boring", "front feature",
            z_start=0.0, z_end=-20.7, target_diameter=16.0, profile=pts)
    mv = generate(op, tool, stock)
    blk = Block("(MACHINE SETUP - 1-FEATURE LATHE TURNING-BASIC FINISH)",
                tool.tool_number, tool.rpm, mv, rpm_ceiling=2000.0,
                tool_name=tool.name, spindle_dir=tool.spindle_dir)
    gcode = post_program([blk], tool_change=True, tool_change_prompt=False,
                         cycle_output=True, feed_mode="mm_per_min",
                         program_number=100)
    with open(OUT, "w") as f:
        f.write(gcode)
    print(f"posted {len(gcode.splitlines())} lines -> {OUT}", flush=True)

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

    ours = [l.strip() for l in gcode.splitlines()
            if l.strip() and not l.startswith((";", "("))]
    expect = ["O0100", "G40 G18 G21 G90", "G53 G0 X0.", "G53 G0 Z0.",
              "T0008 M06", "G50 S2000", "G97 S2000 M04", "M08", "M64 P1",
              "M09", "M65 P1", "M05", "G53 G0 X0.", "G53 G0 Z0.", "M30"]
    it = iter(ours)
    ok = all(any(e == o for o in it) for e in expect)
    print(f"control lines in proven order: {'OK' if ok else 'MISMATCH'}",
          flush=True)
    verdict = ok and not missed
    print(f"BENCHMARK: {'PASS' if verdict else 'DIFF'}", flush=True)


main()
