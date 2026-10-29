"""ISO 1832 insert geometry (issue #14).

Pure stdlib (math/re/array): importable from plain python3 for tests and
from FreeCAD for the GUI and the sim. All lengths mm, angles degrees.

The insert polygon lives in the tool plane with the cutting nose at the
origin pointing toward -x (same convention as the approved mockup
mockups/tool-profile-editor.html):

  * OD turning: +x points away from the spindle axis (into the chip-free
    side of the cut), +y runs along the tool axis toward the face side.
  * Boring: the same polygon mirrored in x — the nose points toward the
    hole wall (+radial) and the bar body hangs below it at -x.

``outer_radius`` is the tool-intrinsic sweep extent: the largest distance
from the nose to any point of the insert envelope, plus the nose radius.
For a boring tool the smallest hole the bar+insert fits through is
``min_bore_d = bar_diameter + 2 * outer_radius``.
"""

import math
import re

# shape letter -> (n_sides, tip angle deg, circumradius/IC factor,
#                  default relief deg, name). n_sides 0 = full round.
# Values follow the approved mockup; the shape letters are the ISO 1832
# set named in the issue (C D V A W T S B R K E X).
SHAPES = {
    "C": (4, 80.0, 2.00, 7.0, "Rhombic 80\u00b0"),
    "D": (4, 55.0, 2.05, 7.0, "Rhombic 55\u00b0"),
    "V": (4, 35.0, 1.72, 7.0, "Rhombic 35\u00b0"),
    "A": (4, 75.0, 1.90, 7.0, "Rhombic 75\u00b0"),
    "W": (3, 60.0, 1.15, 7.0, "Trigon 60\u00b0"),
    "T": (3, 60.0, 1.16, 0.0, "Trigon (bore)"),
    "S": (4, 90.0, 1.41, 7.0, "Square"),
    "B": (4, 90.0, 1.41, 0.0, "Rectangle"),
    "R": (0, 360.0, 1.00, 0.0, "Round"),
    "K": (4, 55.0, 2.00, 7.0, "Parallelogram"),
    "E": (6, 80.0, 1.15, 11.0, "Hexagonal"),
    "X": (8, 80.0, 1.06, 11.0, "Octagonal"),
}

# inscribed-circle code -> mm (mockup table; covers the user's library)
IC_MM = {"04": 4.76, "06": 6.35, "07": 7.94, "08": 8.53, "09": 9.53,
         "11": 11.11, "12": 12.70, "16": 15.88, "19": 19.05}

# thickness code -> mm; numeric codes plus the common letter codes
THK_MM = {"02": 2.38, "03": 3.18, "04": 4.76, "05": 5.56, "06": 6.35,
          "09": 9.53, "T2": 2.38, "T3": 3.18, "E3": 3.18, "U3": 3.18}

# nose-radius code -> mm
RE_MM = {"00": 0.0, "01": 0.1, "02": 0.2, "04": 0.4, "06": 0.6, "08": 0.8,
         "09": 0.9, "12": 1.2, "16": 1.6, "20": 2.0, "25": 2.5}

# relief (clearance) letter -> deg, ISO 1832 / ANSI B212.4. Unknown or
# None -> fall back to the shape default.
RELIEF = {"N": 0.0, "A": 3.0, "B": 5.0, "C": 7.0, "D": 15.0, "E": 20.0,
          "F": 25.0, "G": 30.0, "H": 17.5, "P": 11.0, "R": 27.5}

# tolerance letter (3rd char) — informational only
TOLERANCE = {"J": 0.013, "K": 0.025, "M": 0.013, "N": 0.025, "U": 0.025,
             "G": 0.05, "H": 0.025}

_CODE_RE = re.compile(r"^([A-Z])([A-Z])([A-Z0-9]{2})([A-Z0-9]{2})([A-Z0-9]{2})")


def _invert(table):
    """mm -> code, nearest code wins (codes are the canonical spelling)."""
    out = {}
    for code, mm in table.items():
        key = round(mm, 2)
        if key not in out:
            out[key] = code
    return out


_IC_CODE = _invert(IC_MM)
_THK_CODE = _invert(THK_MM)
_RE_CODE = _invert(RE_MM)


