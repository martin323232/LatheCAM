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
        obj.addProperty("App::PropertyString", "ToolName", "Op",
                        translate("Lathe", "Tool name from library")).ToolName = "OD-rough"
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
            translate("Lathe", "od_finish profile as 'D,Z; D,Z; ...' from start "
                      "to end (empty = simple cylinder)"))
        obj.ProfileString = ""
        obj.addProperty("App::PropertyFloat", "ThreadPitch", "Thread").ThreadPitch = 2.0
        obj.addProperty("App::PropertyLength", "ThreadLength", "Thread").ThreadLength = 20.0
        obj.addProperty("App::PropertyLength", "ThreadLeadIn", "Thread",
                        translate("Lathe", "thread lead-in (0 = 3x pitch)")).ThreadLeadIn = 0.0
        obj.addProperty("App::PropertyBool", "ThreadSpringPass", "Thread").ThreadSpringPass = True
        obj.Proxy = self

    def to_op(self) -> Op:
        s = self.Object
        profile = []
        for pair in str(getattr(s, "ProfileString", "")).split(";"):
            pair = pair.strip()
            if not pair:
                continue
            d_str, z_str = pair.split(",")[:2]
            profile.append((float(d_str), float(z_str)))
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
            thread_spring_pass=bool(s.ThreadSpringPass),
            profile=profile,
        )

    def __getstate__(self):
        return {}

    def __setstate__(self, state):
        pass

    def onDocumentRestored(self, obj):
        self.Object = obj


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


def create_op(doc, op_type, name=None):
    register_types()
    obj = doc.addObject("App::FeaturePython", name or f"Op_{op_type}")
    OpProxy(obj, op_type)
    obj.Label = f"Op: {op_type}"
    # K5: per-type tool default
    obj.ToolName = TOOL_FOR_OP.get(op_type, "OD-rough")
    # K3: new ops append to the program (max Order + 10), not Order=10
    orders = [int(o.Order) for o in doc.Objects
              if getattr(o, "LatheRole", "") == "op" and o.Name != obj.Name]
    obj.Order = (max(orders) + 10) if orders else 10
    return obj


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
