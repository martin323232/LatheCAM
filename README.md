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
   property view. `Order` controls execution sequence. For profiled ops
   (`ProfileString`), select the part's faces/edges in the 3D view and use
   **Profile from selection** — cylinders, cones, fillets and shoulders are
   recognised and turned into the `D,Z[,R]` string; selecting the whole part
   object auto-detects the outer silhouette.
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
- **Arcs**: an `od_finish` profile entry may carry a segment radius
  (`D,Z,R`); `R>0` posts `G02`, `R<0` posts `G03`, with `R` the true
  geometric radius in mm. VERIFY on the machine: Masso's CW/CCW reading
  under diameter programming must be confirmed by dry-run; if arcs bulge
  the wrong way, negate the R values (a Setup `ArcFlip` flag can be added).
- **Nose compensation** (`Setup.NoseComp`, default off): control-side
  compensation — `G42` before the first cut, `G40` before rapids. VERIFY:
  the G41/G42 side convention on Masso is unproven; the proven file is
  post-side compensated instead. The collision checker stays conservative
  when this is on. Note: BobCAD compensates for the nose radius in the
  CAM path itself (the `R.397` corner arcs in the proven file are the
  1/64" nose radius from the BobCAD tool database). LatheCAM's NoseComp
  instead emits `G42`/`G40` and relies on the controller, which needs the
  nose radius and tip direction set in the Masso tool table. Not proven
  on this machine — keep the default `False` until a dry-run confirms it.
- **Internal threads** (`Op.ThreadInside`): cut inside the existing bore
  toward the chuck, `G97`+`G95`, retract at minor dia. VERIFY — no proven
  reference program covers internal threading on this machine; dry-run
  mandatory.
- Whole numbers keep the trailing dot and values below 1 drop the leading
  zero (`X31.` `Z0.` `F100.` `Z-.166` `R.397`), exactly like the proven file.

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
  (translucent) drawn on the setup axis. One object per op per motion kind,
  grouped under the op, so any single op's path can be hidden in the tree.
- **Simulate** — computes the finished part from the toolpath (OD cuts
  shrink the outer envelope, boring grows the bore — the result is the
  annulus between them) and animates the tool along the program.
- **Simulation panel** — a dock with **play / pause / stop**, speed
  0.25x–8x and a **scrubbable timeline**: drag the slider to move the tool
  anywhere in the program, both directions. A per-op checklist picks which
  ops participate (material and animation follow the checked set; material
  updates at op boundaries), and a status line shows the current op, move
  number and a G-code-ish readout (`G1 X20.000 Z-15.160 F.15`) of the
  segment under the slider. The plain **Simulate** button stays the quick
  one-shot path.

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

`tests/reference/drum_screw_magnet_nfc.nc` is the **only** reference
confirmed to have run on the Masso G3; the post conventions are
regression-tested against it. Everything in `tests/reference/unverified/`
is BobCAD output that has **not** been confirmed to run on the machine —
the control-line checks over those files are structure checks, not
ground truth.

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

### 0.3.25

- **Simulation panel** (issue #8): a new **Simulation panel…** toolbar
  command opens a dock with **play/pause/stop**, a **speed combo
  (0.25x–8x)** and a **timeline slider you can scrub** — drag it and the
  tool marker moves to that exact point, forwards or back. A **checkbox
  per op** (all checked by default) picks which ops participate; the
  material preview and the animation follow the checked set, with the
  `Sim_part` refreshed at every op boundary while scrubbing. A status
  line shows `Op 3/5 · label · move 14/120 · G1 X20.000 Z-15.160 F.15`
  for the segment under the slider. Under the hood `sim.animate` was
  refactored into a seekable `sim.SimPlayer` (pure position math +
  QTimer transport), which the plain **Simulate** command now also uses.

### 0.3.24

- **Post defaults to the document name** (issue #9): the Post save dialog
  now opens at `<DocName>_<date>_<time>.nc` (e.g. `D6-test_2026-10-28_1856.nc`)
  instead of `~/lathe_program_...`, so each `.nc` is traceable to its model.
  Illegal filename characters are replaced with `_`. If the document is
  saved, the dialog opens **in the document's own folder**; unsaved or
  still-unnamed documents keep the old `~/lathe_program_<stamp>.nc`
  fallback. The timestamp stays, so repeated posts never overwrite silently.

### 0.3.23

- **Per-op toolpath visibility** (issue #7): the 3D view now builds **one
  object per op per motion kind** (e.g. `Op: id_rough · feed`,
  `Op: id_finish · rapid`) inside a **sub-group per op**, instead of one
  merged compound per kind. Toggle the eye on an op's group to hide just
  that op's path, or on an individual object to hide only its rapids or
  only its cutting moves. Colors stay per kind (rapid red dashed, feed
  green solid, thread blue). Hidden paths are remembered on
  `Setup.HiddenVisPaths` and re-applied after every auto-redraw, so
  editing an op no longer re-shows paths you hid. Old documents migrate
  on the first redraw.

### 0.3.22

- **Fix: tree-click highlight never fired** (issue #5 follow-up): a single
  tree click arrives at a selection observer as `clearSelection` +
  `addSelection(doc, obj, sub, pnt)`, not `setSelection` — the highlight
  now runs from both callbacks, so clicking an op in the tree lights its
  source faces up as intended.

### 0.3.21

- **Ops remember their source face** (issue #5): an op created from a face
  selection (or filled by **Profile from selection**) now stores a
  `SourceFace` link (part object + `FaceN`) on the op. Click the op in the
  tree and those faces light up in the 3D view — instantly see which
  geometry an op was derived from. A selection observer added at workbench
  start drives the highlight; deleted or renumbered source faces degrade
  silently (no error, no highlight).
- `package.json` version realigned with the release (had drifted at 0.3.17).

### 0.3.16

- **Profile-from-selection hardening** (found by a 20-shape verification
  battery, OD and ID): auto silhouette no longer walks through an end face
  into the bore; a full disc face (blind-bore bottom) now yields its radial
  instead of raising "plane face: expected two rims"; a trailing radial that
  runs to the spindle axis is trimmed in strict mode too, while an
  intentional run-in to a bore is kept.
- New `tests/test_profile_extract_shapes.py` (12 checks) and
  `tests/test_profile_extract_shapes2.py` (12 checks): stepped shafts,
  undercut grooves, tapers, spigots, face chamfers, end rounds, shoulder
  fillets, bushings with counterbores, blind bores with bottom fillets,
  internal retaining-ring grooves, countersinks — plus extracted profile →
  `od_rough` / `id_rough` / `id_finish` → post pipeline checks.

### 0.3.15

- **Profile from selection**: new toolbar command. Select faces and/or edges
  of the revolved part in the 3D view (cylinder, cone, torus-fillet and
  shoulder faces, or the seam edges) and LatheCAM extracts the
  `D,Z[,R]` ProfileString — arc signs resolved from the geometry (CW → +R
  G02, CCW → −R G03). Selecting the whole part object auto-detects the
  outer silhouette (bore faces skipped). The string stays the source of
  truth: it lands in the op's `ProfileString` property, editable as before,
  and `ZEnd` is set to the profile's deepest Z.

### 0.3.14

- **About** command: shows the running LatheCAM version, package load path
  and FreeCAD version — settles which copy is actually loaded.

### 0.3.13

- **Auto-redraw toolpaths**: a document observer re-runs the visualization
  automatically whenever any op/setup parameter changes (signature-checked,
  so plain recomputes don't redraw). No more manual Show toolpaths click
  after every edit.

### 0.3.12

- **Simulate (choose ops)**: pick individual operations to simulate/animate;
  `Simulate` now respects a tree selection of ops.

### 0.3.11

- **Regenerate toolpaths** button: recompute + redraw + fit view in one click.

### 0.3.10

- **ThreadLeadOut**: control the thread run-out length (default 1×pitch) on
  both external and internal threading.

### 0.3.9

- **Sim tool marker**: OD tool shank now points radially outward (holder
  comes in from outside the stock); boring-bar shank stays axial toward the
  face side, as on the real machine.


### 0.3.8

- **Profile-aware OD roughing**: `od_rough` now accepts a ProfileString and
  cuts layered passes that follow the finish profile (offset outward by
  StockToLeave, floored at the pass diameter), instead of straight cylinders.
  Stepped/tapered parts rough evenly with uniform finish stock at every
  profile point; without a profile the old straight-pass behaviour is kept.

### 0.3.7

- **Timestamp in default filename**: the Post dialog now suggests
  `lathe_program_YYYY-MM-DD_HHMM.nc` so the posted time is in the filename too.
- **Late-bound post call**: the Post command calls `program.post_document`
  through the module, so reloading the module (or an addon update) takes effect
  without needing the old direct function import.

### 0.3.6

- **Post timestamp**: every posted program carries `; posted YYYY-MM-DD HH:MM:SS`
  in its header comment, so an .nc file can always be traced to the moment it
  was generated.

### 0.3.5

- **Profile-aware bore roughing**: `id_rough` now accepts a ProfileString.
  Each pass cuts the finish profile capped at D = bore + 2·DepthPerPass·k
  and offset inward by 2·StockToLeave, so every point keeps exactly
  StockToLeave radial for the finish pass. Rough paths are polylines
  (arcs belong to the finish). Without a profile `id_rough` is unchanged
  (cylindrical stepping).

### 0.3.4

- **Tool shank orientation matches the machine**: the animated marker's
  shank now runs parallel to the spindle axis (Z), extending from the tip
  back toward the face side — like a real tool holder in the turret.
  OD shank sits just outside the cut surface; boring-bar shank sits
  inside the bore with the tip projecting radially.

### 0.3.3

- **Animated tool follows the spindle side**: boring/internal-thread
  segments now draw the marker shank inward (toward the axis, through the
  bore) instead of outward through the part wall. OD segments unchanged.
- Fixed marker jumping to origin when the shape flips: assigning
  `Part::Feature.Shape` resets `Placement`, so the shape switch must
  happen before the placement is set.

### 0.3.2

- **Sim understands ID vs OD**: the material model is now dual-profile —
  OD moves leave the smallest radius reached at each Z (unchanged), while
  boring moves GROW the bore (largest radius reached, starting from the
  pre-drilled bore). A bored part now simulates as a tube, not a solid
  plug of the bore profile. Internal-thread ops count as boring.
- Fixed the sim solid being `isValid() == False`: the envelope polygon
  was never closed (`Part.makePolygon` does not auto-close), which also
  skewed the revolved volume.
- Test-suite fix: the headless suite had been aborting mid-file since
  v0.3.1 (invalid test arc `R0.4` on a 20 mm chord tripped the new arc
  validator; the runner grep missed the abort). Test profile now uses a
  valid R0.4 corner fillet; the runner checks the exit code.

### 0.3.1

- **id_finish profiles (A)**: `id_finish` accepts the same
  `ProfileString` as `od_finish` (`D,Z[,R];`, same arc sign rule). Entry:
  face start = rapid to (d0, safe Z) then feed in; buried start = rapid
  in at the enter diameter, feed out at Z. Exit: feed in to
  `x_out = min(profile diams, bore) - 1` (≥ 0.5), lift out **axially**
  through the bore, then rapid out in X — never an X+Z move below the
  face except profile cuts. id_finish blocks are collision-checked with
  the boring (inner-material) model.
- **od_finish exit (B)**: radial clear at the last Z, then axial
  withdraw — the old diagonal rapid (X and Z at once below the face) is
  gone.
- **Benchmark rewritten honestly (C)**: `drum_screw_magnet_nfc.nc` is an
  internal boring pass; the benchmark now builds it as an `id_finish`
  op, runs it through the same `CollisionChecker` `post_document` uses
  (boring=True), and checks the exit order (axial before radial).
  13/13 cutting moves still match. The profile is transcribed from the
  BobCAD output — the match proves post formatting and path discipline,
  not geometry generation.
- **Reference labelling (D)**: `drum_screw_magnet_nfc.nc` is the only
  machine-proven reference; the other BobCAD files moved to
  `tests/reference/unverified/` (structure checks only). `Part1.nc`
  (empty) and `aa.nc` (conflicting `T0101` tool word) deleted.

### 0.3.0

- **Arc output (A)**: `ProfileString` accepts `D,Z,R;` — the segment ending
  at that point posts `G02`/`G03` with `R` (true geometric radius). The
  collision checker samples arcs as chords. Chord validation rejects `R`
  smaller than half the chord.
- **Auto-chamfer (C)**: `Op.ChamferStart` / `Op.ChamferEnd` (45°, mm) cut
  the face/shoulder corner of an `od_finish` profile without hand-editing
  the profile; guards reject chamfers that do not fit the adjacent segment.
- **finish_feed (B)**: tools carry an optional `finish_feed` (mm/rev) used
  by finish ops when the op feed is 0; new column in the Tool Library
  dialog. `examples/tools_masso_g3.json`: DCMT070204 → 0.03 (F75 @ 2500).
- **Nose compensation (N, opt-in)**: `Setup.NoseComp` posts `G42`/`G40`
  around cuts (VERIFY — see Conventions).
- **Internal threading (E, VERIFY)**: `Op.ThreadInside` threads an existing
  bore toward the chuck.
- **Number format**: values below 1 drop the leading zero (`Z-.166`,
  `R.397`, `F.381`) — byte-matching the proven file's style.
- **References (D)**: BobCAD programs live in `tests/reference/`
  (relabelled in 0.3.1: only `drum_screw_magnet_nfc.nc` is machine-proven,
  the rest are in `unverified/`); control-line order is regression-tested.
- **Benchmark**: `examples/benchmark_drum_screw.py` reproduces the proven
  `drum_screw_magnet_nfc.nc` finish pass — 13/13 cutting moves match
  (including the four `R.397` arcs) and the control lines post in proven
  order. Differences are deliberate: explicit `G94`/`F` words, safer rapid
  clearances, no final `T0001 M06`.

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
