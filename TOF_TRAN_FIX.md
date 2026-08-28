# TOF_train8: plan + progress log

Goal: create the `ODIN_TOF_train8` prototype that drives the TOF-train mechanism through
the existing CLI plumbing (`mcrun --tof-trains=K` → global `NTOF` → cogen'ed
`N_trains` / `t_offset` / `p_trains` / ... ) instead of train7's instrument-parameter +
uservar approach. Keep train7's efficient `N_active` compaction model (alive sub-particles
are a contiguous prefix `[0, N_active)`), which requires one small extension of the cogen
contract (add `N_active`, drop the unused `alive_trains` array).

Working branch: `TOF_train8` (created from `origin/TOF_train_experiments`, base commit
c0fe02583 "Retire older Rrototypes..."). Repo root: /Users/user/Projects/McStas/McCode

## 1. Gap analysis (train7 uservars vs cogen contract)

| train7 uservar (ODIN_TOF_train7/ODIN_wfm.instr:109-119) | cogen contract | verdict |
|---|---|---|
| `t_offset` (double*) | `t_offset` | covered |
| `p_trains` (double*) | `p_trains` | covered |
| `p_last_time_manipulation` (double) | `P_last_time_manipulation` | covered, renamed (capital P) |
| `adaptive_N` (int) | `adaptive_N` (init `=NTOF` in generated code_main) | covered |
| `total_arrived` / `total_N_sent` / `total_rays_sent` | same names | covered |
| `N_active` (int) | **none** | **NOT covered → Task 1 adds it** |
| `allocated` (instr DECLARE) | n/a | dropped (alloc moves to runtime) |

Two problems found:
1. `N_active` has no counterpart in the cogen contract. The kept train4 prototype
   (`ODIN_MCPL_TOF_train4`) instead used `p_trains[i]==0` as liveness. We keep train7's
   compaction model (proven faster: no float-zero checks, shrinking loop) and add
   `N_active` to the contract; the never-used `alive_trains` array is removed.
