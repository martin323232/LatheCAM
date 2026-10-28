"""FreeCAD document objects for LatheCAM (headless-safe).

Object types:
  Lathe::Setup  — stock + machine settings
  Lathe::Op     — one turning operation (linked to a Setup)

Both are App::FeaturePython with a Python proxy; usable from freecadcmd.
"""

import FreeCAD

from .model import Stock, Tool, Op, OP_TYPES

if FreeCAD.GuiUp:
    from PySide import QtCore
    translate = QtCore.QCoreApplication.translate
else:
    def translate(context, text, disambig=None):
        return text

TYPE_SETUP = "Lathe::Setup"
TYPE_OP = "Lathe::Op"

_registered = False


def register_types():
    """Register the FeaturePython proxy types (idempotent)."""
    global _registered
    if _registered:
        return
    _registered = True


def get_setup(obj):
    """Return the Lathe::Setup in the document (first one)."""
    for o in obj.Document.Objects:
        if o.TypeId == "App::FeaturePython" and getattr(o, "LatheRole", "") == "setup":
            return o
    return None


class SetupProxy:
    """Stock + machine parameters."""

    def __init__(self, obj):
        self.Object = obj
        obj.addProperty("App::PropertyString", "LatheRole", "Lathe").LatheRole = "setup"
        obj.addProperty("App::PropertyLength", "StockDiameter", "Stock",
                        translate("Lathe", "Raw stock diameter")).StockDiameter = 30.0
        obj.addProperty("App::PropertyLength", "StockLength", "Stock",
                        translate("Lathe", "Length from face into waste")).StockLength = 60.0
        obj.addProperty("App::PropertyLength", "BoreDiameter", "Stock",
                        translate("Lathe", "Pre-drilled bore (0 = solid)")).BoreDiameter = 0.0
        obj.addProperty("App::PropertyLength", "FaceAllowance", "Stock",
                        translate("Lathe", "Uncut facing allowance in front of "
                                  "Z0 (auto-raised to the largest face-op "
                                  "FaceDepth; collision model sees this material)")
                        ).FaceAllowance = 0.0
        obj.addProperty("App::PropertyFloat", "MaxRPM", "Machine",
                        translate("Lathe", "Spindle RPM limit")).MaxRPM = 3000.0
        obj.addProperty("App::PropertyFloat", "SurfaceSpeed", "Machine",
                        translate("Lathe", "CSS m/min (if CSS used)")).SurfaceSpeed = 150.0
        obj.addProperty("App::PropertyBool", "UseCSS", "Machine",
                        translate("Lathe", "Constant surface speed (G96)")).UseCSS = False
        obj.addProperty("App::PropertyBool", "LineNumbers", "Post",
                        translate("Lathe", "Emit N line numbers")).LineNumbers = False
        obj.addProperty("App::PropertyBool", "ToolChange", "Post",
                        translate("Lathe", "Emit T/M6 tool changes")).ToolChange = True
        obj.addProperty("App::PropertyBool", "ToolChangePrompt", "Post",
                        translate("Lathe", "Stop (M00) at each tool change for "
                                  "manual confirmation; off = automatic turret")
                        ).ToolChangePrompt = True
        obj.addProperty("App::PropertyFile", "ToolLibraryFile", "Post",
                        translate("Lathe", "Optional tool-library JSON replacing "
                                  "the built-in set (per-user tools.json is "
                                  "layered on top unless this is set)")
                        ).ToolLibraryFile = ""
        obj.addProperty("App::PropertyEnumeration", "FeedMode", "Post",
                        translate("Lathe", "Feed mode: mm_per_min = G94 with "
                                  "F in mm/min (proven on Masso G3); "
                                  "mm_per_rev = G95 with F in mm/rev "
                                  "(UNTESTED on this machine)")
                        ).FeedMode = ["mm_per_min", "mm_per_rev"]
        obj.FeedMode = "mm_per_min"
        obj.addProperty("App::PropertyInteger", "ProgramNumber", "Post",
                        translate("Lathe", "Program number -> first line "
                                  "O0100")).ProgramNumber = 100
        obj.addProperty("App::PropertyBool", "CycleOutput", "Post",
                        translate("Lathe", "Emit M64 P<n> after the first "
                                  "rapid and M65 P<n> at program end (chip "
                                  "conveyor / coolant pump interlock)")
                        ).CycleOutput = False
        obj.addProperty("App::PropertyInteger", "CycleOutputP", "Post",
                        translate("Lathe", "P word for M64/M65 cycle output")
                        ).CycleOutputP = 1
        obj.addProperty("App::PropertyBool", "NoseComp", "Post",
                        translate("Lathe", "Control-side nose-radius "
                                  "compensation: emit G42 before cuts and "
                                  "G40 before rapids (VERIFY on machine: "
                                  "G41/G42 side convention unproven)")
                        ).NoseComp = False
        obj.addProperty("App::PropertyPlacement", "ViewPlacement", "View",
                        translate("Lathe", "Placement of visualization/simulation "
                                  "objects in the model (lathe Z = spindle axis)")
                        ).ViewPlacement = FreeCAD.Placement()
        obj.Proxy = self

    def to_stock(self) -> Stock:
        o = self.Object
        return Stock(diameter=float(o.StockDiameter),
                     length=float(o.StockLength),
                     bore_diameter=float(o.BoreDiameter),
                     max_rpm=float(o.MaxRPM))

    def __getstate__(self):
        return {}

    def __setstate__(self, state):
        pass

    def onDocumentRestored(self, obj):
        self.Object = obj


