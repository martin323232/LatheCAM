"""Plain-python tests for the continuous removal sim (issue #13).

Run: python3 tests/test_removal.py
"""

import math
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.join(_here, "..", "LatheCAM"), os.path.dirname(_here)):
    # dev layout: repo/LatheCAM/LatheCAM/pkg | release layout: repo/LatheCAM/pkg
    if os.path.isfile(os.path.join(_cand, "LatheCAM", "__init__.py")):
        sys.path.insert(0, _cand)
        break

from LatheCAM.model import Stock, Tool
from LatheCAM.paths import Motion
from LatheCAM.removal import Envelope, RemovalSim, insert_band

FAILED = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILED.append(name)


def tools():
    return {t.name: t for t in Tool.default_library()}


def env_at_z(env, z):
    i = env._clamp(env.i_of(z))
    return env.outer[env.i_of(z)] if 0 <= env.i_of(z) < env.n else None


# ---------------------------------------------------------------- basics

def test_initial_state():
    stock = Stock(diameter=30.0, length=60.0)
    sim = RemovalSim(stock, [])
    env = sim.state_at(-1)
    check("initial: full stock radius",
          all(abs(r - 15.0) < 1e-9 for r in env.outer))
    check("initial: volume = stock",
          abs(env.volume() - math.pi * 15.0 ** 2 * 60.0) < 1.0,
          f"{env.volume():.1f}")
    # env0 must survive a cutting program untouched (it is the t=0 baseline)
    ops2 = [("r", False, tools()["OD-rough"], [
        Motion("rapid", 38.0, 2.0),
        Motion("feed", 20.0, -30.0, 0.2),
        Motion("feed", 20.0, -2.0, 0.2),
    ])]
    sim2 = RemovalSim(stock, ops2)
    check("env0 untouched by cuts",
          all(abs(r - 15.0) < 1e-9 for r in sim2.env0.outer))


def test_rapids_never_cut():
    stock = Stock(diameter=20.0, length=40.0)
    tl = tools()
    ops = [("g", False, tl["Groove 3mm"], [
        Motion("rapid", 26.0, 2.0),
        Motion("rapid", 26.0, -10.0),
        Motion("rapid", 12.0, -10.0),     # plunge as RAPID: must not cut
        Motion("rapid", 26.0, -10.0),
    ])]
    sim = RemovalSim(stock, ops)
    env = sim.state_at(len(sim.moves) - 1)
    check("rapids never cut", all(abs(r - 10.0) < 1e-9 for r in env.outer),
          f"min outer {min(env.outer)}")


# ---------------------------------------------------------------- groove

def test_groove_floor_width():
    """The slit-bug regression: a groove floor must be insert-wide."""
    stock = Stock(diameter=20.0, length=40.0)
    tl = tools()
    ops = [("g", False, tl["Groove 3mm"], [   # width 3, z_reference left
        Motion("rapid", 26.0, 2.0),
        Motion("rapid", 26.0, -10.0),
        Motion("feed", 12.0, -10.0, 0.06),    # plunge to Ø12
        Motion("rapid", 26.0, -10.0),
    ])]
    sim = RemovalSim(stock, ops)
    env = sim.state_at(len(sim.moves) - 1)
    cut = [env.z_of(i) for i in range(env.n) if env.outer[i] < 9.9]
    width = (max(cut) - min(cut) + env.dz) if cut else 0.0
    check("groove floor width == insert width",
          abs(width - 3.0) <= 2 * env.dz, f"width {width:.2f} mm")
    check("groove floor at cut radius",
          cut and all(abs(env.outer[env.i_of(z)] - 6.0) < 1e-9 for z in cut))
    check("groove left-referenced band",
          cut and abs(min(cut) - (-10.0)) <= env.dz
          and abs(max(cut) - (-7.0)) <= env.dz,
          f"span {min(cut):.2f}..{max(cut):.2f}" if cut else "no cut")


