"""Build a ProfileString from selected faces/edges of a revolved solid.

Headless-safe: Part only, no GUI. The GUI command collects the selection
and calls segments()/profile_from_segments(); everything here is testable
under freecadcmd.

Lathe space: spindle axis = global Z, D = 2 * radial distance from Z.
Arc sign convention (matches paths.py/post.py and the proven reference):
in (rho, z) space, with C the arc centre, cross = (p_from - C) x (p_mid - C);
cross < 0 -> CW -> R > 0 (G02), cross > 0 -> CCW -> R < 0 (G03).
"""

import math
import re

import Part

TOL = 1e-4  # chaining tolerance, mm


class Seg:
    """One profile segment between two (D, z) points.

    radius/center/mid are set for arcs only: radius is the true geometric
    radius (mm), center and mid are (rho, z) — centre and a point ON the arc
    used to resolve the CW/CCW sign at output time."""

    __slots__ = ("p1", "p2", "radius", "center", "mid")

    def __init__(self, p1, p2, radius=None, center=None, mid=None):
        self.p1 = p1
        self.p2 = p2
        self.radius = radius
        self.center = center
        self.mid = mid

    def signed_radius(self, p_from, p_to):
        """R for the ProfileString when travelling p_from -> p_to."""
        cx, cz = self.center
        ax, az = p_from[0] / 2.0 - cx, p_from[1] - cz
        mx, mz = self.mid[0] - cx, self.mid[1] - cz
        cross = ax * mz - az * mx
        return self.radius if cross < 0 else -self.radius


def _close(a, b, tol=TOL):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _dz(p):
    return (2.0 * math.hypot(p.x, p.y), p.z)


def _rhoz(p):
    return (math.hypot(p.x, p.y), p.z)


def _on_axis(xy, tol=1e-6):
    return abs(xy[0]) < tol and abs(xy[1]) < tol


def _rim_circles(face):
    """(rho, z) of every circular edge of a face of revolution."""
    out = []
    for e in face.Edges:
        c = e.Curve
        if isinstance(c, Part.Circle) and _on_axis((c.Center.x, c.Center.y)):
            out.append((c.Radius, c.Center.z))
    return out


