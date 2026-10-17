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
   post options (feed mode, CSS on/off, program number, tool changes).
2. **Ops** — add operations (Face, OD rough/finish, ID rough/finish,
   Groove, Part, Thread). Each is a document object; edit parameters in the
   property view. `Order` controls execution sequence.
3. **Tool library** — the **Tool Library** toolbar command edits names, T
   numbers, geometry, cutting data, spindle direction and notes in a table
   dialog (saved to your per-user `tools.json`). `examples/tools_masso_g3.json`
   is a ready-made set matching the proven Masso G3 programs: copy it over
   the per-user `tools.json`, or point `Setup.ToolLibraryFile` at it.
   `ToolName` on each op must match a library entry.
4. **Post (Masso G3)** — writes a `.nc` file.

## Conventions

The post follows the conventions of a **proven** Masso G3 program (BobCAD-CAM
post `Masso_G3_Lathe`, verified on the machine):

- **X = diameter** (mm), X0 = centreline. **Z = 0 at the stock face**,
  negative into the stock (towards the chuck).
- **Program**: first line `O0100` (`Setup.ProgramNumber`), start line
  `G40 G18 G21 G90`. Optional cycle output (`Setup.CycleOutput`):
  `M64 P<n>` after the first rapid, `M65 P<n>` at program end.
- **Tool changes at machine home**: `M09`, `M05` (from the second change
  on), `G53 G0 X0.`, `G53 G0 Z0.`, `T0008 M06` (4-digit tool number),
  optional `M00 (LOAD T0008 <name>)` stop. Consecutive ops on the same tool
  emit no change — only a speed change if the RPM differs. Program end:
  `M09`, `M65 P<n>`, `M05`, home, `M30` — no final tool change.
- **Spindle**: `G50 S<MaxRPM>` clamp, then one line `G97 S<rpm> M03` —
  or `M04` for tools with `spindle_dir: ccw` (e.g. an upside-down boring
  bar). CSS mode: `G50 S<MaxRPM>` then `G96 S<m/min> M03`.
- **Feed mode** (`Setup.FeedMode`): default `mm_per_min` — `G94` with
  `F = mm/rev × rpm` (e.g. 0.04 × 2500 → `F100.`), the mode the proven
  file uses. `mm_per_rev` emits `G95` with F in mm/rev (untested on this
  machine). Thread blocks always post `G97` + `G95` so the `G32` F word is
  the pitch.
- **Threading**: `G32 Z.. F<pitch>` passes, progressive infeed (first 0.3 mm,
  decreasing), optional spring pass. Spindle runs at fixed RPM.
- Whole numbers keep the trailing dot (`X31.` `Z0.` `F100.`), like the
  proven file.

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
python3 tests/test_post.py             # v0.2.0 Masso conventions + reference regression
freecadcmd tests/test_headless.py      # full addon (run from the repo root)
freecadcmd tests/test_gui_sim.py       # workbench + commands, real exec semantics
```

`tests/reference/drum_screw_magnet_nfc.nc` is the proven machine file the
post conventions are regression-tested against.

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

### 0.2.0

Post output now matches the conventions of the user's **proven** Masso G3
file (BobCAD-CAM `Masso_G3_Lathe`); every change is regression-tested
against `tests/reference/drum_screw_magnet_nfc.nc`.

- Program number line `O0100` (`Setup.ProgramNumber`) and start line
  `G40 G18 G21 G90`.
- Tool changes move to **machine home** first: `G53 G0 X0.` / `G53 G0 Z0.`
  before every `T.. M06`; program end homes too and no longer emits a
  final tool change.
- Tool word is 4-digit zero-padded: `T0008 M06` (was `T08 M06`).
- `G50 S<MaxRPM>` spindle clamp before each spindle start; `G97 S.. M03`
  on one line; CSS drops the `D` word (`G50` is the clamp).
- **Feed mode** `Setup.FeedMode`: `mm_per_min` (default, `G94`,
  `F = mm/rev × rpm`) or `mm_per_rev` (`G95`). Thread blocks always post
  `G97` + `G95` with `F` = pitch.
- Tool library `spindle_dir`: `cw` (M03) or `ccw` (M04, e.g. upside-down
  boring bar); editable in the Tool Library dialog, which also shows a
  free-text `note` field.
- Same-tool ops no longer re-home/re-index: spindle and coolant stay on,
  only a speed change is emitted.
- Optional cycle output: `Setup.CycleOutput` + `CycleOutputP` emit
  `M64 P<n>` after the first rapid and `M65 P<n>` at the end.
- Boring with `ZStart < 0` now rapids in inside the existing bore and
  starts cutting exactly at `ZStart` (the bore mouth is no longer cut to
  the pass diameter); the final retract lifts out at the enter diameter.
- Facing allowance is part of the stock/collision model
  (`Setup.FaceAllowance`, auto-raised to the largest face-op `FaceDepth`);
  OD entry moves are lifted above it.
- `examples/tools_masso_g3.json`: the user's real Masso G3 tool set
  (T1 CCMT060208, T3 thread, T5 DCMT070204, T7 part, T8 CCGT boring ccw)
  with `spindle_dir` and VERIFY notes.

### 0.1.9

- **User tool library**: a per-user `tools.json` (FreeCAD user AppData →
  `LatheCAM_tools/tools.json`) is layered over the built-in library —
  entries override by name, new names are added, and the file survives
  addon updates. New **Tool Library** toolbar command edits names, T
  numbers, geometry and cutting data in a table dialog.
- Setup `ToolLibraryFile`: point an individual document at its own
  library JSON instead of the per-user one.
- Duplicate T numbers are rejected at post time (the machine turret keys
  on them).
- Setup `ToolChangePrompt` (default on): `T.. M06` + `M00 (LOAD T..)` stop
  for manual tooling; set **False** for a fully automatic turret (emits
  `T.. M06` only, no stop).

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