2. `P_last_time_manipulation` is never zeroed per ray by the plumbing: on GPU the struct
   member is uninitialized (mcstas-r.c mcsetstate doesn't set it); on CPU the global
   persists between rays. train7 survived because uservars are zeroed per particle.
   Fix in core: Tasks 1 (CPU, in generated `mcgenstate()`) and 3 (GPU, in `mcsetstate`).

## 2. Tasks

### Task 1 — cogen contract: add `N_active`, remove `alive_trains`, CPU zeroing
File: `mccode/src/cogen.c.in`
- [x] header CPU-global block (~line 2499-2511): after the `int N_trains;` line add
  `cout("int N_active;");`; remove `cout("int *alive_trains;");` (line 2505) and
  `cout("int alive_trains[NTOF_GPU_STATIC];");` (line 2509)
- [x] OpenACC particle-member block (~line 2572-2578): after the `int N_trains;` member
  add `cout("  int N_active;");`; remove `cout("  int *alive_trains;");` (line 2576)
- [x] `def_trace_section` (~line 1647): replace
  `cout("#define alive_trains (_particle->alive_trains)");` with
  `cout("#define N_active (_particle->N_active)");`
- [x] `undef_trace_section` (~line 1710): replace `cout("#undef alive_trains");` with
  `cout("#undef N_active");`
- [x] end-of-raytrace free block (~line 2039): remove
  `cout("  if (alive_trains) free(alive_trains);");`
- [x] inner-loop free block (~line 2129): remove
  `cout("      if (alive_trains) free(alive_trains);");`
- [x] generated `mcgenstate()` body (~line 2681, before `cout("  return(particle);");`):
  emit per-ray CPU zeroing (works in both embedded-runtime and link-against-runtime
  builds because it lands in the generated TU):
  ```c
  cout("  #ifdef TOF_TRAIN");
  cout("  #ifndef OPENACC");
  cout("  N_active = 0;");
  cout("  P_last_time_manipulation = 0;");
  cout("  #endif");
  cout("  #endif");
  ```

### Task 2 — `common/lib/share/mccode_main.c`
File: `common/lib/share/mccode_main.c` (CPU init block, lines 157-164)
- [x] remove `alive_trains=malloc(NTOF*sizeof(int));` (line 162)

### Task 3 — `mccode/nlib/share/mcstas-r.c` (GPU per-ray zeroing)
File: `mccode/nlib/share/mcstas-r.c`, `mcsetstate()` TOF_TRAIN block (lines 84-93)
- [x] remove `mcneutron.alive_trains=malloc(NTOF*sizeof(int));` (line 90)
- [x] add (inside the existing `#ifdef TOF_TRAIN #ifdef OPENACC` block, any of the
  allocation variants):
  ```c
  mcneutron.N_active = 0;
  mcneutron.P_last_time_manipulation = 0;
  ```
  (put it OUTSIDE the `#ifndef NTOF_GPU_STATIC` sub-guard so both dynamic and static
  GPU modes zero the scalar members)

### Task 4 — `common/lib/share/mccode-r.c` (small CLI fixes, train8 exercises this path)
- [x] line 4844: fix format string with two `%i` but one argument:
  `fprintf(stderr, "WARNING: Instrument was compiled with -DNTOF_GPU_STATIC=%i, reducing to --tof-trains=%i\n");`
  → add the missing argument (`NTOF_GPU_STATIC, NTOF_GPU_STATIC`)
- [x] line 4541: help text says "(default 10)" but code default is 100 (line 4727);
  change help to "(default 100)"

### Task 5 — rebuild core into conda env
- [x] Activate env: `source /Users/user/miniforge3/etc/profile.d/conda.sh && conda activate mcstas-dev`
- [x] Build+install (persistent build dir for fast incremental rebuilds):
  `python devel/bin/mccode-build-conda -b /var/folders/1t/8gxgl7l51dd3pk_tbw0mvtj80000gn/T/opencode/mccode-build`
  (full build on first run; this reinstalls cogen + mcrun (with `--tof-trains`) + tools
  from this branch into the env)
- [x] Smoke-check: `/Users/user/miniforge3/envs/mcstas-dev/bin/mcrun --help | grep tof-trains`

### Task 6 — new prototype `mcstas-comps/examples/Prototypes/ODIN_TOF_train8/`
Reference: train7 = `mcstas-comps/examples/Prototypes/ODIN_TOF_train7/`
(macros at ESS_butterfly.comp:153-201, source block ~:590-680), train4 =
`ODIN_MCPL_TOF_train4/` (bare-name loop patterns).

- [x] `TOF_trains-lib.h` (NEW): `T_ABSORB`/`T_TRANSMIT` + `TRAIN_GATE` + `TRAIN_READ`
  ported from train7 ESS_butterfly.comp:153-201 with ALL `_particle->` prefixes removed
  (bare `N_trains`, `N_active`, `t_offset`, `p_trains`, `P_last_time_manipulation`).
  Pure `#define`s only — safe to %include from SHARE because expansion happens inside
  TRACE, where cogen's GPU defines are active. Keep the swap-with-last compaction.
- [x] `ODIN_wfm.instr` (from train7, keep name): remove `N_trains_par` param (keep
  `target_tsplit`); remove USERVARS block; remove `int allocated;` DECLARE; remove
  `#define N_trains INSTRUMENT_GETPAR(N_trains_par)`; remove the Origin-EXTEND malloc
  block; remove the FINALLY free block
- [x] `CSPEC.instr` (from train7, keep name): same stripping (its `N_trains_par=200`,
  USERVARS ~:221, `allocated` ~:217, `#define` ~:456, EXTEND malloc ~:491-498,
  FINALLY free ~:1542-1544)
- [x] `ESS_butterfly.comp` (from train7): remove TRAIN macros from SHARE (now in
  TOF_trains-lib.h); source block in bare names: set `P_last_time_manipulation = 0;`
  BEFORE the train-fill loop (defensive, in addition to core zeroing), adaptive-N
  formula (`total_N_sent==0 ? N_trains : ceil(target_tsplit*total_N_sent/total_arrived)`,
  capped at `N_trains`) into bare `adaptive_N`, fill `t_offset[i]`/`p_trains[i]` for
  `i<adaptive_N`, then `N_active = adaptive_N;`
- [x] `DiskChopper.comp`, `MultiDiskChopper.comp` (from train7): add
  `%include "TOF_trains-lib"` in SHARE; gate body unchanged (jitter drawn once per gate)
- [x] `TOF_monitor.comp`, `TOFLambda_monitor.comp` (from train7): add
  `%include "TOF_trains-lib"` in SHARE; TRAIN_READ usage unchanged
- [x] `Monitor_nD.comp` (from train7): add `%include "TOF_trains-lib"` in SHARE;
  TRAIN_READ + `total_*` bookkeeping in bare names; remove the dead commented block that
  references `alive_trains` (~:596-602)
- [x] `ESS_butterfly-lib.c`, `ESS_butterfly-lib.h`: copy unchanged from train7
- [x] `bi_spec_ellipse.comp`: copy from `ODIN_MCPL_TOF_train4/` (train7 is missing it —
  ODIN_wfm.instr uses it; this fixes a train7 build gap)

### Task 7 — verification
Run all simulations in a scratch dir, e.g.
`/var/folders/1t/8gxgl7l51dd3pk_tbw0mvtj80000gn/T/opencode/train8-test/`
(env: `source /Users/user/miniforge3/etc/profile.d/conda.sh && conda activate mcstas-dev`;
use `bin/mcrun`, `bin/mcstas`).
- [x] Build train8 ODIN: in the prototype dir,
  `mcrun -c ODIN_wfm.instr --D1 "-DTOF_TRAIN" --no-mpi -n 1000` (or manual:
  `mcstas ODIN_wfm.instr -c -o ODIN_wfm.c` then
  `cc -O2 -DTOF_TRAIN -I$CONDA_PREFIX/include/mcstas ODIN_wfm.c -lm -o ODIN_wfm`)
- [x] Parity run (the key check): same `-n` (e.g. 20000) and `--seed` for
  train7 ODIN (no -DTOF_TRAIN, `N_trains_par=10`) vs train8 ODIN
  (`--tof-trains=10`); compare `time.dat`, `wavelength_tof.dat`, `image.dat` — expect
  near-identical profiles (same physics, same RNG draw order)
- [x] Small-N sanity: train8 with `--tof-trains=4` completes and gives sane output
- [x] Regression smoke: build+run a stock instrument WITHOUT -DTOF_TRAIN (any simple
  tutorial instr) to confirm default cogen output is unaffected; optionally diff cogen
  output of a stock instr before/after the core change (expect no diff outside
  #ifdef TOF_TRAIN guards — there should be none at all)
- [x] CSPEC: build+short run (CSPEC is long; `-n 1000` is fine) with `--tof-trains=20`
- [x] Format changed .comp/.instr files: `devel/bin/mccode-clangformat` (see skill
  pr-clangformat) on the ODIN_TOF_train8 dir
- [x] Update git status; DO NOT commit (no commit requested)

## 3. Notes / out of scope
- Known GPU limitation (inherited from train4, unchanged): `adaptive_N`/`total_*` are
  device-wide shared globals → per-particle adaptation races on GPU. CPU is unaffected.
- The branch's `-DNTOF_GPU_STATIC` GPU path looks incomplete (struct pointer members
  never allocated in that mode); left untouched.
- Stale xray `mcsetstate` signature vs main (`allow_backprop`, main db5b426d8) is a
  separate merge-main task, not part of this work.
- No GPU/OpenACC verification possible on this macOS box; flag for CI.

## 4. Progress log
- 2026-08-27 Plan written. Branch `TOF_train8` created from `origin/TOF_train_experiments`.
  Implementation delegated.
- 2026-08-27 Task 1 done (cogen.c.in): added `N_active` to CPU global block and
  OPENACC particle struct, replaced `alive_trains` define/undef with `N_active` in
  def/undef_trace_section, removed both `alive_trains` free blocks and both
  `alive_trains` global declarations, added per-ray CPU zeroing of `N_active` and
  `P_last_time_manipulation` in generated `mcgenstate()` (guarded
  `#ifdef TOF_TRAIN #ifndef OPENACC`). No deviations.
- 2026-08-27 Task 2 done (common/lib/share/mccode_main.c): removed
  `alive_trains=malloc(...)` from CPU init block.
- 2026-08-27 Task 3 done (mccode/nlib/share/mcstas-r.c): removed
  `mcneutron.alive_trains=malloc(...)`; added `mcneutron.N_active=0;` and
  `mcneutron.P_last_time_manipulation=0;` inside `#ifdef OPENACC`, outside the
  `#ifndef NTOF_GPU_STATIC` sub-guard.
- 2026-08-27 Task 4 done (common/lib/share/mccode-r.c): fprintf at ~4844 now passes
  `NTOF_GPU_STATIC, NTOF_GPU_STATIC`; help text at ~4541 now says "(default 100)".
- 2026-08-27 Task 5 started: full conda build launched in background
  (log: train8-test/build.log); Task 6 (prototype) being written in parallel.
- 2026-08-27 Task 5 done: cmake configure+build+install into
  /Users/user/miniforge3/envs/mcstas-dev succeeded (no errors in build.log;
  build dir was already configured from an earlier session, so this was an
  incremental rebuild, ~3 min). Smoke check: `mcrun --help` lists
  `--tof-trains=TOFTRAINS` ("Set number of neutron-subtrains to simulate").
- 2026-08-27 Task 6 done: created ODIN_TOF_train8/ with all files.
  TOF_trains-lib.h = train7 macros with `_particle->` stripped (bare names,
  swap-with-last compaction kept). ODIN_wfm.instr/CSPEC.instr: removed
  N_trains_par, allocated, USERVARS, #define N_trains, Origin-EXTEND malloc,
  FINALLY free. ESS_butterfly.comp: macros moved out of SHARE, source block in
  bare names + defensive `P_last_time_manipulation=0;` before fill loop.
  DiskChopper/MultiDiskChopper/TOF_monitor/TOFLambda_monitor/Monitor_nD:
  %include "TOF_trains-lib" added in SHARE (new SHARE section added where
  missing); Monitor_nD total_* bookkeeping in bare names, dead alive_trains
  comment block removed. ESS_butterfly-lib.c/.h copied unchanged;
  bi_spec_ellipse.comp copied from ODIN_MCPL_TOF_train4.
  DEVIATION: cogen's `%include "name"` (no extension) also requires a
  companion `name.c` (it embeds the .h AND queues the .c), so the include
  was written as `%include "TOF_trains-lib.h"` (explicit extension) in all
  5 components. Also DEVIATION: ODIN_wfm.instr uses Graphite_Diffuser, which
  is missing from stock resources AND from the train7 dir (a second train7
  build gap, like bi_spec_ellipse) — copied Graphite_Diffuser.comp from
  ODIN_MCPL_TOF_train4 into ODIN_TOF_train8.
- 2026-08-27 Task 7 done (all verification in
  /var/folders/.../T/opencode/train8-test/):
  * Build train8 ODIN: `mcstas ODIN_wfm.instr` + `cc -O2 -DTOF_TRAIN` OK
    (only pre-existing-style paren warnings).
  * PARITY (key check): train7 ODIN (scratch copy, N_trains_par=10, no
    -DTOF_TRAIN) vs train8 ODIN (--tof-trains=10), both -n 20000 --seed 42,
    all other params at defaults. time.dat, wavelength_tof.dat, image.dat,
    wavelength.dat are BIT-IDENTICAL (max/mean relative difference = 0.0/0.0
    in every column). Summaries identical too: PSD I=8.93048e8 N=168,
    tof bins 201. NOTE: train7's own dir does not build (missing
    bi_spec_ellipse.comp and Graphite_Diffuser.comp) — pre-existing gap;
    parity ran from a scratch copy with those two files added from train4.
  * Small-N sanity: --tof-trains=4, -n 5000 completes, sane non-zero output
    (PSD I=1.529e9, 19 hits; TOF range 0-2.4 ms).
  * Regression smoke: DTU/Vout_test WITHOUT -DTOF_TRAIN builds & runs
    (PSD_N=1000). Preprocessed non-TOF output contains 0 TOF symbols;
    with -DTOF_TRAIN the 2 N_active decls appear (both properly guarded).
  * CSPEC: builds with -DTOF_TRAIN; -n 1000 --tof-trains=20 completes
    (all-zero detectors, expected for 241 m at 1k rays); -n 20000
    --tof-trains=20 gives tof_I=1.766e8 (N=5) and det_NDmonitor_I=181.6
    (N=5) -> train mechanism produces real signal.
  * clangformat: all 10 .comp/.instr in ODIN_TOF_train8 formatted in place,
    .orig backups removed, re-check all "No changes necessary". After
    formatting: ODIN parity re-run still bit-identical; CSPEC still builds.
  * git status: 4 modified core files + new ODIN_TOF_train8/ dir; nothing
    committed (per instructions).
  * Params note: mcreadparams prompts per-parameter (EOF -> error), so all
    runs passed full param=value command lines (defaults from the .instr).
