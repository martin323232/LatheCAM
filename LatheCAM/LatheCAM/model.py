"""Data model: Stock, Tool, operations. Pure dataclasses + validation.

All lengths in mm. X is DIAMETER everywhere in this module.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

OP_TYPES = ("face", "od_rough", "od_finish", "id_rough", "id_finish",
            "groove", "part", "thread")


@dataclass
class Stock:
    diameter: float = 30.0          # raw stock diameter
    length: float = 60.0            # length from face (Z0) into the waste
    bore_diameter: float = 0.0      # pre-drilled hole (0 = solid)
    max_rpm: float = 3000.0         # spindle limit
    safe_x_clearance: float = 4.0   # rapid X clearance beyond stock dia (dia mm)
    safe_z_clearance: float = 2.0   # rapid Z clearance in front of face
    # B3: uncut facing allowance in front of Z0 (material from Z=0 up to
    # Z=face_allowance exists and the collision model must see it).
    face_allowance: float = 0.0

    def safe_x(self) -> float:
        return self.diameter + self.safe_x_clearance

    def safe_z(self) -> float:
        return self.face_allowance + self.safe_z_clearance


@dataclass
class Tool:
    name: str = "OD-rough"
    tool_number: int = 1
    kind: str = "turn"              # turn | bore | groove | part | thread
    width: float = 3.0              # insert width (groove/part/thread)
    nose_radius: float = 0.4
    max_depth: float = 20.0         # max safe plunge depth (radial mm)
    # spindle/feed defaults for this tool (feed = mm/rev)
    rpm: float = 800.0
    feed: float = 0.15
    # B: dedicated feed (mm/rev) for finish ops when the op feed is 0
    # (0 = off, fall back to .feed). e.g. DCMT070204 -> 0.03.
    finish_feed: float = 0.0
    thread_pitch: float = 0.0       # for thread tools (mm)
    # where the machine tool offset is measured for groove/part tools:
    # left = chuck-side corner, right = face-side corner, centre = middle.
    # The op Z is the nominal cut CENTRE; the programmed Z is shifted so the
    # kerf stays centred there given this corner.
    z_reference: str = "left"
    # A5: spindle direction for this tool: "cw" -> M03, "ccw" -> M04
    # (e.g. a boring bar mounted upside down runs M04).
    spindle_dir: str = "cw"
    # free-text note shown in the Tool Library dialog (e.g. "VERIFY")
    note: str = ""

    @staticmethod
    def default_library() -> List["Tool"]:
        return [
            Tool("OD-rough", 1, "turn", rpm=800, feed=0.20, z_reference="centre"),
            Tool("OD-finish", 2, "turn", nose_radius=0.8, rpm=1200, feed=0.10,
                 z_reference="centre"),
            Tool("Bore", 3, "bore", width=4.0, max_depth=25.0, rpm=700, feed=0.12,
                 z_reference="centre"),
            Tool("Groove 3mm", 4, "groove", width=3.0, max_depth=5.0, rpm=500,
                 feed=0.06, z_reference="left"),
            Tool("Part 2mm", 5, "part", width=2.0, max_depth=20.0, rpm=400,
                 feed=0.05, z_reference="left"),
            Tool("Thread 60deg", 6, "thread", width=1.6, rpm=250, feed=0.0,
                 thread_pitch=2.0, z_reference="centre"),
        ]


@dataclass
class Op:
    """One turning operation. Fields used depend on .type."""
    type: str = "od_rough"
    tool: str = "OD-rough"
    comment: str = ""

    # geometry (Z negative into stock; X diameter)
    z_start: float = 0.0
    z_end: float = -30.0
    target_diameter: float = 20.0   # final diameter (od/id ops)
    face_diameter: float = 0.0      # face op: start diameter to face down from
    face_depth: float = 2.0         # face op: axial mm to cut off the end

    # cutting params
    depth_per_pass: float = 1.5     # radial mm per roughing pass
    stock_to_leave: float = 0.5     # radial mm left for finish pass
    feed: Optional[float] = None    # mm/rev (None = tool default)
    rpm: Optional[float] = None     # constant spindle rpm (None = tool default)

    # groove / part
    groove_diameter: float = 10.0   # groove/part bottom diameter
    peck: float = 1.0               # plunge peck depth (radial mm)

    # thread (external, straight)
    thread_major_diameter: float = 20.0
    thread_pitch: float = 2.0
    thread_length: float = 20.0
    thread_lead_in: Optional[float] = None  # None = 3x pitch
    thread_lead_out: Optional[float] = None  # None = 1x pitch
    thread_spring_pass: bool = True
    # deepest Z the thread may reach (shoulder / undercut position); None = only
    # the stock length limits it
    thread_z_limit: Optional[float] = None

    # boring: False (default) = cut toward the chuck (-Z, standard boring bar);
    # True = back-boring, cut toward the face (+Z)
    bore_back_cut: bool = False

    # od_finish profile: list of (diameter, z[, radius]) points start to end.
    # radius (A) applies to the segment ENDING at that point: >0 G02 CW,
    # <0 G03 CCW, 0/absent = straight. Empty = simple cylinder.
    profile: List[Tuple[float, ...]] = field(default_factory=list)
    # C: 45-degree auto-chamfer (mm) at the profile start/end corner, 0 = off
    chamfer_start: float = 0.0
    chamfer_end: float = 0.0
    # E: internal thread (cut inside an existing bore, toward the chuck)
    thread_inside: bool = False

    def validate(self, stock: Stock) -> None:
        if self.type not in OP_TYPES:
            raise ValueError(f"unknown op type {self.type!r}")
        if self.z_start <= self.z_end and self.type not in ("face", "part", "groove"):
            raise ValueError(f"{self.type}: z_start must be > z_end (cut away from face)")
        if self.type in ("od_rough", "od_finish"):
            if self.target_diameter <= 0:
                raise ValueError("target_diameter must be > 0")
            if self.target_diameter >= stock.diameter:
                raise ValueError("target_diameter must be < stock diameter")
        if self.type in ("id_rough", "id_finish"):
            if stock.bore_diameter <= 0:
                raise ValueError("stock has no bore (set bore_diameter)")
            if not self.profile and self.target_diameter <= stock.bore_diameter:
                raise ValueError("id target must be > bore_diameter")
        if self.type == "thread":
            if self.thread_pitch <= 0:
                raise ValueError("thread_pitch must be > 0")
            if self.thread_major_diameter <= 0:
                raise ValueError("thread_major_diameter must be > 0")
            if self.thread_inside:
                if stock.bore_diameter <= 0:
                    raise ValueError("internal thread: stock has no bore "
                                     "(set bore_diameter)")
                minor = self.thread_major_diameter - 2 * thread_depth(
                    self.thread_pitch)
                if minor < stock.bore_diameter - 1e-9:
                    raise ValueError(
                        f"internal thread: minor dia {minor:.2f} is below the "
                        f"existing bore {stock.bore_diameter:.2f} — the bore "
                        "must be drilled at or under the thread minor dia")
            lead_out = max(1.0 * self.thread_pitch, 1.0)
            z_end = self.z_start - self.thread_length - lead_out
            if z_end < -stock.length:
                raise ValueError(
                    f"thread: lead-out ends at Z{z_end:.1f}, beyond stock "
                    f"length {stock.length:.1f} — shorten thread_length or "
                    "move z_start toward the face")
            if (self.thread_z_limit is not None
                    and z_end < self.thread_z_limit - 1e-9):
                raise ValueError(
                    f"thread: lead-out ends at Z{z_end:.1f}, past the shoulder/"
                    f"undercut at Z{self.thread_z_limit:.1f} — shorten "
                    "thread_length or move z_start toward the face")


def thread_depth(pitch: float) -> float:
    """Radial depth of an ISO 60-degree metric thread (~0.6134 * P)."""
    return round(0.6134 * pitch, 3)


def thread_passes(pitch: float) -> List[float]:
    """Progressive radial infeed depths (mm) summing exactly to thread depth."""
    total = thread_depth(pitch)
    depths: List[float] = []
    remaining = total
    infeed = 0.30
    while remaining > 1e-6:
        d = min(infeed, remaining)
        depths.append(d)
        remaining -= d
        infeed = max(infeed * 0.72, 0.05)
    # round each pass but keep the sum exactly equal to total depth
    rounded = [round(d, 3) for d in depths[:-1]]
    rounded.append(round(total - sum(rounded), 3))
    return rounded
