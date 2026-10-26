"""LatheCAM workbench for FreeCAD 1.x.

FreeCAD 1.x runs InitGui.py via exec() inside a function scope
(FreeCADGuiInit.RunInitGuiPy), so module-level imports are stored in a
locals dict that is discarded before Initialize() runs — referencing
them later raises NameError at workbench activation. All imports used
inside Initialize() must therefore happen inside Initialize() itself.
"""

import FreeCADGui as Gui


class LatheCAMWorkbench(Gui.Workbench):
    MenuText = "LatheCAM"
    ToolTip = "Turning CAM for Masso lathes"
    Icon = ""

    def Initialize(self):
        import FreeCADGui as Gui
        from LatheCAM import gui
        from LatheCAM.model import OP_TYPES

        cmds = ["CmdLatheSetup"]
        Gui.addCommand("CmdLatheSetup", gui.CmdSetup())
        cmds.append("CmdLatheTools")
        Gui.addCommand("CmdLatheTools", gui.CmdToolLibrary())
        for t in OP_TYPES:
            name = f"CmdLatheOp_{t}"
            Gui.addCommand(name, gui.make_op_command(t))
            cmds.append(name)
        cmds.append("CmdLathePost")
        Gui.addCommand("CmdLathePost", gui.CmdPost())
        cmds.append("CmdLatheVis")
        Gui.addCommand("CmdLatheVis", gui.CmdVisualize())
        cmds.append("CmdLatheRegen")
        Gui.addCommand("CmdLatheRegen", gui.CmdRegenerate())
        cmds.append("CmdLatheSim")
        Gui.addCommand("CmdLatheSim", gui.CmdSimulate())
        cmds.append("CmdLatheSimChoose")
        Gui.addCommand("CmdLatheSimChoose", gui.CmdSimulateChoose())
        cmds.append("CmdLatheAbout")
        Gui.addCommand("CmdLatheAbout", gui.CmdAbout())
        self.appendToolbar("LatheCAM", cmds)
        self.appendMenu("LatheCAM", cmds)
        # keep the drawn toolpaths in step with op edits (App-level observer:
        # Gui.addDocumentObserver never calls Python hooks)
        import FreeCAD as App
        if not gui.observer_armed():
            gui.arm_auto_observer(App)

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(LatheCAMWorkbench())