- 2026-08-28 FOLLOW-UP: user reported plain `mcrun ODIN_wfm.instr` fails in a fresh
  env (mcstas-dev-agent). Diagnosis: two compounded problems:
  (1) the fresh env's cogen had been built from a different (older) checkout, so the
  TOF_TRAIN globals were missing even with -DTOF_TRAIN; rebuilding the current branch
  into mcstas-dev-agent fixed that.
  (2) real prototype gap: plain mcrun never passes -DTOF_TRAIN. FIXED via the existing
  DEPENDENCY->CFLAGS mechanism (yacc `dependency` rule in instrument.y, cogen emits
  `* CFLAGS=...`, mcrun/mccode.py appends it): added `DEPENDENCY " -DTOF_TRAIN "` to
  all six train-using components (ESS_butterfly — matching the train4 precedent,
  DiskChopper, MultiDiskChopper, TOF_monitor, TOFLambda_monitor, Monitor_nD).
  Verified in mcstas-dev-agent: plain `mcrun -y ODIN_wfm.instr -n 100` compiles and
  runs; `--tof-trains=10 -n 5000` runs with sane detector output.
  Committed as e6cb92ee9 and pushed to the fork branch TOF_train8.
  NOTE on the (1) diagnosis, refined: the stale cogen lived in the *old* env
  (mcstas-dev, reinstalled 2026-08-28 11:21 by a build from a different/older
  checkout — not by this session). mcstas-dev-agent received the fresh build of
  this branch at 2026-08-28 12:40 and is the canonical env for this work;
  mcstas-dev was left untouched today (only used read-only to reproduce the
  failure) and still holds the stale cogen.
- 2026-08-28 Planning document committed and pushed with the branch.
  Branch state on the fork (mads-bertelsen-agentic/McCode, branch TOF_train8):
    e6cb92ee9 Add DEPENDENCY -DTOF_TRAIN to ODIN_TOF_train8 components
    057d02a9c Add ODIN_TOF_train8 prototype driven by the --tof-trains CLI plumbing
  (both on top of origin/TOF_train_experiments base c0fe02583).
  Remaining before a PR: merge-main reconciliation (stale xray mcsetstate
  signature vs allow_backprop, main db5b426d8), GPU/OpenACC build check via CI.
