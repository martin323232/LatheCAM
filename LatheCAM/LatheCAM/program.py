"""Program builder: FreeCAD doc -> blocks -> Masso G-code. Headless-safe."""

import os
from datetime import datetime
from typing import Dict, List

from .model import Tool
from .paths import generate, CollisionChecker
from .post import Block, post_program

_HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_LIBRARY_PATH = os.path.join(_HERE, "tool_library.json")


def user_tool_dir() -> str:
    """Per-user tool library directory (survives addon updates).

    Uses FreeCAD's user AppData dir when available (so it lands next to the
    user's config), else falls back to ~/.lathecam for headless use."""
    try:
        import FreeCAD
        base = FreeCAD.getUserAppDataDir()
    except Exception:
        base = os.path.join(os.path.expanduser("~"), ".lathecam")
    d = os.path.join(base, "LatheCAM_tools")
    return d


def user_tool_library_path() -> str:
    return os.path.join(user_tool_dir(), "tools.json")


def _read_tool_file(path):
    import json
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [Tool(**t) for t in json.load(f)]
    except (OSError, ValueError, TypeError):
        return []


def load_tools(path: str = None) -> Dict[str, Tool]:
    """Built-in library merged with the user library (user wins by name).

    An explicit ``path`` (from Setup.ToolLibraryFile) replaces the built-in
    base entirely; otherwise the built-in json is the base and the per-user
    tools.json is layered on top so custom tools and renumbered tools persist
    across addon updates. Raises ValueError if two tools share a T number.
    """
    if path:
        base = _read_tool_file(path) or Tool.default_library()
    else:
        base = _read_tool_file(TOOL_LIBRARY_PATH) or Tool.default_library()
    tools = {t.name: t for t in base}
    if not path:
        for t in _read_tool_file(user_tool_library_path()):
            tools[t.name] = t
    seen = {}
    for t in tools.values():
        if t.tool_number in seen:
            raise ValueError(
                f"duplicate tool number T{t.tool_number:02d}: "
                f"{seen[t.tool_number]!r} and {t.name!r}")
        seen[t.tool_number] = t.name
    return tools


def _stock_for(setup_obj, op_objs) -> "Stock":
    """Build the Stock model, including the B3 facing allowance: material
    exists from Z=0 up to the largest face-op FaceDepth (or the explicit
    Setup.FaceAllowance), and the collision model must see it."""
    stock = setup_obj.Proxy.to_stock()
    fa = float(getattr(setup_obj, "FaceAllowance", 0.0) or 0.0)
    for o in op_objs:
        op = o.Proxy.to_op()
        if op.type == "face":
            fa = max(fa, op.face_depth)
    stock.face_allowance = fa
    return stock


def build_blocks(setup_obj, op_objs, tools: Dict[str, Tool]) -> List[Block]:
    from .objects import SetupProxy, OpProxy  # local import: avoid cycles
    stock = _stock_for(setup_obj, op_objs)
    blocks = []
    for o in op_objs:
        op = o.Proxy.to_op()
        tool = tools.get(op.tool)
        if tool is None:
            raise ValueError(f"tool {op.tool!r} not in library "
                             f"(available: {', '.join(sorted(tools))})")
        moves = generate(op, tool, stock)
        rpm = op.rpm if op.rpm is not None else tool.rpm
        block = Block(f"{o.Label} [{op.type}]", tool.tool_number, rpm, moves,
                      rpm_ceiling=stock.max_rpm,
                      surface_speed=float(getattr(setup_obj, "SurfaceSpeed", 150.0)),
                      tool_name=tool.name,
                      spindle_dir=getattr(tool, "spindle_dir", "cw"))
        block.op_type = op.type
        block.tool_kind = tool.kind
        block.tool_width = tool.width
        block.z_reference = getattr(tool, "z_reference", "centre")
        block.nose_radius = getattr(tool, "nose_radius", 0.0)
        blocks.append(block)
    return blocks


def post_document(doc, filename: str = None) -> str:
    """Post the whole document. Returns gcode text; writes file if filename given.

    Raises ValueError if any rapid move passes through remaining material —
    fix the ops before posting.
    """
    from .objects import collect_program
    setup, ops = collect_program(doc)
    if setup is None:
        raise ValueError("no Lathe::Setup object in document")
    if not ops:
        raise ValueError("no Lathe::Op objects in document")
    tools = load_tools(str(getattr(setup, "ToolLibraryFile", "")).strip() or None)
    blocks = build_blocks(setup, ops, tools)
    stock = _stock_for(setup, ops)
    nose_comp = bool(getattr(setup, "NoseComp", False))
    checker = CollisionChecker(stock)
    for b in blocks:
        kind = getattr(b, "tool_kind", "turn")
        # kerf width only for groove/part tools; turning stays point-sampled
        tw = getattr(b, "tool_width", 0.0) if kind in ("groove", "part") else 0.0
        checker.add(b.moves, boring=getattr(b, "op_type", "").startswith("id_"),
                    tool_width=tw, z_reference=getattr(b, "z_reference", "centre"),
                    nose_radius=getattr(b, "nose_radius", 0.0) if nose_comp else 0.0)
    if checker.problems:
        raise ValueError("rapid collision check failed:\n  " + "\n  ".join(checker.problems))
    gcode = post_program(
        blocks,
        header=f"LatheCAM {os.path.basename(filename or 'program')} | posted "
               f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | stock "
               f"D{float(setup.StockDiameter):g} x {float(setup.StockLength):g} mm",
        css=bool(getattr(setup, "UseCSS", False)),
        line_numbers=bool(getattr(setup, "LineNumbers", False)),
        tool_change=bool(getattr(setup, "ToolChange", False)),
        tool_change_prompt=bool(getattr(setup, "ToolChangePrompt", True)),
        feed_mode=str(getattr(setup, "FeedMode", "mm_per_min") or "mm_per_min"),
        program_number=int(getattr(setup, "ProgramNumber", 100)),
        cycle_output=bool(getattr(setup, "CycleOutput", False)),
        cycle_output_p=int(getattr(setup, "CycleOutputP", 1) or 1),
        nose_comp=nose_comp,
    )
    if filename:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(gcode)
    return gcode