def test_groove_axial_widening():
    """Axial feed with a band tool extends the cut by the width."""
    stock = Stock(diameter=20.0, length=40.0)
    tl = tools()
    ops = [("g", False, tl["Groove 3mm"], [
        Motion("rapid", 26.0, 2.0),
        Motion("rapid", 26.0, -10.0),
        Motion("feed", 12.0, -10.0, 0.06),
        Motion("feed", 12.0, -20.0, 0.06),    # axial along floor
        Motion("rapid", 26.0, -20.0),
    ])]
    sim = RemovalSim(stock, ops)
    env = sim.state_at(len(sim.moves) - 1)
    cut = [env.z_of(i) for i in range(env.n) if env.outer[i] < 9.9]
    check("groove axial span includes insert width",
          cut and abs(max(cut) - (-7.0)) <= env.dz
          and abs(min(cut) - (-20.0)) <= env.dz,
          f"span {min(cut):.2f}..{max(cut):.2f}" if cut else "no cut")


# ---------------------------------------------------------------- facing

def test_facing_removes_slab():
    stock = Stock(diameter=20.0, length=40.0, face_allowance=2.0)
    tl = tools()
    ops = [("f", False, tl["OD-rough"], [
        Motion("rapid", 26.0, 4.0),
        Motion("feed", 22.0, 2.0, 0.2),
        Motion("feed", 0.0, 2.0, 0.2),      # face sweep at Z=+2
    ])]
    sim = RemovalSim(stock, ops)
    env = sim.state_at(len(sim.moves) - 1)
    front = [env.outer[i] for i in range(env.n) if env.z_of(i) >= 2.0 - 1e-9]
    below = [env.outer[i] for i in range(env.n) if env.z_of(i) < 2.0 - 1e-9]
    check("facing removes the whole slab in front",
          front and all(r <= 1e-9 for r in front))
    check("facing leaves material behind the plane",
          all(abs(r - 10.0) < 1e-9 for r in below))


# ---------------------------------------------------------------- boring

def test_bore_grows():
    stock = Stock(diameter=20.0, length=40.0, bore_diameter=4.2)
    tl = tools()
    ops = [("b", True, tl["Bore"], [
        Motion("rapid", 2.0, 2.0),
        Motion("rapid", 2.0, -10.0),
        Motion("feed", 8.4, -10.0, 0.12),    # radial infeed to Ø8.4
        Motion("feed", 8.4, -20.0, 0.12),    # axial toward chuck
    ])]
    sim = RemovalSim(stock, ops)
    env = sim.state_at(len(sim.moves) - 1)
    check("bore grows at cut",
          abs(env.bore[env.i_of(-15.0)] - 4.2) < 1e-9)
    check("bore untouched in front of cut",
          abs(env.bore[env.i_of(-5.0)] - 2.1) < 1e-9)
    check("outer untouched by boring",
          all(abs(r - 10.0) < 1e-9 for r in env.outer))


# ---------------------------------------------------------------- OD rough

def test_od_rough_partial_and_snapshots():
    stock = Stock(diameter=30.0, length=60.0)
    tl = tools()
    ops = [("r", False, tl["OD-rough"], [
        Motion("rapid", 38.0, 2.0),
        Motion("rapid", 38.0, -30.0),
        Motion("feed", 26.0, -30.0, 0.2),    # plunge
        Motion("feed", 26.0, -2.0, 0.2),     # axial pass
        Motion("rapid", 38.0, -2.0),
    ])]
    sim = RemovalSim(stock, ops)
    k = 1                                        # the axial pass
    full = sim.state_at(k)
    half = sim.state_at(k, 0.5)
    v0 = sim.volume_after(k - 1)
    v1 = sim.volume_after(k)
    vh = half.volume()
    check("partial move cuts between endpoints", v1 < vh < v0,
          f"{v1:.0f} < {vh:.0f} < {v0:.0f}")
    cut_full = [full.z_of(i) for i in range(full.n) if full.outer[i] < 14.9]
    cut_half = [half.z_of(i) for i in range(half.n) if half.outer[i] < 14.9]
    check("partial cuts less than the full span",
          0 < len(cut_half) < 0.75 * len(cut_full),
          f"{len(cut_half)} vs {len(cut_full)}")
    check("axial pass reaches plunge to near face",
          abs(min(cut_full) - (-30.0)) <= env_tol() and
          abs(max(cut_full) - (-2.0)) <= env_tol())
    ok = all(abs(sim.state_at(j).volume() - sim.volume_after(j)) < 1e-6
             for j in range(len(sim.moves)))
    check("snapshots consistent with replay", ok)


