"""Masso G3 lathe post-processor.

Masso lathe dialect. Conventions are taken from the user's PROVEN
BobCAD-CAM output (post "Masso_G3_Lathe", drum_screw_magnet_nfc.nc):

  O0100                     program number first line
  G40 G18 G21 G90           start line
  G53 G0 X0. / G53 G0 Z0.   machine-home moves before every tool change
  T0008 M06                 T + 4-digit zero-padded tool number
  G50 S<max rpm>            spindle RPM clamp before the spindle start
  G97 S<rpm> M03/M04        fixed spindle; M04 for upside-down tools
  G96 S<m/min> M03          constant surface speed (G50 is the clamp)
  G94 + F mm/min            default feed mode (proven file has no G95)
  G95 + F mm/rev            thread block only (G32 needs feed/rev)
  G32 Z.. F<pitch>          threading, spindle-synchronised, no X word
  G02/G03 X.. Z.. R.. F..   arc feed moves (true geometric radius)
  G42 / G40                 nose-radius compensation, opt-in (NoseComp):
                            G42 before the first cut, G40 before rapids
  M64 P1 / M65 P1           optional cycle output (chip conveyor etc.)
  M09, M65 P1, M05, G53 X0, G53 Z0, M30   program end (no final tool change)

X is DIAMETER programming (Masso lathe mode).
"""

from typing import List

from .paths import Motion


def _fmt(v: float) -> str:
    """Format a number like the proven file: whole numbers keep the
    trailing dot (X31. Z0. F100.), fractional values drop trailing zeros
    AND the leading zero (Z-.166 R.397 F.381), exactly as the proven
    Masso file prints them."""
    s = f"{v:.3f}".rstrip("0")
    if s in ("-0.", "0.", ""):
        return "0."
    if s.startswith("0."):
        return s[1:]
    if s.startswith("-0."):
        return "-" + s[2:]
    return s


