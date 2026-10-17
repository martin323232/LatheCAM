"""Plain-python tests for LatheCAM paths + post (no FreeCAD needed).

Run: python3 tests/test_paths.py
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.join(_here, "..", "LatheCAM"), os.path.dirname(_here)):
    # dev layout: repo/LatheCAM/LatheCAM/pkg | release layout: repo/LatheCAM/pkg
    if os.path.isfile(os.path.join(_cand, "LatheCAM", "__init__.py")):
        sys.path.insert(0, _cand)
        break

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

    # gcode structure (v0.2.0: O-header + reference start line, mm/min default)
    check("O0100 present", "O0100" in gcode)
    check("start line G40 G18 G21 G90", "G40 G18 G21 G90" in gcode)
    check("G94 feed mode (mm/min default)", "G94" in gcode)
    check("G95 in thread block", "G95" in gcode)
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
    check("part reaches X0", any("X0 " in l or "X0." in l or l.endswith("X0.")
                                 for l in part_lines)
          or "X0." in gcode, "no X0 plunge found")

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
    check("css emits G50 clamp + G96", "G50 S3000" in g and "G96 S150 M03" in g)
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
    check("O0100 first line", lines[0] == "O0100", lines[0])
    check("start line reference order", lines[1] == "G40 G18 G21 G90", lines[1])
    check("M05 emitted once", g.count("M05") == 1, f"count={g.count('M05')}")
    check("coolant M08/M09", "M08" in g and "M09" in g)
    thr_idx = next(i for i, l in enumerate(lines) if l.startswith("G32"))
    start = next(i for i, l in enumerate(lines) if l == "(thread)")
    seg = "\n".join(lines[start:thr_idx + 1])
    check("thread block forces G97 under CSS", "G97" in seg and "G96" not in seg)
    check("thread block forces G95 under CSS", "G95" in seg and "G94" not in seg)


def test_review_round2():
    """v0.1.8 review fixes R1-R7 — each check fails on 0.1.6."""
    tl = tools()

    # R1: od_rough mid-length start must never cut the collar in (z_start, 0]
    stock = Stock(diameter=30.0, length=60.0)
    mv = generate(Op("od_rough", "OD-rough", "r", z_start=-10.0, z_end=-30.0,
                     target_diameter=20.0, depth_per_pass=2.0, stock_to_leave=0.5),
                  tl["OD-rough"], stock)
    bad = [mo for mo in mv
           if mo.kind == "feed" and -10.0 < mo.z <= 0.0 and mo.x < 30.0 - 1e-9]
    check("R1: no feed inside collar zone (z_start, 0]", not bad, str(bad[:2]))

    # R2: tool change stops spindle + coolant, uses M00 with tool name
    mv1 = generate(Op("od_finish", "OD-finish", "f", z_end=-20.0,
                      target_diameter=20.0), tl["OD-finish"], stock)
    mv2 = generate(Op("part", "Part 2mm", "p", z_start=-45.0, peck=0.5),
                   tl["Part 2mm"], stock)
    b1 = Block("(op1)", 2, 1200, mv1, tool_name="OD-finish")
    b2 = Block("(op2)", 5, 400, mv2, tool_name="Part 2mm")
    g = post_program([b1, b2], tool_change=True)
    lines = g.splitlines()
    i_m03 = [i for i, l in enumerate(lines) if l.endswith("M03")][1]   # op2 spindle on
    seg = lines[:i_m03]
    i_m09 = max(i for i, l in enumerate(seg) if l == "M09")
    i_m05 = max(i for i, l in enumerate(seg) if l == "M05")
    i_t = max(i for i, l in enumerate(seg) if l.startswith("T0005 M06"))
    i_m00 = max(i for i, l in enumerate(seg) if l.startswith("M00"))
    check("R2: M09 -> M05 -> T0005 M06 -> M00 -> M03 order",
          i_m09 < i_m05 < i_t < i_m00 < i_m03,
          f"{i_m09},{i_m05},{i_t},{i_m00},{i_m03}")
    check("R2: M00 names the tool", "M00 (LOAD T0005 Part 2mm)" in g)
    check("R2: no M01 anywhere", "M01" not in g)

    # R3: wide groove (multi-centre) rapids straight out between centres
    mv = generate(Op("groove", "Groove 3mm", "g", z_start=-10.0, z_end=-19.0,
                     groove_diameter=24.0, peck=0.5), tl["Groove 3mm"], stock)
    probs = check_collisions(mv, stock, tool_width=3.0, z_reference="left")
    check("R3: wide groove collision-free", not probs, str(probs[:2]))

    # R4: width-aware checker catches kerf-edge overlap the point model misses
    stock25 = Stock(diameter=25.0, length=45.0)
    base = [Motion("rapid", 34, 2),
            Motion("rapid", 27, -12),
            Motion("feed", 16, -12, 0.06, "groove plunge"),
            Motion("rapid", 27, -12, 0, "peck retract")]
    mv = base + [Motion("rapid", 20, -9.2, 0, "centre in air, edge in wall")]
    check("R4: width model catches kerf-edge hit",
          bool(check_collisions(mv, stock25, tool_width=3.0, z_reference="left")))
    mv = base + [Motion("rapid", 20, -12.0, 0, "span = exactly the kerf")]
    check("R4: width model clears rapid fully inside kerf",
          not check_collisions(mv, stock25, tool_width=3.0, z_reference="left"))

    # R5: boring cuts toward the chuck (-Z), no diagonal feeds
    stock_b = Stock(diameter=30.0, length=40.0, bore_diameter=10.0)
    for op_type in ("id_rough", "id_finish"):
        mv = generate(Op(op_type, "Bore", "b", z_start=0.0, z_end=-20.0,
                         target_diameter=20.0, depth_per_pass=2.0,
                         stock_to_leave=0.3), tl["Bore"], stock_b)
        prev, dz_pos, diag = None, [], []
        for mo in mv:
            if mo.kind == "feed" and prev is not None:
                dz, dx = mo.z - prev[1], mo.x - prev[0]
                if dz > 1e-9:
                    dz_pos.append(mo)
                if abs(dz) > 1e-9 and abs(dx) > 1e-9:
                    diag.append(mo)
            prev = (mo.x, mo.z)
        check(f"R5: {op_type} cuts -Z only", not dz_pos, str(dz_pos[:2]))
        check(f"R5: {op_type} no diagonal feeds", not diag, str(diag[:2]))

    # R6: groove/part programmed Z offset for the left (chuck-side) corner
    mv = generate(Op("groove", "Groove 3mm", "g", z_start=-25.0, z_end=-25.0,
                     groove_diameter=16.0, peck=0.5), tl["Groove 3mm"], stock)
    zs = {round(mo.z, 3) for mo in mv if mo.kind == "feed"}
    check("R6: groove Z = centre - w/2 (left ref)", zs == {-26.5}, str(zs))
    mv = generate(Op("part", "Part 2mm", "p", z_start=-45.0, peck=0.5),
                  tl["Part 2mm"], stock)
    zs = {round(mo.z, 3) for mo in mv if mo.kind == "feed"}
    check("R6: part kerf centred on op Z (left ref)", zs == {-46.0}, str(zs))

    # R7: thread lead-out must not pass the shoulder/undercut
    ok = Op("thread", "Thread 60deg", "t", z_start=0.0,
            thread_major_diameter=20.0, thread_pitch=2.0, thread_length=25.0,
            thread_z_limit=-30.0)
    try:
        generate(ok, tl["Thread 60deg"], stock)
        check("R7: thread within shoulder limit passes", True)
    except ValueError as e:
        check("R7: thread within shoulder limit passes", False, str(e))
    bad = Op("thread", "Thread 60deg", "t", z_start=0.0,
             thread_major_diameter=20.0, thread_pitch=2.0, thread_length=29.0,
             thread_z_limit=-30.0)
    try:
        generate(bad, tl["Thread 60deg"], stock)
        check("R7: thread past shoulder raises", False, "no error raised")
    except ValueError as e:
        check("R7: thread past shoulder raises", "shoulder" in str(e), str(e))


def test_tool_library():
    import json
    import tempfile
    from LatheCAM import program as prog

    tl = tools()
    stock = Stock(diameter=25.0, length=45.0)

    # tool_change_prompt=False: T.. M06 kept, no M00 stop (automatic turret)
    mv1 = generate(Op("od_finish", "OD-finish", "f", z_end=-20.0,
                      target_diameter=20.0), tl["OD-finish"], stock)
    mv2 = generate(Op("part", "Part 2mm", "p", z_start=-45.0, peck=0.5),
                   tl["Part 2mm"], stock)
    g = post_program([Block("(op1)", 2, 1200, mv1), Block("(op2)", 5, 400, mv2)],
                     tool_change=True, tool_change_prompt=False)
    check("prompt off: T.. M06 kept", "T0005 M06" in g)
    check("prompt off: no M00", "M00" not in g)

    # user library: override built-in by name + add a custom tool
    upath = os.path.join(tempfile.mkdtemp(), "tools.json")
    with open(upath, "w") as f:
        json.dump([
            {"name": "OD-finish", "tool_number": 22, "kind": "turn",
             "width": 3.0, "nose_radius": 0.8, "max_depth": 20.0,
             "rpm": 1500.0, "feed": 0.08, "thread_pitch": 0.0,
             "z_reference": "centre"},
            {"name": "My groover", "tool_number": 44, "kind": "groove",
             "width": 2.0, "nose_radius": 0.2, "max_depth": 5.0,
             "rpm": 600.0, "feed": 0.05, "thread_pitch": 0.0,
             "z_reference": "left"},
        ], f)
    orig = prog.user_tool_library_path
    prog.user_tool_library_path = lambda: upath
    try:
        merged = prog.load_tools()
        check("user lib: override wins by name",
              merged["OD-finish"].tool_number == 22
              and merged["OD-finish"].rpm == 1500.0)
        check("user lib: custom tool added",
              "My groover" in merged
              and merged["My groover"].tool_number == 44)
        check("user lib: untouched built-ins remain", "OD-rough" in merged)
        with open(upath, "w") as f:
            json.dump([{"name": "Dup", "tool_number": 1, "kind": "turn"}], f)
        try:
            prog.load_tools()
            check("user lib: duplicate T# raises", False, "no error raised")
        except ValueError as e:
            check("user lib: duplicate T# raises", "duplicate" in str(e), str(e))
    finally:
        prog.user_tool_library_path = orig



# ---------------------------------------------------------------- v0.3.0

def test_arc_samples():
    """A4: arc collision sampling lies on the true circle."""
    import math as _m
    from LatheCAM.paths import _arc_samples
    p1 = Motion("rapid", 20.0, 0.0)
    p2 = Motion("cw", 28.0, -4.0, radius=4.0)
    pts = _arc_samples(p1, p2, 0.05)
    check("A4: arc samples lie on the circle (center 10,-4 r4)",
          all(abs(_m.hypot(x / 2 - 10.0, z + 4.0) - 4.0) < 1e-6
              for x, z in pts))
    check("A4: samples include both endpoints",
          abs(pts[0][0] - 20) < 1e-9 and abs(pts[-1][0] - 28) < 1e-9
          and abs(pts[-1][1] + 4) < 1e-9)


def test_arc_collision_clean():
    """A4: an OD arc path posts collision-free through the checker."""
    stock = Stock(diameter=30.0, length=60.0)
    tool = Tool("fin", 2, "turn")
    op = Op("od_finish", "fin", "a", z_start=0.0, z_end=-10.0,
            profile=[(30.0, 0.0), (20.0, -10.0, 12.0)])
    probs = check_collisions(generate(op, tool, stock), stock)
    check("A4: arc path collision-free", not probs, str(probs))


def test_nose_offset():
    """N2: with nose comp the checker records material conservatively."""
    from LatheCAM.paths import CollisionChecker
    stock = Stock(diameter=30.0, length=60.0)
    mv = [Motion("feed", 20.0, 0.0, 0.1), Motion("feed", 20.0, -30.0, 0.1)]
    c = CollisionChecker(stock)
    c.add(mv, nose_radius=0.8)
    j = c._bucket(-15.0)
    check("N2: nose comp records conservative core (20+2*0.8)",
          abs(c.core[j] - 21.6) < 1e-6, str(c.core[j]))


if __name__ == "__main__":
    test_thread_math()
    gcode = test_full_program()
    test_css_mode()
    test_collision_checker()
    test_post_dialect()
    test_review_round2()
    test_tool_library()
    test_arc_samples()
    test_arc_collision_clean()
    test_nose_offset()
    print("\n--- sample gcode (first 40 lines) ---")
    print("\n".join(gcode.splitlines()[:40]))
    print(f"\n{len(FAILED)} failed" if FAILED else "\nALL TESTS PASSED")
    sys.exit(1 if FAILED else 0)