def _arc_mid(edge):
    pts = edge.discretize(5)
    p = pts[len(pts) // 2]
    return _rhoz(p)


def segments_from_face(face):
    s = face.Surface
    if isinstance(s, Part.Cylinder):
        if not _on_axis((s.Center.x, s.Center.y)) or abs(abs(s.Axis.z) - 1) > 1e-6:
            raise ValueError("cylinder face is not a revolution around Z")
        cs = _rim_circles(face)
        if len(cs) >= 2:
            (_, z1), (_, z2) = cs[0], cs[1]
        else:
            z1, z2 = face.BoundBox.ZMin, face.BoundBox.ZMax
        if abs(z2 - z1) < 1e-9:
            return []
        d = 2.0 * s.Radius
        return [Seg((d, z1), (d, z2))]
    if isinstance(s, Part.Cone):
        cs = _rim_circles(face)
        if len(cs) < 2:
            raise ValueError("cone face: could not find the two rim circles")
        (r1, z1), (r2, z2) = cs[0], cs[1]
        if abs(z1 - z2) < 1e-9:
            return []
        return [Seg((2 * r1, z1), (2 * r2, z2))]
    if isinstance(s, Part.Toroid):
        cs = _rim_circles(face)
        if len(cs) < 2:
            raise ValueError("torus face: could not find the two rim circles")
        (r1, z1), (r2, z2) = cs[0], cs[1]
        c = (s.MajorRadius, s.Center.z)
        rt = s.MinorRadius
        for r, z in ((r1, z1), (r2, z2)):
            if abs(math.hypot(r - c[0], z - c[1]) - rt) > 1e-4:
                raise ValueError("torus rims do not lie on the section circle")
        mid = None
        for e in face.Edges:
            # FreeCAD 1.1 reports an arc edge's Curve as the underlying
            # Part.Circle; the seam arc is the open, off-axis circle edge.
            if (isinstance(e.Curve, (Part.Circle, Part.ArcOfCircle))
                    and not e.isClosed()
                    and not _on_axis((e.Curve.Center.x, e.Curve.Center.y))):
                mid = _arc_mid(e)
                break
        if mid is None:
            raise ValueError("torus face has no seam arc to orient the fillet")
        return [Seg((2 * r1, z1), (2 * r2, z2), radius=rt, center=c, mid=mid)]
    if isinstance(s, Part.Plane):
        if abs(abs(s.Axis.z) - 1) > 1e-6:
            raise ValueError("plane face is not perpendicular to the spindle axis")
        cs = _rim_circles(face)
        if len(cs) < 2:
            raise ValueError("plane face: expected two circular rims")
        (r1, z1), (r2, z2) = cs[0], cs[1]
        if abs(r1 - r2) < 1e-9:
            return []
        return [Seg((2 * r1, z1), (2 * r2, z2))]
    raise ValueError(f"unsupported face surface: {type(s).__name__}")


def segments_from_edge(edge):
    c = edge.Curve
    if isinstance(c, Part.ArcOfCircle):
        a = _dz(edge.Vertexes[0].Point)
        b = _dz(edge.Vertexes[-1].Point)
        return [Seg(a, b, radius=c.Radius, center=_rhoz(c.Center),
                    mid=_arc_mid(edge))]
    if isinstance(c, Part.Line):
        a = _dz(edge.Vertexes[0].Point)
        b = _dz(edge.Vertexes[-1].Point)
        return [] if _close(a, b) else [Seg(a, b)]
    if isinstance(c, Part.Circle):
        if _on_axis((c.Center.x, c.Center.y)):
            return []  # rim circle: a corner point, not a segment
        if abs(c.Axis.z) > 1 - 1e-6:
            return []
        a = _dz(edge.Vertexes[0].Point)
        b = _dz(edge.Vertexes[-1].Point)
        return [Seg(a, b, radius=c.Radius, center=_rhoz(c.Center),
                    mid=_arc_mid(edge))]
    # BSpline and friends: polyline approximation
    pts = [_dz(p) for p in edge.discretize(24)]
    return [Seg(a, b) for a, b in zip(pts, pts[1:]) if not _close(a, b)]


def segments(shape, subnames):
    """Segments for 'Face3'/'Edge5' style subelement names of one shape."""
    segs = []
    for name in subnames:
        m = re.match(r"(Face|Edge)(\d+)$", name)
        if not m:
            raise ValueError(f"unsupported element '{name}'")
        i = int(m.group(2)) - 1
        if m.group(1) == "Face":
            segs.extend(segments_from_face(shape.Faces[i]))
        else:
            segs.extend(segments_from_edge(shape.Edges[i]))
    return segs


def chain(segs, allow_open=False):
    """Order segments into a path. Returns [(point, R), ...] where R applies
    to the segment ENDING at that point (ProfileString convention)."""
    if not segs:
        raise ValueError("selection gives no profile segments")
    ends = []
    for s in segs:
        for p in (s.p1, s.p2):
            deg = sum(1 for t in segs if t is not s
                      and (_close(t.p1, p) or _close(t.p2, p)))
            if deg == 0:
                ends.append(p)
    if not ends:  # closed loop: start at the face-side outer corner
        pts = [p for s in segs for p in (s.p1, s.p2)]
        ends = [max(pts, key=lambda p: (round(p[1], 6), p[0]))]
    cur = max(ends, key=lambda p: (round(p[1], 6), p[0]))
    path = [(cur, None)]
    used = [False] * len(segs)
    for _ in range(len(segs)):
        cands = []
        for i, s in enumerate(segs):
            if used[i]:
                continue
            if _close(s.p1, cur):
                cands.append((i, s, s.p2))
            elif _close(s.p2, cur):
                cands.append((i, s, s.p1))
        if not cands:
            break
        # at a junction prefer the segment heading INTO the cut (lower z,
        # then larger D): makes closed loops walk face -> OD -> depth
        i, s, p = min(cands, key=lambda c: (round(c[2][1], 6), -c[2][0]))
        used[i] = True
        r = s.signed_radius(cur, p) if s.radius else None
        path.append((p, r))
        cur = p
    if not allow_open and any(not u for u in used):
        raise ValueError("selection is not one connected profile — "
                         "select every face/edge of the outline")
    if len(path) > 2:
        # a closed loop repeats its start point: drop the duplicate
        if _close(path[-1][0], path[0][0]):
            path.pop()
    if len(path) > 2:
        # drop a LEADING radial (face annulus) that runs outward from the
        # bore: od profiles start at the outer face corner, not the bore
        (d1, z1), _ = path[0]
        (d2, z2), r2 = path[1]
        if r2 is None and abs(z1 - z2) < TOL and d2 > d1 + TOL:
            path.pop(0)
    if allow_open and len(path) > 2:
        # auto mode: drop a trailing inward radial (annulus) segment
        (d1, z1), _ = path[-2]
        (d2, z2), r2 = path[-1]
        if r2 is None and abs(z1 - z2) < TOL and d2 < d1 - TOL:
            path.pop()
    return path


def _fmt(v):
    v = round(v, 9) + 0.0  # kill float noise, normalise -0.0
    return f"{v:.6g}"


def format_profile(path):
    out = [f"{_fmt(path[0][0][0])},{_fmt(path[0][0][1])}"]
    for (d, z), r in path[1:]:
        s = f"{_fmt(d)},{_fmt(z)}"
        if r:
            s += f",{_fmt(r)}"
        out.append(s)
    return "; ".join(out)


def profile_from_segments(segs, allow_open=False):
    return format_profile(chain(segs, allow_open=allow_open))


def auto_profile(shape):
    """Outer silhouette from every face of the solid (no manual selection).
    Faces whose max radius is under half the solid max are treated as bore
    and skipped; the walk stops at the first dead end."""
    mx = max(math.hypot(v.Point.x, v.Point.y) for v in shape.Vertexes)
    segs = []
    for f in shape.Faces:
        try:
            if f.BoundBox.XMax < 0.5 * mx and f.BoundBox.YMax < 0.5 * mx:
                continue
            segs.extend(segments_from_face(f))
        except ValueError:
            continue
    return profile_from_segments(segs, allow_open=True)
