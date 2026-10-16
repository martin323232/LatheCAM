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

`examples/build_examples.py` builds three ready-to-use parts into
`examples/parts/` (each an FCStd with Setup + ops + toolpath visualization
+ simulated part, plus the posted NC):

| Part | Stock | Exercises |
|---|---|---|
| `spindle_shaft` | Ø30 × 62 | facing, 2-step roughing, profile finish w/ chamfer, relief groove, M16×2 G32 thread, part-off |
| `bushing` | Ø30 × 32, pre-drilled Ø12 | OD + **boring** (cuts toward chuck, no diagonal feeds), part-off |
| `v_pulley` | Ø52 × 24, pre-drilled Ø12 | **10 mm wide groove** (multi-centre, collision-free rapids), bore Ø19, part-off |

## Changelog

### 0.1.8

- `od_rough` with a mid-length start (`ZStart < 0`) now plunges exactly at
  `ZStart` — material in front of the start keeps its stock diameter.
- Tool changes stop coolant + spindle first: `M09`, `M05`, `T.. M06`,
  `M00 (LOAD T.. <tool>)` (was `M01`, which only stops with optional-stop on).
- Multi-centre grooves rapid straight out in X after each centre — wide
  grooves post collision-free.
- Collision checker models groove/part kerf width (`z_reference` corner);
  a rapid whose centre is in the air but whose edge overlaps material is
  now caught.
- Boring cuts toward the chuck (−Z) with no diagonal feeds between passes;
  `BoreBackCut = True` restores back-boring (+Z).
- Tool library field `z_reference` (`left` | `right` | `centre`): groove/part
  programmed Z is offset so the kerf stays centred on the op Z for how the
  tool was touched off (default `left` = chuck-side corner).
- Thread op `ThreadZLimit`: posting fails if the G32 lead-out passes the
  shoulder/undercut Z.
- Removed committed `__pycache__`/`.pyc`; added `.gitignore`.

### 0.1.7

- `ViewPlacement` on the Setup object: visualization and simulation output
  is placed through it, so alignment with the model survives re-runs.

### 0.1.6

- Collision-safe rapids (K1/K2) + pre-post collision checker, proxy
  persistence, per-type tool defaults, auto op order, thread G97/G95 under
  CSS, Z-stepped facing, groove width, thread lead-out validation,
  G18/M05-once/coolant, license + icon + tests.