class OpProxy:
    """One turning operation."""

    def __init__(self, obj, op_type="od_rough"):
        self.Object = obj
        obj.addProperty("App::PropertyString", "LatheRole", "Lathe").LatheRole = "op"
        obj.addProperty("App::PropertyString", "OpType", "Op",
                        translate("Lathe", "Operation type")).OpType = op_type
        obj.addProperty("App::PropertyEnumeration", "ToolName", "Op",
                        translate("Lathe", "Tool name from library"))
        _set_tool_enum(obj, default_tool_for(op_type))
        obj.addProperty("App::PropertyString", "Comment", "Op",
                        translate("Lathe", "Comment")).Comment = ""
        obj.addProperty("App::PropertyInteger", "Order", "Op",
                        translate("Lathe", "Execution order")).Order = 10
        # PropertyDistance (not Length) so negative Z values are allowed
        obj.addProperty("App::PropertyDistance", "ZStart", "Geometry").ZStart = 0.0
        obj.addProperty("App::PropertyDistance", "ZEnd", "Geometry").ZEnd = -30.0
        obj.addProperty("App::PropertyLength", "TargetDiameter", "Geometry").TargetDiameter = 20.0
        obj.addProperty("App::PropertyLength", "FaceDiameter", "Geometry").FaceDiameter = 0.0
        obj.addProperty("App::PropertyLength", "FaceDepth", "Geometry",
                        translate("Lathe", "face op: axial depth to cut off the end")).FaceDepth = 2.0
        obj.addProperty("App::PropertyLength", "GrooveDiameter", "Geometry").GrooveDiameter = 10.0
        obj.addProperty("App::PropertyLength", "DepthPerPass", "Cutting").DepthPerPass = 1.5
        obj.addProperty("App::PropertyLength", "StockToLeave", "Cutting").StockToLeave = 0.5
        obj.addProperty("App::PropertyLength", "Peck", "Cutting").Peck = 1.0
        obj.addProperty("App::PropertyFloat", "Feed", "Cutting",
                        translate("Lathe", "mm/rev (0 = tool default)")).Feed = 0.0
        obj.addProperty("App::PropertyFloat", "RPM", "Cutting",
                        translate("Lathe", "spindle rpm (0 = tool default)")).RPM = 0.0
        obj.addProperty("App::PropertyLength", "ThreadMajorDia", "Thread").ThreadMajorDia = 20.0
        obj.addProperty(
            "App::PropertyString", "ProfileString", "Geometry",
            translate("Lathe", "od_finish profile as 'D,Z[,R]; D,Z[,R]; ...' "
                      "from start to end (empty = simple cylinder). R applies "
                      "to the segment ENDING at that point: >0 G02 CW, "
                      "<0 G03 CCW, in mm"))
        obj.ProfileString = ""
        obj.addProperty("App::PropertyLinkSub", "SourceFace", "Geometry",
                        translate("Lathe", "the source object + FaceN this "
                                  "op was created from (set automatically "
                                  "from the face selection; clicking the op "
                                  "in the tree lights these faces up)"))
        obj.addProperty("App::PropertyLength", "ChamferStart", "Geometry",
                        translate("Lathe", "od_finish: 45° chamfer (mm) at "
                                  "the face corner of the profile, 0 = off")
                        ).ChamferStart = 0.0
        obj.addProperty("App::PropertyLength", "ChamferEnd", "Geometry",
                        translate("Lathe", "od_finish: 45° chamfer (mm) at "
                                  "the shoulder/back corner of the profile, "
                                  "0 = off")
                        ).ChamferEnd = 0.0
        obj.addProperty("App::PropertyFloat", "ThreadPitch", "Thread").ThreadPitch = 2.0
        obj.addProperty("App::PropertyLength", "ThreadLength", "Thread").ThreadLength = 20.0
        obj.addProperty("App::PropertyLength", "ThreadLeadIn", "Thread",
                        translate("Lathe", "thread lead-in (0 = 3x pitch)")).ThreadLeadIn = 0.0
        obj.addProperty("App::PropertyLength", "ThreadLeadOut", "Thread",
                        translate("Lathe", "thread run-out past the thread "
                                  "end (0 = 1x pitch)")).ThreadLeadOut = 0.0
        obj.addProperty("App::PropertyBool", "ThreadSpringPass", "Thread").ThreadSpringPass = True
        obj.addProperty("App::PropertyDistance", "ThreadZLimit", "Thread",
                        translate("Lathe", "deepest Z the thread may reach "
                                  "(shoulder/undercut; 0 = only stock length "
                                  "limits it)")).ThreadZLimit = 0.0
        obj.addProperty("App::PropertyBool", "ThreadInside", "Thread",
                        translate("Lathe", "internal thread: cut inside the "
                                  "existing bore toward the chuck (VERIFY: "
                                  "no proven reference on this machine)")
                        ).ThreadInside = False
        obj.addProperty("App::PropertyBool", "BoreBackCut", "Cutting",
                        translate("Lathe", "bore ops: cut toward the face (+Z) "
                                  "instead of toward the chuck (-Z)")
                        ).BoreBackCut = False
        obj.Proxy = self

    def to_op(self) -> Op:
        s = self.Object
        profile = []
        for pair in str(getattr(s, "ProfileString", "")).split(";"):
            pair = pair.strip()
            if not pair:
                continue
            parts = [p.strip() for p in pair.split(",")]
            try:
                d, z = float(parts[0]), float(parts[1])
                r = float(parts[2]) if len(parts) > 2 and parts[2] else 0.0
            except (ValueError, IndexError):
                raise ValueError(f"ProfileString: bad entry '{pair}' "
                                 "(expected 'D,Z' or 'D,Z,R')") from None
            profile.append((d, z, r))
        return Op(
            type=s.OpType,
            tool=s.ToolName,
            comment=s.Comment or s.OpType,
            z_start=float(s.ZStart),
            z_end=float(s.ZEnd),
            target_diameter=float(s.TargetDiameter),
            face_diameter=float(s.FaceDiameter),
            face_depth=float(getattr(s, "FaceDepth", 2.0)),
            depth_per_pass=float(s.DepthPerPass),
            stock_to_leave=float(s.StockToLeave),
            feed=float(s.Feed) or None,
            rpm=float(s.RPM) or None,
            groove_diameter=float(s.GrooveDiameter),
            peck=float(s.Peck),
            thread_major_diameter=float(s.ThreadMajorDia),
            thread_pitch=float(s.ThreadPitch),
            thread_length=float(s.ThreadLength),
            thread_lead_in=float(getattr(s, "ThreadLeadIn", 0.0)) or None,
            thread_lead_out=float(getattr(s, "ThreadLeadOut", 0.0)) or None,
            thread_spring_pass=bool(s.ThreadSpringPass),
            thread_z_limit=float(getattr(s, "ThreadZLimit", 0.0)) or None,
            bore_back_cut=bool(getattr(s, "BoreBackCut", False)),
            profile=profile,
            chamfer_start=float(getattr(s, "ChamferStart", 0.0) or 0.0),
            chamfer_end=float(getattr(s, "ChamferEnd", 0.0) or 0.0),
            thread_inside=bool(getattr(s, "ThreadInside", False)),
        )

    def __getstate__(self):
        return {}

    def __setstate__(self, state):
        pass

    def onDocumentRestored(self, obj):
        self.Object = obj
        # migrate pre-enumeration documents: ToolName was a plain string
        try:
            if obj.getTypeIdOfProperty("ToolName") == "App::PropertyString":
                cur = obj.ToolName
                obj.removeProperty("ToolName")
                obj.addProperty("App::PropertyEnumeration", "ToolName", "Op",
                                translate("Lathe", "Tool name from library"))
                _set_tool_enum(obj, cur or default_tool_for(obj.OpType))
        except Exception:
            pass


