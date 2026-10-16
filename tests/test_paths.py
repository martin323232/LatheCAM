"""Plain-python tests for LatheCAM paths + post (no FreeCAD needed).

Run: python3 tests/test_paths.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from LatheCAM.model import Stock, Tool, Op, thread_depth, thread_passes
from LatheCAM.paths import generate, Motion, check_collisions
from LatheCAM.post import Block, post_program

FAILED = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def tools():
    return {t.name: t for t in Tool.default_library()}


def test_thread_math():
    check("thread depth P2.0 ~1.227", abs(thread_depth(2.0) - 1.227) < 0.001)
    d = thread_passes(2.0)
    check("thread passes sum to depth", abs(sum(d) - thread_depth(2.0)) < 0.01,
          f"sum={sum(d)}")
    check("thread passes decreasing", all(d[i] >= d[i + 1] - 1e-9 for i in range(len(d) - 1)))
    check("no pass > 0.3", max(d) <= 0.301)


def test_full_program():
    stock = Stock(diameter=30.0, length=60.0)
    tl = tools()
    ops = [
        Op("face", "OD-rough", "Face stock", face_diameter=30.0, depth_per_pass=1.5),
        Op("od_rough", "OD-rough", "Rough OD to 20", z_end=-40.0,
           target_diameter=20.0, depth_per_pass=2.0, stock_to_leave=0.4),
        Op("od_finish", "OD-finish", "Finish OD 20", z_end=-40.0, target_diameter=20.0),
        Op("groove", "Groove 3mm", "Groove 3x2 at Z-25", z_start=-25.0, z_end=-25.0,
           groove_diameter=16.0, peck=0.5),
        Op("thread", "Thread 60deg", "M20x2 at face", z_start=0.0,
           thread_major_diameter=20.0, thread_pitch=2.0, thread_length=18.0),
        Op("part", "Part 2mm", "Part at Z-45", z_start=-45.0, peck=0.5),
    ]
    blocks = []
    all_moves = []
    for op in ops:
        op.validate(stock)
        tool = tl[op.tool]
        moves = generate(op, tool, stock)
        all_moves += moves
        blocks.append(Block(f"{op.type}: {op.comment}", tool.tool_number,
                            op.rpm or tool.rpm, moves, rpm_ceiling=stock.max_rpm))
    gcode = post_program(blocks, header="TEST PROGRAM", tool_change=False)

    # sanity on moves
    xs = [m.x for m in all_moves]
    zs = [m.z for m in all_moves]
    check("X within limits", min(xs) >= 0 and max(xs) <= stock.diameter + 4.01,
          f"min={min(xs)} max={max(xs)}")
    check("Z within limits", min(zs) >= -60 and max(zs) <= 6.0, f"min={min(zs)} max={max(zs)}")
    check("no negative X", min(xs) >= 0.0)

    # gcode structure
    check("starts G21", "G21" in gcode.splitlines()[0] or "G21" in gcode[:200])
    check("G95 feed mode", "G95" in gcode)
    check("G97 rpm mode", "G97 S" in gcode)
    check("ends M30", gcode.strip().endswith("M30"))
    check("spindle M03", "M03" in gcode)

    # threading: G32 has Z+F only; X depth comes from the G1 infeed before it
    g32 = [l for l in gcode.splitlines() if l.startswith("G32")]
    check("thread passes present", len(g32) >= len(thread_passes(2.0)), f"{len(g32)} lines")
    check("G32 has pitch F2", all("F2" in l for l in g32))
    check("G32 no X word", all("X" not in l for l in g32))
    depths = []
    last_x = None
    for l in gcode.splitlines():
        if l.startswith("G1") and "X" in l:
            last_x = float(l.split("X")[1].split()[0])
        elif l.startswith("G32") and last_x is not None:
            depths.append((20.0 - last_x) / 2.0)
    check("thread depth increasing", all(depths[i] <= depths[i + 1] + 1e-9 for i in range(len(depths) - 1)),
          str(depths))
    check("thread final depth ~1.227", abs(depths[-1] - thread_depth(2.0)) < 0.01, str(depths[-1]))

    # part-off reaches centre
    part_lines = [l for l in gcode.splitlines() if "part X" in l]
    check("part reaches X0", any("X0 " in l or "X0\n" in l or l.endswith("X0") for l in part_lines)
          or "X0 " in gcode, "no X0 plunge found")

    # bore ops
    stock2 = Stock(diameter=30.0, length=40.0, bore_diameter=10.0)
    bop = Op("id_rough", "Bore", "Bore to 20", z_end=-20.0, target_diameter=20.0,
             depth_per_pass=2.0, stock_to_leave=0.3)
    bm = generate(bop, tl["Bore"], stock2)
    bx = [m.x for m in bm]
    check("bore enters below bore dia", min(bx) < 10.0, f"min={min(bx)}")
    check("bore reaches target-leave", max(bx) >= 20.0 - 2 * 0.3 - 0.01, f"max={max(bx)}")

    return gcode


def test_css_mode():
    stock = Stock(diameter=30.0, length=60.0)
    tl = tools()
    op = Op("od_finish", "OD-finish", "css test", z_end=-20.0, target_diameter=20.0)
    moves = generate(op, tl["OD-finish"], stock)
    blk = [Block("css", 2, 1200, moves, rpm_ceiling=3000, surface_speed=150)]
    g = post_program(blk, css=True)
    check("css emits G96 D S", "G96 D3000 S150" in g)
    check("css feed mode G94", "G94" in g)


def test_collision_checker():
    """v0.1.6 review fixes: K1/K2 regression + clean-program proof."""
    stock = Stock(diameter=25.0, length=45.0)
    # old K1 pattern: diagonal rapid across the freshly cut surface
    old_k1 = [Motion("rapid", 22, 2), Motion("feed", 22, -30, 0.2, "pass1"),
              Motion("rapid", 28, -30, 0, "clear"),
              Motion("rapid", 19, 2, 0, "diagonal")]
    check("checker catches K1 diagonal", bool(check_collisions(old_k1, stock)))
    # new od_rough full-length and mid-length (K2) must be clean
    tl = tools()
    mv = generate(Op("od_rough", "OD-rough", "r", z_start=0.0, z_end=-25.0,
                     target_diameter=15.0, depth_per_pass=1.5, stock_to_leave=0.5),
                  tl["OD-rough"], stock)
    check("new od_rough rapids clean", not check_collisions(mv, stock),
          str(check_collisions(mv, stock)))
    mv2 = generate(Op("od_rough", "OD-rough", "r", z_start=-10.0, z_end=-25.0,
                      target_diameter=15.0, depth_per_pass=1.5, stock_to_leave=0.5),
                   tl["OD-rough"], stock)
    check("mid-length od_rough rapids clean (K2)", not check_collisions(mv2, stock),
          str(check_collisions(mv2, stock)))
    # thread lead-out beyond stock must raise (B2)
    try:
        generate(Op("thread", "Thread 60deg", "t", z_start=0.0, z_end=-44.0,
                    thread_major_diameter=20.0, thread_pitch=2.0,
                    thread_length=44.0), tl["Thread 60deg"], stock)
        check("thread lead-out validation (B2)", False, "no error raised")
    except ValueError as e:
        check("thread lead-out validation (B2)", "lead-out" in str(e), str(e))


def test_post_dialect():
    """v0.1.6: G18 default, M05 once, coolant, thread forces G97+G95."""
    stock = Stock(diameter=30.0, length=60.0)
    tl = tools()
    mv_fin = generate(Op("od_finish", "OD-finish", "f", z_end=-20.0,
                         target_diameter=20.0), tl["OD-finish"], stock)
    mv_thr = generate(Op("thread", "Thread 60deg", "t", z_start=-20.0, z_end=-40.0,
                         thread_major_diameter=20.0, thread_pitch=2.0,
                         thread_length=15.0), tl["Thread 60deg"], stock)
    blocks = [Block("finish", 2, 1200, mv_fin), Block("thread", 6, 250, mv_thr)]
    g = post_program(blocks, css=True)   # CSS on: thread block must still be G97+G95
    lines = g.splitlines()
    check("safe_start has G18", lines[0].startswith("G21 G90 G18"), lines[0])
    check("M05 emitted once", g.count("M05") == 1, f"count={g.count('M05')}")
    check("coolant M08/M09", "M08" in g and "M09" in g)
    thr_idx = next(i for i, l in enumerate(lines) if l.startswith("G32"))
    start = next(i for i, l in enumerate(lines) if l == "(thread)")
    seg = "\n".join(lines[start:thr_idx + 1])
    check("thread block forces G97 under CSS", "G97" in seg and "G96" not in seg)
    check("thread block forces G95 under CSS", "G95" in seg and "G94" not in seg)


if __name__ == "__main__":
    test_thread_math()
    gcode = test_full_program()
    test_css_mode()
    test_collision_checker()
    test_post_dialect()
    print("\n--- sample gcode (first 40 lines) ---")
    print("\n".join(gcode.splitlines()[:40]))
    print(f"\n{len(FAILED)} failed" if FAILED else "\nALL TESTS PASSED")
    sys.exit(1 if FAILED else 0)