class MassoPost:
    def __init__(self, css: bool = False, line_numbers: bool = False,
                 tool_change: bool = False, safe_start: str = "G40 G18 G21 G90",
                 coolant: bool = True, tool_change_prompt: bool = True,
                 feed_mode: str = "mm_per_min", program_number: int = 100,
                 cycle_output: bool = False, cycle_output_p: int = 1,
                 nose_comp: bool = False):
        self.css = css
        self.line_numbers = line_numbers
        self.tool_change = tool_change
        self.safe_start = safe_start
        self.coolant = coolant
        # False = fully automatic turret: emit T.. M06 with no M00 stop
        self.tool_change_prompt = tool_change_prompt
        # A3: "mm_per_min" (G94, proven on the machine) | "mm_per_rev" (G95)
        self.feed_mode = feed_mode or "mm_per_min"
        self.program_number = program_number
        self.cycle_output = cycle_output
        self.cycle_output_p = int(cycle_output_p or 1)
        # N1: opt-in control-side nose compensation (G42/G40). Default off:
        # the proven file is post-side compensated; G42 on Masso is VERIFY.
        self.nose_comp = nose_comp

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

        # A6: program number as first line (O0100)
        if self.program_number is not None:
            emit(f"O{int(self.program_number):04d}")
        if self.safe_start:
            emit(self.safe_start)

        # CSS always converts feed to mm/min (docs warn against G96 + G95)
        mm_min = (self.feed_mode == "mm_per_min") or self.css
        prev_tool = None
        spindle_on = False
        prev_rpm = None
        cycle_on = False
        comp_on = False

        for bi, b in enumerate(blocks):
            emit(f"({b.comment})")
            has_thread = any(mo.kind == "thread" for mo in b.moves)
            dir_word = "M04" if getattr(b, "spindle_dir", "cw") == "ccw" else "M03"

            # A1/A7: tool change at machine home; same tool as previous op
            # means NO stop, home, T word or M00 — spindle stays running.
            if (self.tool_change and b.tool_number
                    and b.tool_number != prev_tool):
                if spindle_on:
                    # R2: never change tools with spindle/coolant running
                    if self.coolant:
                        emit("M09")
                    emit("M05")
                emit("G53 G0 X0.")
                emit("G53 G0 Z0.")
                emit(f"T{int(b.tool_number):04d} M06")
                if self.tool_change_prompt:
                    tname = getattr(b, "tool_name", "") or ""
                    label = (f"LOAD T{int(b.tool_number):04d}"
                             + (f" {tname}" if tname else ""))
                    emit(f"M00 ({label})")  # full stop: operator confirms
                spindle_on = False
                prev_rpm = None
                prev_tool = b.tool_number

            # A4: G50 clamp before the spindle start; one-line G97/G96 + M03/M04
            if b.rpm and not spindle_on:
                emit(f"G50 S{int(b.rpm_ceiling)}")
                if self.css and not has_thread:
                    emit(f"G96 S{int(b.surface_speed or 150)} {dir_word}")
                else:
                    emit(f"G97 S{int(b.rpm)} {dir_word}")
                if self.coolant:
                    emit("M08")
                spindle_on = True
                prev_rpm = b.rpm
            elif b.rpm and spindle_on and (abs(b.rpm - prev_rpm) > 1e-9
                                           or (has_thread and self.css)):
                # A7: same tool, spindle already running: only a speed
                # change is emitted. K6: a thread block must force G97 even
                # when the rpm is unchanged (G32 needs fixed RPM + G95).
                emit(f"G97 S{int(b.rpm)}")
                prev_rpm = b.rpm

            # A3: feed mode per block; the thread block is always G95 so the
            # G32 F word is the pitch (mm/rev); following blocks re-emit G94.
            mode = "G95" if has_thread else ("G94" if mm_min else "G95")
            emit(mode)

            for mo in b.moves:
                if mo.kind == "dwell":
                    emit(f"G4 P{_fmt(mo.feed)}")
                    continue
                # N1: G42 before the first cut of the program, G40 before
                # any rapid (retract / tool change) once compensation is on.
                if self.nose_comp:
                    if mo.kind in ("feed", "cw", "ccw") and not comp_on:
                        emit("G42")
                        comp_on = True
                    elif mo.kind == "rapid" and comp_on:
                        emit("G40")
                        comp_on = False
                if mo.kind in ("cw", "ccw"):
                    # A3: arc feed move; R is the true geometric radius
                    g = "G02" if mo.kind == "cw" else "G03"
                    fa = mo.feed * (b.rpm or 800) if mode == "G94" else mo.feed
                    emit(f"{g} X{_fmt(mo.x)} Z{_fmt(mo.z)} "
                         f"R{_fmt(abs(mo.radius))} F{_fmt(fa)}")
                    continue
                g = {"rapid": "G0", "feed": "G1", "thread": "G32"}[mo.kind]
                if mo.kind == "thread":
                    # Masso G32 syntax: Z and F only (X pre-positioned)
                    emit(f"G32 Z{_fmt(mo.z)} F{_fmt(mo.feed)}")
                    continue
                words = [g, f"X{_fmt(mo.x)}", f"Z{_fmt(mo.z)}"]
                if mo.kind == "feed":
                    # mm/rev -> mm/min at the block's nominal RPM (G94)
                    f = mo.feed * (b.rpm or 800) if mode == "G94" else mo.feed
                    words.append(f"F{_fmt(f)}")
                emit(" ".join(words))
                if self.cycle_output and not cycle_on and mo.kind == "rapid":
                    emit(f"M64 P{self.cycle_output_p}")
                    cycle_on = True

        # A1: program end — M09, (M65), M05, home, M30. No tool change here:
        # the proven file's final T0001 M06 (turret index with spindle
        # running) is a known BobCAD flaw and is deliberately NOT copied.
        if comp_on:
            emit("G40")
        if self.coolant:
            emit("M09")
        if self.cycle_output:
            emit(f"M65 P{self.cycle_output_p}")
        emit("M05")
        emit("G53 G0 X0.")
        emit("G53 G0 Z0.")
        emit("M30")
        return "\n".join(out) + "\n"


class Block:
    """One posted operation: header info + motion list."""

    def __init__(self, comment: str, tool_number: int, rpm: float,
                 moves: List[Motion], rpm_ceiling: float = 3000.0,
                 surface_speed: float = 0.0, tool_name: str = "",
                 spindle_dir: str = "cw"):
        self.comment = comment
        self.tool_number = tool_number
        self.rpm = rpm
        self.rpm_ceiling = rpm_ceiling
        self.surface_speed = surface_speed
        self.tool_name = tool_name
        self.spindle_dir = spindle_dir
        self.moves = moves


def post_program(blocks: List[Block], header: str = "", **kw) -> str:
    body = MassoPost(**kw).post(blocks)
    lines = []
    if header:
        lines += [f"; {h}" for h in header.splitlines()]
    return "\n".join(lines) + ("\n" if lines else "") + body
