"""v0.2.0 tests: Masso proven post conventions (A1-A9), bore fixes
(B1-B3), reference regression (C1), user tool library (D).

Every check fails on 0.1.9 and passes on 0.2.0.
Run: python3 tests/test_post.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "LatheCAM"))

from LatheCAM.model import Stock, Tool, Op
from LatheCAM.paths import generate, Motion, check_collisions
from LatheCAM.post import Block, post_program, _fmt

FAILED = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def tools():
    return {t.name: t for t in Tool.default_library()}


def lines_of(g):
    return [l for l in g.splitlines() if l.strip()]


def subseq(needles, hay):
    it = iter(hay)
    return all(any(n == h for h in it) for n in needles)


def _finish_moves(stock=None, tl=None):
    stock = stock or Stock(diameter=30.0, length=60.0)
    tl = tl or tools()
    return generate(Op("od_finish", "OD-finish", "f", z_end=-20.0,
                       target_diameter=20.0), tl["OD-finish"], stock)


# ---------------------------------------------------------------- A2

def test_a2_tool_word():
    mv = _finish_moves()
    g = post_program([Block("op", 8, 2000, mv)], tool_change=True,
                     tool_change_prompt=False)
    check("A2: tool 8 posts T0008 M06", "T0008 M06" in g)
    g1 = post_program([Block("op", 1, 2000, mv)], tool_change=True,
                      tool_change_prompt=False)
    check("A2: tool 1 posts T0001 M06", "T0001 M06" in g1)
    check("A2: no 2-digit T word", "T08 M06" not in g)


# ---------------------------------------------------------------- A1

def test_a1_home_toolchange():
    tl = tools()
    mv1 = _finish_moves(tl=tl)
    mv2 = generate(Op("part", "Part 2mm", "p", z_start=-45.0, peck=0.5),
                   tl["Part 2mm"], stock := Stock(diameter=30.0, length=60.0))
    g = post_program([Block("(op1)", 2, 1200, mv1, tool_name="OD-finish"),
                      Block("(op2)", 5, 400, mv2, tool_name="Part 2mm")],
                     tool_change=True)
    lines = lines_of(g)
    i_hx = lines.index("G53 G0 X0.")
    i_hz = lines.index("G53 G0 Z0.")
    i_t1 = lines.index("T0002 M06")
    check("A1: first change homes X then Z then T",
          i_hx + 1 == i_hz and i_hz + 1 == i_t1,
          f"{i_hx},{i_hz},{i_t1}")
    check("A1: nothing stopped before first change",
          "M09" not in lines[:i_hx] and "M05" not in lines[:i_hx])
    i_t2 = lines.index("T0005 M06")
    seg = lines[i_t1:i_t2]
    check("A1: later change stops spindle/coolant + homes",
          all(x in seg for x in ("M09", "M05", "G53 G0 X0.", "G53 G0 Z0.")))
    i_m09 = max(i for i, l in enumerate(seg) if l == "M09")
    i_m05 = max(i for i, l in enumerate(seg) if l == "M05")
    check("A1: M09 -> M05 -> home order",
          i_m09 < i_m05 < seg.index("G53 G0 X0."))
    i_end = max(i for i, l in enumerate(lines) if l == "M05")
    tail = lines[i_end:]
    check("A1: end = M05, home, M30",
          tail == ["M05", "G53 G0 X0.", "G53 G0 Z0.", "M30"], str(tail))
    check("A1: M09 right before end M05", lines[i_end - 1] == "M09")
    check("A1: no tool change at program end", "M06" not in tail)


# ---------------------------------------------------------------- A3

def test_a3_feed_mode():
    tl = tools()
    op = Op("od_finish", "OD-finish", "f", z_end=-20.0, target_diameter=20.0,
            feed=0.04, rpm=2500.0)
    mv = generate(op, tl["OD-finish"], Stock(diameter=30.0, length=60.0))
    g = post_program([Block("op", 2, 2500, mv)])
    check("A3: default mm/min emits G94", "G94" in g)
    check("A3: 0.04 x 2500 -> F100.", "F100." in g)
    check("A3: no G95 in mm/min program", "G95" not in g)
    g2 = post_program([Block("op", 2, 2500, mv)], feed_mode="mm_per_rev")
    check("A3: mm_per_rev emits G95 + mm/rev feed",
          "G95" in g2 and "F0.04" in g2 and "G94" not in g2)
    # thread sandwich: G94 -> G95 (thread block only) -> G94
    mv_fin = _finish_moves(tl=tl)
    mv_thr = generate(Op("thread", "Thread 60deg", "t", z_start=-20.0,
                         z_end=-40.0, thread_major_diameter=20.0,
                         thread_pitch=2.0, thread_length=15.0),
                      tl["Thread 60deg"], Stock(diameter=30.0, length=60.0))
    g3 = post_program([Block("turn1", 2, 1200, mv_fin),
                       Block("thread", 6, 250, mv_thr),
                       Block("turn2", 2, 1200, mv_fin)])
    lines = lines_of(g3)
    modes = [l for l in lines if l in ("G94", "G95")]
    check("A3: thread sandwich G94/G95/G94", modes == ["G94", "G95", "G94"],
          str(modes))
    g32 = [l for l in lines if l.startswith("G32")]
    check("A3: G32 F is the pitch", g32 and all("F2." in l for l in g32))


# ---------------------------------------------------------------- A4

def test_a4_spindle_start():
    mv = _finish_moves()
    g = post_program([Block("op", 2, 1200, mv, rpm_ceiling=2000)])
    lines = lines_of(g)
    i = lines.index("G50 S2000")
    check("A4: G50 clamp then one-line G97 + M03",
          lines[i + 1] == "G97 S1200 M03", str(lines[i:i + 2]))
    gc = post_program([Block("op", 2, 1200, mv, rpm_ceiling=3000,
                             surface_speed=150)], css=True)
    check("A4: CSS drops D word", "G96 S150 M03" in gc and "G96 D" not in gc)
    check("A4: CSS keeps G50 clamp", "G50 S3000" in gc)


# ---------------------------------------------------------------- A5

def test_a5_spindle_dir():
    mv = _finish_moves()
    g = post_program([Block("op", 8, 2000, mv, spindle_dir="ccw")])
    check("A5: ccw tool posts M04", "G97 S2000 M04" in g)
    g2 = post_program([Block("op", 8, 2000, mv)])
    check("A5: default cw posts M03", "G97 S2000 M03" in g2)


# ---------------------------------------------------------------- A6

def test_a6_program_number_cycle():
    mv = _finish_moves()
    g = post_program([Block("op", 8, 2000, mv)], cycle_output=True)
    lines = lines_of(g)
    check("A6: O0100 first line", lines[0] == "O0100", lines[0])
    check("A6: start line second", lines[1] == "G40 G18 G21 G90", lines[1])
    i64 = lines.index("M64 P1")
    check("A6: M64 P1 right after first rapid",
          lines[i64 - 1].startswith("G0 "), str(lines[i64 - 1:i64 + 1]))
    i09 = max(i for i, l in enumerate(lines) if l == "M09")
    check("A6: M65 P1 right after final M09", lines[i09 + 1] == "M65 P1")
    check("A6: ProgramNumber override -> O0042",
          lines_of(post_program([Block("op", 8, 2000, mv)],
                                program_number=42))[0] == "O0042")
    g3 = post_program([Block("op", 8, 2000, mv)])
    check("A6: cycle output off by default", "M64" not in g3 and "M65" not in g3)


# ---------------------------------------------------------------- A7

def test_a7_same_tool_dedup():
    tl = tools()
    stock = Stock(diameter=30.0, length=60.0)
    mv1 = _finish_moves(stock, tl)
    mv2 = generate(Op("od_rough", "OD-finish", "r", z_end=-30.0,
                      target_diameter=22.0, depth_per_pass=2.0,
                      stock_to_leave=0.5), tl["OD-finish"], stock)
    g = post_program([Block("op1", 2, 1200, mv1), Block("op2", 2, 1200, mv2)],
                     tool_change=True)
    check("A7: same tool -> single T word", g.count("M06") == 1)
    check("A7: same tool -> home only at first change + end",
          g.count("G53 G0 X0.") == 2, f"count={g.count('G53 G0 X0.')}")
    check("A7: same tool same rpm -> no extra G97", g.count("G97 S1200") == 1)
    g2 = post_program([Block("op1", 2, 1200, mv1), Block("op2", 2, 2500, mv2)],
                      tool_change=True)
    check("A7: speed change emits G97 S2500", "G97 S2500" in g2)
    check("A7: speed change does not re-home or re-T",
          g2.count("M06") == 1 and g2.count("G53 G0 X0.") == 2)


# ---------------------------------------------------------------- A8/A9

def test_a8_number_format():
    check("A8: whole keeps dot", _fmt(31.0) == "31.")
    check("A8: zero -> 0.", _fmt(0.0) == "0.")
    check("A8: fractional trimmed", _fmt(17.522) == "17.522")
    check("A8: negative fractional", _fmt(-0.166) == "-0.166")
    check("A8: negative whole keeps dot", _fmt(-20.0) == "-20.")
    mv = _finish_moves()
    g = post_program([Block("op", 2, 1200, mv)])
    check("A8: X20. in output", "X20." in g)
    check("A8: Z-20. in output", "Z-20." in g)


def test_a9_start_line():
    lines = lines_of(post_program([]))
    check("A9: reference start line", lines[1] == "G40 G18 G21 G90", lines[1])


# ---------------------------------------------------------------- B1/B2

def test_b1_bore_entry():
    stock = Stock(diameter=30.0, length=60.0, bore_diameter=10.0)
    tl = tools()
    for op_type in ("id_rough", "id_finish"):
        op = Op(op_type, "Bore", "b", z_start=-10.0, z_end=-20.0,
                target_diameter=16.0, depth_per_pass=2.0, stock_to_leave=0.0)
        mv = generate(op, tl["Bore"], stock)
        bad = [mo for mo in mv if mo.kind == "feed"
               and -10.0 < mo.z < 0.0 and mo.x > 10.0 + 1e-9]
        check(f"B1: {op_type} no feed above bore dia in (z_start, 0]",
              not bad, str(bad[:2]))
        cuts = [mo for mo in mv if mo.kind == "feed" and mo.x > 10.0 + 1e-9]
        check(f"B1: {op_type} first cut exactly at z_start",
              cuts and abs(cuts[0].z + 10.0) < 1e-9,
              str(cuts[0] if cuts else None))


def test_b2_bore_retract():
    stock = Stock(diameter=30.0, length=60.0, bore_diameter=10.0)
    tl = tools()
    op = Op("id_rough", "Bore", "b", z_start=-10.0, z_end=-20.0,
            target_diameter=16.0, depth_per_pass=2.0, stock_to_leave=0.0)
    probs = check_collisions(generate(op, tl["Bore"], stock), stock,
                             boring=True)
    check("B2: id_rough rapids clean (B1 example)", not probs, str(probs[:2]))
    op2 = Op("id_finish", "Bore", "bf", z_start=-10.0, z_end=-20.0,
             target_diameter=16.0)
    probs2 = check_collisions(generate(op2, tl["Bore"], stock), stock,
                              boring=True)
    check("B2: id_finish rapids clean", not probs2, str(probs2[:2]))


# ---------------------------------------------------------------- B3

def test_b3_face_allowance():
    stock = Stock(diameter=30.0, length=60.0, face_allowance=2.0)
    check("B3: safe_z above allowance", abs(stock.safe_z() - 4.0) < 1e-9,
          str(stock.safe_z()))
    mv = [Motion("rapid", 34, 4),
          Motion("rapid", 15, 1.0, 0, "diagonal into facing allowance")]
    probs = check_collisions(mv, stock)
    check("B3: rapid into facing allowance flagged", bool(probs), str(probs[:1]))
    tl = tools()
    op = Op("face", "OD-finish", "f", face_diameter=30.0, face_depth=2.0,
            depth_per_pass=1.0)
    probs2 = check_collisions(generate(op, tl["OD-finish"], stock), stock)
    check("B3: face op itself collision-free", not probs2, str(probs2[:2]))
    # without allowance the same diagonal is air (regression guard)
    stock0 = Stock(diameter=30.0, length=60.0)
    mv0 = [Motion("rapid", 34, 2), Motion("rapid", 15, 1.0, 0, "air")]
    check("B3: no false positive without allowance",
          not check_collisions(mv0, stock0))


# ---------------------------------------------------------------- C1

# control lines as they appear IN the proven reference file (subsequence;
# the final T0001 M06 turret-index flaw is simply not listed)
REF_EXPECTED = ["O0100", "G40 G18 G21 G90", "G53 G0 X0.", "G53 G0 Z0.",
                "T0008 M06", "G50 S2000", "G97 S2000 M04", "M08", "M64 P1",
                "M09", "M65 P1", "G53 G0 X0.", "G53 G0 Z0.", "M30"]
# our output: intended difference — M05 BEFORE the final home moves
OUR_EXPECTED = ["O0100", "G40 G18 G21 G90", "G53 G0 X0.", "G53 G0 Z0.",
                "T0008 M06", "G50 S2000", "G97 S2000 M04", "M08", "M64 P1",
                "M09", "M65 P1", "M05", "G53 G0 X0.", "G53 G0 Z0.", "M30"]


def test_c1_reference_regression():
    ref_path = os.path.join(os.path.dirname(__file__), "reference",
                            "drum_screw_magnet_nfc.nc")
    check("C1: reference file present", os.path.exists(ref_path))
    if not os.path.exists(ref_path):
        return
    with open(ref_path, "r", encoding="utf-8", errors="replace") as f:
        ref = [l.strip() for l in f.read().replace("\r\n", "\n").splitlines()
               if l.strip()]
    check("C1: reference contains expected control lines in order",
          subseq(REF_EXPECTED, ref),
          str([n for n in REF_EXPECTED if n not in ref]))
    # one-op equivalent: tool 8, ccw, 2000 rpm, MaxRPM 2000, cycle output on
    stock = Stock(diameter=18.0, length=33.0, max_rpm=2000.0)
    tool = Tool("CCGT boring", 8, "bore", rpm=2000.0, feed=0.025,
                spindle_dir="ccw")
    op = Op("od_finish", "CCGT boring", "front feature", z_start=0.0,
            z_end=-21.0, target_diameter=16.0)
    mv = generate(op, tool, stock)
    blk = Block("( (MACHINE SETUP - 1-FEATURE LATHE TURNING-BASIC FINISH) )",
                8, 2000.0, mv, rpm_ceiling=2000.0, tool_name="CCGT boring",
                spindle_dir="ccw")
    g = post_program([blk], tool_change=True, tool_change_prompt=False,
                     cycle_output=True)
    lines = lines_of(g)
    check("C1: our output matches reference control-line order",
          subseq(OUR_EXPECTED, lines),
          str([n for n in OUR_EXPECTED if n not in lines]))
    check("C1: intended difference — G94 at block start", "G94" in g)
    check("C1: no final T0001 M06 (BobCAD flaw not copied)",
          g.count("M06") == 1)


# ---------------------------------------------------------------- D

def test_d_user_library():
    from LatheCAM import program as prog
    path = os.path.join(os.path.dirname(__file__), "..", "examples",
                        "tools_masso_g3.json")
    check("D: examples/tools_masso_g3.json present", os.path.exists(path))
    if not os.path.exists(path):
        return
    tl = prog.load_tools(path)
    nums = [t.tool_number for t in tl.values()]
    check("D: no duplicate tool numbers", len(nums) == len(set(nums)), str(nums))
    check("D: five tools", len(tl) == 5, str(sorted(tl)))
    check("D: T8 spindle_dir ccw", tl["CCGT boring"].spindle_dir == "ccw")
    check("D: VERIFY notes present",
          all("VERIFY" in tl[n].note for n in ("Thread 60deg", "CCGT boring",
                                               "Part")))
    stock = Stock(diameter=18.0, length=33.0, max_rpm=2000.0)
    tool = tl["CCGT boring"]
    op = Op("od_finish", "CCGT boring", "bore", z_start=0.0, z_end=-20.0,
            target_diameter=16.0)
    mv = generate(op, tool, stock)
    blk = Block("t8", tool.tool_number, tool.rpm, mv, rpm_ceiling=2000.0,
                tool_name=tool.name, spindle_dir=tool.spindle_dir)
    g = post_program([blk], tool_change=True, tool_change_prompt=False)
    check("D: T8 op posts T0008 M06", "T0008 M06" in g)
    check("D: T8 op posts G97 S2000 M04", "G97 S2000 M04" in g)
    check("D: T8 op posts F50.", "F50." in g)


if __name__ == "__main__":
    test_a1_home_toolchange()
    test_a2_tool_word()
    test_a3_feed_mode()
    test_a4_spindle_start()
    test_a5_spindle_dir()
    test_a6_program_number_cycle()
    test_a7_same_tool_dedup()
    test_a8_number_format()
    test_a9_start_line()
    test_b1_bore_entry()
    test_b2_bore_retract()
    test_b3_face_allowance()
    test_c1_reference_regression()
    test_d_user_library()
    print(f"\n{len(FAILED)} failed: {FAILED}" if FAILED else "\nALL TESTS PASSED")
    sys.exit(1 if FAILED else 0)
