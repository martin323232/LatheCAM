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

This generates real machine code. **Every program is checked before
posting:** LatheCAM replays the toolpath against a material model and
refuses to post if any rapid move passes through remaining material. That
check assists your setup — it does not replace it. **Always** simulate in
Masso's G-code preview and dry-run with the tool away from the part before
cutting. First cut of any new program: single block, rapid override low,
hand on feed hold.

## Built-in visualization & simulation

- **Show toolpaths** — rapids (red dashed), feed cuts (green), stock
  (translucent) drawn on the setup axis.
- **Simulate** — computes the finished part from the toolpath (revolved
  min-radius envelope) and animates the tool along the program.

Both are approximations for sanity-checking; the authoritative verification
stays Masso's preview.

## Limitations (v0.1)

- Straight external threads only (no taper, no internal threading).
- `od_finish` profile = polyline of (diameter, Z) points set via script or
  the `ProfileString` property (`"D,Z; D,Z; ..."`); no sketch-edge picking yet.
- Simulation is tool-centerline based: no insert nose-radius or
  insert-shape cutting, no cutter-compensation output.

## Tests

```
python3 tests/test_paths.py            # pure-python core, no FreeCAD needed
freecadcmd tests/test_headless.py      # full addon (run from the repo root)
```

## Example

`examples/demo_program.py` builds a complete part program headless and
writes `demo.nc`.
