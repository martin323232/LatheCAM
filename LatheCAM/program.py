"""Program builder: FreeCAD doc -> blocks -> Masso G-code. Headless-safe."""

import os
from typing import Dict, List

from .model import Tool
from .paths import generate, CollisionChecker
from .post import Block, post_program

_HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_LIBRARY_PATH = os.path.join(_HERE, "tool_library.json")


def load_tools(path: str = None) -> Dict[str, Tool]:
    import json
    path = path or TOOL_LIBRARY_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        tools = [Tool(**t) for t in data]
    except (OSError, ValueError):
        tools = Tool.default_library()
    return {t.name: t for t in tools}


def build_blocks(setup_obj, op_objs, tools: Dict[str, Tool]) -> List[Block]:
    from .objects import SetupProxy, OpProxy  # local import: avoid cycles
    stock = setup_obj.Proxy.to_stock()
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
                      surface_speed=float(getattr(setup_obj, "SurfaceSpeed", 150.0)))
        block.op_type = op.type
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
    tools = load_tools()
    blocks = build_blocks(setup, ops, tools)
    stock = setup.Proxy.to_stock()
    checker = CollisionChecker(stock)
    for b in blocks:
        checker.add(b.moves, boring=getattr(b, "op_type", "").startswith("id_"))
    if checker.problems:
        raise ValueError("rapid collision check failed:\n  " + "\n  ".join(checker.problems))
    gcode = post_program(
        blocks,
        header=f"LatheCAM {os.path.basename(filename or 'program')} | stock "
               f"D{float(setup.StockDiameter):g} x {float(setup.StockLength):g} mm",
        css=bool(getattr(setup, "UseCSS", False)),
        line_numbers=bool(getattr(setup, "LineNumbers", False)),
        tool_change=bool(getattr(setup, "ToolChange", False)),
    )
    if filename:
        with open(filename, "w", encoding="utf-8") as f:
            f.write(gcode)
    return gcode