def create_setup(doc, name="Setup"):
    register_types()
    obj = doc.addObject("App::FeaturePython", name)
    SetupProxy(obj)
    obj.Label = "Lathe Setup"
    return obj


# Sensible default tool per operation type (K5): a thread op must never
# silently inherit the roughing tool's feed/RPM.
TOOL_FOR_OP = {
    "face": "OD-finish",
    "od_rough": "OD-rough",
    "od_finish": "OD-finish",
    "id_rough": "Bore",
    "id_finish": "Bore",
    "groove": "Groove 3mm",
    "part": "Part 2mm",
    "thread": "Thread 60deg",
}

# Fallback when the library has no tool with the placeholder name above.
# A real machine table names its tools after the inserts in the turret, so
# matching on kind beats pointing a new op at a tool that does not exist.
KIND_FOR_OP = {
    "face": "turn",
    "od_rough": "turn",
    "od_finish": "turn",
    "id_rough": "bore",
    "id_finish": "bore",
    "groove": "groove",
    "part": "part",
    "thread": "thread",
}


def default_tool_for(op_type):
    """Best tool name for a new op of ``op_type`` in the current library."""
    name = TOOL_FOR_OP.get(op_type, "OD-rough")
    try:
        from .program import load_tools
        lib = load_tools()
    except Exception:
        return name
    if name in lib:
        return name
    kind = KIND_FOR_OP.get(op_type, "turn")
    matches = sorted((t.tool_number, t.name) for t in lib.values()
                     if t.kind == kind)
    return matches[0][1] if matches else name