def env_tol():
    return 0.051


# ---------------------------------------------------------------- arcs

def test_arc_motion_cuts():
    """cw/ccw profile arcs must be sampled, not skipped (current sim.py
    envelope ignores them)."""
    stock = Stock(diameter=30.0, length=60.0)
    tl = tools()
    ops = [("a", False, tl["OD-finish"], [
        Motion("rapid", 38.0, 2.0),
        Motion("rapid", 24.0, -10.0),
        Motion("cw", 20.0, -20.0, 0.1, radius=8.0),   # arc segment
        Motion("rapid", 38.0, -20.0),
    ])]
    sim = RemovalSim(stock, ops)
    env = sim.state_at(len(sim.moves) - 1)
    mid = env.outer[env.i_of(-15.0)]
    check("arc cuts material along its span", mid < 15.0 - 1e-6,
          f"mid radius {mid}")
    check("arc sampled into several moves", len(sim.moves) > 3,
          f"{len(sim.moves)} moves")


# ---------------------------------------------------------------- thread

def test_thread_band_extension():
    stock = Stock(diameter=18.0, length=40.0)
    tl = tools()
    ops = [("t", False, tl["Thread 60deg"], [   # width 1.6, centre
        Motion("rapid", 26.0, 6.0),
        Motion("rapid", 26.0, 2.0),
        Motion("feed", 16.0, 2.0, 2.0),
        Motion("thread", 16.0, -10.0, 2.0),
        Motion("rapid", 26.0, -10.0),
    ])]
    sim = RemovalSim(stock, ops)
    env = sim.state_at(len(sim.moves) - 1)
    cut = [env.z_of(i) for i in range(env.n) if env.outer[i] < 8.9]
    check("thread pass extends by half width at the far end",
          cut and abs(min(cut) - (-10.8)) <= env_tol(),
          f"span {min(cut):.2f}..{max(cut):.2f}" if cut else "no cut")
    check("thread lead-in above the face has no material to cut",
          cut and abs(max(cut) - 0.0) <= env_tol(),
          f"span {min(cut):.2f}..{max(cut):.2f}" if cut else "no cut")


# ---------------------------------------------------------------- mapping

def test_cut_index_mapping():
    stock = Stock(diameter=20.0, length=40.0)
    tl = tools()
    ops = [("g", False, tl["Groove 3mm"], [
        Motion("rapid", 26.0, 2.0),        # 0: no cut
        Motion("feed", 12.0, -10.0, 0.06), # 1: cut 0
        Motion("rapid", 26.0, -10.0),      # 2: no cut, prefix still 0
    ])]
    sim = RemovalSim(stock, ops)
    check("cut_index before first cut", sim.cut_index(0, 0) == -1)
    check("cut_index at cut", sim.cut_index(0, 1) == 0)
    check("cut_index after cut keeps last", sim.cut_index(0, 2) == 0)
    check("insert_band centre", insert_band(tl["OD-rough"]) is None)
    check("insert_band groove left", insert_band(tl["Groove 3mm"]) == (0.0, 3.0))


if __name__ == "__main__":
    test_initial_state()
    test_rapids_never_cut()
    test_groove_floor_width()
    test_groove_axial_widening()
    test_facing_removes_slab()
    test_bore_grows()
    test_od_rough_partial_and_snapshots()
    test_arc_motion_cuts()
    test_thread_band_extension()
    test_cut_index_mapping()
    print()
    if FAILED:
        print(f"REMOVAL TESTS FAILED: {len(FAILED)} — {FAILED}")
        sys.exit(1)
    print("ALL REMOVAL TESTS PASSED")
