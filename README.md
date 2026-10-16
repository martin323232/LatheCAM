# LatheCAM — turning CAM for FreeCAD 1.x + Masso G3

Pure-Python FreeCAD workbench: parametric turning operations (facing, OD/ID
rough + finish, grooving, part-off, straight G32 threading) with a
**Masso G3 lathe post-processor**. No LibLathe/C++ dependency, no legacy
Path API — works on FreeCAD 1.0/1.1.x, GUI and headless.

## Install (Windows 11)

1. Find your FreeCAD user Mod folder:
   `%APPDATA%\FreeCAD\v1-1\Mod` (create `Mod` if missing).
2. Copy the `LatheCAM` folder from this repo into it, so you get
   `...\Mod\LatheCAM\InitGui.py`.
3. Restart FreeCAD → select the **LatheCAM** workbench.

(Alternative: `git clone` this repo and symlink/copy the inner `LatheCAM/`
folder into Mod.)

## Workflow

1. **Lathe Setup** — set stock diameter/length, bore (if any), RPM limit,
   post options (CSS on/off, line numbers, tool changes).
2. **Ops** — add operations (Face, OD rough/finish, ID rough/finish,
   Groove, Part, Thread). Each is a document object; edit parameters in the
   property view. `Order` controls execution sequence.
3. **Tool library** — `LatheCAM/tool_library.json`; edit names, numbers,
   widths, feeds, RPMs. `ToolName` on each op must match.
4. **Post (Masso G3)** — writes a `.nc` file.

## Conventions

- **X = diameter** (mm), X0 = centreline. **Z = 0 at the stock face**,
  negative into the stock (towards the chuck).
- Default spindle/feed mode: `G97` fixed RPM + `G95` mm/rev (Masso uses
  G95 for feed-per-rev — not G99). CSS (`G96 D<maxrpm> S<m/min>`) is
  opt-in on the Setup object; Masso docs warn CSS+G95 don't mix, so the
  post converts feed to mm/min in CSS mode.
- Threading: `G32 Z.. F<pitch>` passes, progressive infeed (first 0.3 mm,
  decreasing), optional spring pass. Spindle runs at fixed RPM.

## Safety

This generates real machine code. **Always** simulate in Masso's G-code
preview and dry-run with the tool away from the part before cutting. First
cut of any new program: single block, rapid override low, hand on feed hold.

## Limitations (v0.1)

- Straight external threads only (no taper, no internal threading).
- `od_finish` profile = polyline of (diameter, Z) points set via script or
  property edit; no sketch-edge picking yet.
- No toolpath simulation/verification view — use Masso's preview or
  `ncviewer`.
- No cutter-compensation / nose-radius compensation output.

## Tests

```
python3 tests/test_paths.py            # pure-python core
freecadcmd tests/test_headless.py      # full addon in FreeCAD 1.1.3
freecadcmd tests/test_gui_sim.py       # workbench + commands, real exec semantics
freecadcmd tests/test_gui_sim.py      # workbench+commands, real exec semantics
```

## Example

`examples/demo_program.py` builds a complete part program headless and
writes `demo.nc`.