def _tool_names(obj=None):
    """Tool names for the ToolName dropdown: built-in + user library."""
    names = [t.name for t in Tool.default_library()]
    try:
        from .program import load_tools
        names = sorted(load_tools().keys())
    except Exception:
        pass
    return names


def _set_tool_enum(obj, value):
    """Populate the ToolName enumeration and select ``value``.

    The current value is always kept in the list so a tool deleted from
    the library never wipes an existing op's setting.
    """
    names = _tool_names(obj)
    cur = getattr(obj, "ToolName", None)
    for extra in (value, cur):
        if extra and extra not in names:
            names.append(extra)
    try:
        obj.ToolName = names  # list assignment defines the enumeration
        if value:
            obj.ToolName = value
    except Exception:
        pass


def refresh_tool_enums(doc):
    """Re-read the library into every op's ToolName dropdown in ``doc``."""
    for o in doc.Objects:
        if getattr(o, "LatheRole", "") == "op":
            _set_tool_enum(o, None)


def create_op(doc, op_type, name=None):
    register_types()
    obj = doc.addObject("App::FeaturePython", name or f"Op_{op_type}")
    OpProxy(obj, op_type)
    obj.Label = f"Op: {op_type}"
    # K5: per-type tool default is set by OpProxy via the ToolName enumeration
    # (assigning TOOL_FOR_OP here would bypass it and fail for names the
    # enumeration dropped).
    # K3: new ops append to the program (max Order + 10), not Order=10
    orders = [int(o.Order) for o in doc.Objects
              if getattr(o, "LatheRole", "") == "op" and o.Name != obj.Name]
    obj.Order = (max(orders) + 10) if orders else 10
    return obj


def source_faces(op):
    """(part_object, ['FaceN', ...]) the op was derived from, or None.

    Degrades silently: a deleted source object or a renumbered/vanished face
    (topological naming) simply yields None — the highlight is cosmetic and
    must never raise.
    """
    try:
        if getattr(op, "LatheRole", "") != "op":
            return None
        link = getattr(op, "SourceFace", None)
        if not link:
            return None
        src, subs = link
        if src is None or not hasattr(src, "Shape"):
            return None
        faces = [str(s).split(".")[-1] for s in (subs or ())]
        faces = [f for f in faces if f.startswith("Face")
                 and src.getSubObject(f) is not None]
        return (src, faces) if faces else None
    except Exception:
        return None


def collect_program(doc):
    """Return (setup_obj, [op_obj, ...] sorted by Order)."""
    setup = None
    ops = []
    for o in doc.Objects:
        role = getattr(o, "LatheRole", "")
        if role == "setup":
            setup = o
        elif role == "op":
            ops.append(o)
    ops.sort(key=lambda o: (int(o.Order), o.Name))
    return setup, ops
