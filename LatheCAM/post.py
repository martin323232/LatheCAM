"""Masso G3 lathe post-processor.

Masso lathe dialect (verified against docs.masso.com.au, Oct 2026):
  G96 D<maxrpm> S<m/min>  constant surface speed (D = RPM ceiling)
  G97                     fixed spindle RPM
  G95                     feed per REVOLUTION (Masso uses G95, not G99!)
  G32 Z.. F<pitch>        threading, spindle-synchronised, no taper
  NOTE: docs warn against combining G96 CSS with G95 feed/rev.

Default mode: G97 (fixed RPM) + G95 (mm/rev) — deterministic, safe for
threading. Optional CSS mode converts feed to mm/min from nominal RPM.
X is DIAMETER programming (Masso lathe mode).
"""

from typing import List

from .paths import Motion


def _fmt(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class MassoPost:
    def __init__(self, css: bool = False, line_numbers: bool = False,
                 tool_change: bool = False, safe_start: str = "G21 G90 G18 G40",
                 coolant: bool = True, tool_change_prompt: bool = True):
        self.css = css
        self.line_numbers = line_numbers
        self.tool_change = tool_change
        self.safe_start = safe_start
        self.coolant = coolant
        # False = fully automatic turret: emit T.. M06 with no M00 stop
        self.tool_change_prompt = tool_change_prompt

    def post(self, blocks: List["Block"]) -> str:
        out: List[str] = []
        n = [0]

        def emit(line: str):
            if not line:
                return
            if self.line_numbers:
                n[0] += 10
                out.append(f"N{n[0]} {line}")
            else:
                out.append(line)

        if self.safe_start:
            emit(self.safe_start)

        last = len(blocks) - 1
        first_tc = True
        for bi, b in enumerate(blocks):
            emit(f"({b.comment})")
            has_thread = any(mo.kind == "thread" for mo in b.moves)
            if self.tool_change and b.tool_number:
                if not first_tc:
                    # R2: never change tools with spindle/coolant running
                    if self.coolant:
                        emit("M09")
                    emit("M05")
                emit(f"T{b.tool_number:02d} M06")
                if self.tool_change_prompt:
                    tname = getattr(b, "tool_name", "") or ""
                    label = f"LOAD T{b.tool_number:02d}" + (f" {tname}" if tname else "")
                    emit(f"M00 ({label})")  # full stop: operator confirms the tool
                first_tc = False
            if b.rpm:
                # K6: G32 threading must run G97 + G95 (feed in mm/rev,
                # fixed rpm) regardless of the CSS setting, or the pitch
                # would be read as mm/min under G94.
                if self.css and not has_thread:
                    emit(f"G96 D{int(b.rpm_ceiling)} S{int(b.surface_speed or 150)}")
                else:
                    emit(f"G97 S{int(b.rpm)}")
                emit("M03")
                if self.coolant:
                    emit("M08")
            mode = "G95" if (not self.css or has_thread) else "G94"
            emit(mode)
            for mo in b.moves:
                if mo.kind == "dwell":
                    emit(f"G4 P{_fmt(mo.feed)}")
                    continue
                g = {"rapid": "G0", "feed": "G1", "thread": "G32"}[mo.kind]
                if mo.kind == "thread":
                    # Masso G32 syntax: Z and F only (X pre-positioned)
                    emit(f"G32 Z{_fmt(mo.z)} F{_fmt(mo.feed)}")
                    continue
                words = [g, f"X{_fmt(mo.x)}", f"Z{_fmt(mo.z)}"]
                if mo.kind == "feed":
                    f = mo.feed
                    if self.css and mode == "G94":
                        f = mo.feed * (b.rpm or 800)  # mm/rev -> mm/min approx
                    words.append(f"F{_fmt(f)}")
                emit(" ".join(words))
            if bi == last:
                if self.coolant:
                    emit("M09")
                emit("M05")
        emit("M30")
        return "\n".join(out) + "\n"


class Block:
    """One posted operation: header info + motion list."""

    def __init__(self, comment: str, tool_number: int, rpm: float,
                 moves: List[Motion], rpm_ceiling: float = 3000.0,
                 surface_speed: float = 0.0, tool_name: str = ""):
        self.comment = comment
        self.tool_number = tool_number
        self.rpm = rpm
        self.rpm_ceiling = rpm_ceiling
        self.surface_speed = surface_speed
        self.tool_name = tool_name
        self.moves = moves


def post_program(blocks: List[Block], header: str = "", **kw) -> str:
    body = MassoPost(**kw).post(blocks)
    lines = []
    if header:
        lines += [f"; {h}" for h in header.splitlines()]
    return "\n".join(lines) + ("\n" if lines else "") + body