def parse_iso(code):
    """Parse an ISO 1832 insert code (e.g. CCMT060208) into a dict.

    Returns None when the code is not recognised (unknown shape letter or
    unknown IC/thickness/nose-radius code). The dict carries the raw
    ``code`` so callers can round-trip the exact spelling.
    """
    if not code:
        return None
    v = re.sub(r"[^A-Z0-9]", "", str(code).upper())
    m = _CODE_RE.match(v)
    if not m:
        return None
    shape, relief_l, ic_c, thk_c, re_c = m.groups()
    if shape not in SHAPES:
        return None
    if ic_c not in IC_MM or thk_c not in THK_MM or re_c not in RE_MM:
        return None
    n, tip, f, rel_default, name = SHAPES[shape]
    relief = RELIEF.get(relief_l, rel_default)
    return {
        "code": v[:8],
        "insert_shape": shape,
        "shape_name": name,
        "tip_angle": tip,
        "circ_factor": f,
        "relief_letter": relief_l,
        "relief_angle": relief,
        "tolerance": TOLERANCE.get(m.group(2) if False else relief_l, None),
        "insert_ic": IC_MM[ic_c],
        "insert_thickness": THK_MM[thk_c],
        "nose_radius": RE_MM[re_c],
    }


def iso_code(shape, ic, thickness, nose_radius):
    """Rebuild the 8-char code from geometry (round-trips parse_iso).

    Codes are the canonical spelling of the tables, so
    ``iso_code(**parse_iso(c)) == c`` for every code the parser accepts.
    """
    ic_c = _IC_CODE.get(round(ic, 2))
    thk_c = _THK_CODE.get(round(thickness, 2))
    re_c = _RE_CODE.get(round(nose_radius, 2))
    if shape not in SHAPES or ic_c is None or thk_c is None or re_c is None:
        return None
    return f"{shape}M{ic_c}{thk_c}{re_c}"


def insert_polygon(shape, ic, lead_angle=0.0, n_round=24):
    """Insert outline with the nose at the origin (see module docstring).

    Regular n-gon (or circle for R) of circumradius ic/2 * factor, one
    vertex at -x (the nose), then rotated about the nose by lead_angle
    (degrees, CCW in the tool plane). Returns [(x, y), ...].
    """
    if shape not in SHAPES:
        raise ValueError(f"unknown insert shape {shape!r}")
    n, _tip, f, _rel, _name = SHAPES[shape]
    r = ic / 2.0 * f
    pts = []
    if n == 0:
        # full round: the nose is the -x point of the circle
        for i in range(n_round):
            a = math.pi + 2.0 * math.pi * i / n_round
            pts.append((r * math.cos(a), r * math.sin(a)))
    else:
        for i in range(n):
            a = math.pi + i * 2.0 * math.pi / n
            pts.append((r * math.cos(a), r * math.sin(a)))
    # nose = leftmost point -> translate it to the origin
    nose = min(pts, key=lambda p: p[0])
    pts = [(p[0] - nose[0], p[1] - nose[1]) for p in pts]
    if abs(lead_angle) > 1e-12:
        ca, sa = math.cos(math.radians(lead_angle)), \
            math.sin(math.radians(lead_angle))
        pts = [(x * ca - y * sa, x * sa + y * ca) for x, y in pts]
    return pts


def outer_radius(shape, ic, nose_radius, lead_angle=0.0):
    """Max distance from the nose to the insert envelope, + nose radius.

    This is the tool-intrinsic "ytterradius": how far the insert body
    sweeps beyond the cutting point (issue #14).
    """
    pts = insert_polygon(shape, ic, lead_angle)
    m = max(math.hypot(x, y) for x, y in pts)
    return round(m + max(nose_radius, 0.0), 3)


def approach_angle(shape, lead_angle=0.0):
    """Approach angle kappa_r (deg): tip angle + side cutting edge angle
    (a round insert approaches at 95 deg, per the mockup)."""
    if shape not in SHAPES:
        return 0.0
    tip = SHAPES[shape][1]
    if tip >= 360.0:
        return 95.0
    return round(tip + lead_angle, 1)


def min_bore_d(bar_diameter, outer_radius_mm):
    """Smallest bore diameter a bar+insert envelope fits through."""
    return round(bar_diameter + 2.0 * outer_radius_mm, 2)


def bore_fit_warning(tool, bore_diameter, tol=1e-9):
    """Warning text when a boring tool cannot fit the current bore.

    Returns None when the tool has no boring geometry, the bore is
    unknown/solid, or the envelope fits.
    """
    kind = getattr(tool, "kind", "")
    minb = float(getattr(tool, "min_bore_d", 0.0) or 0.0)
    if kind != "bore" or minb <= 0.0:
        return None
    bd = float(bore_diameter or 0.0)
    if bd <= 0.0:
        return None
    if minb <= bd + tol:
        return None
    return (f"tool {getattr(tool, 'name', '?')!r} needs a bore of at least "
            f"\u00d8{minb:.1f} mm (bar \u00d8{getattr(tool, 'bar_diameter', 0):g}"
            f" + insert envelope), but the stock bore is \u00d8{bd:g} mm")
