# Completion plan: tested implementation and reviewable PR

Owner requested this plan and its execution on 2026-10-25. All six release phases
were previously authorized. This closes a software milestone; independent scientific
acceptance and measured latency retain their original gates.

## 1. Establish the baseline and collect outstanding runs

- Work on `release/all-phases`, initially based on `fc61947` and updated to main
  `1d56f83` during review; preserve the existing globe,
  frozen wire signatures and committed Eaton cache.
  Also incorporate Cody's subsequent `b408fe2` release-data/Pages update and audit
  its full archive before treating the development replay as complete.
- Collect the full engine test result and the running Eaton PF/open-loop/reinit
  replays and N=128 benchmark. Record partial coverage and contended measurements
  explicitly; a running process is not a passing result.
- Keep private hub credentials, transcripts, dispatch journals and raw caches out
  of Git. Use the locked Python environment outside iCloud for dependable tests.

## 2. Review and repair the implementation

- Audit report sessions, service credentials, provider opt-in, upload bounds,
  report idempotence and uncertain-call reconciliation.
- Check report acceptance/rejection publication, causal next-cycle injection,
  parent immutability, checkpoint restoration and backward seeks across forks.
- Check partial cache labels, selected-scenario asset serving and raw-member export
  with matching weights and complete consumed-evidence audit.
- Add regression tests for any concrete defect found. Keep numerical defaults
  unchanged; do not promote a candidate merely because a software test passes.

## 3. Complete the software regression and fixture demonstration

- Run all engine tests, source/import/diagnostic tests, release audit regressions,
  service/audio tests, canonical dashboard tests, TypeScript checks, lint and build.
- Run the fixture demo through the existing CLI and independently decode its
  binary output. Preserve scientific and performance warnings on real-data views.
- Inspect `/dashboard` and `/p` locally; verify consent, live hub connection and a
  tap report with providers disabled. Retain screenshots and browser CI coverage.
- Run native/hub checks relevant to changed behavior. Report configured skips and
  any environment-specific limitation separately from failures.

## 4. Record scientific and performance dispositions

- Keep the downloaded later NIFC polygon out of overnight truth. Follow
  `PERIMETER_SOURCE_REVIEW.md` for candidate acquisition and the limited fallback.
- Record synthetic calibration results, including failed wind/ROS/report targets.
- Record the measured target-size timing and its resource contention. No target
  revision or speed claim is authorized by this plan.
- List actionable remaining work: original FireGuard GIS/coverage/lineage, frozen
  independent cases, calibration experiments and numerically equivalent native
  solver optimization. These remain visible release gates rather than hidden TODOs.

## 5. Publish for review

- Fetch main, reconcile any new commits, inspect the final diff and secret exposure,
  and update `RELEASE_READINESS.md`, the evidence ledger and `docs/process.md`.
- Commit and push the implementation branch; open a PR into main and attach it to
  this chat. Include exact local test results, incomplete gates and reproduction
  commands. Observe CI and fix implementation failures.
- Use a draft PR if required integration checks or release evidence remain open.
  Do not merge an incomplete or failing change. External calls and deployment
  still require actual provider setup and the specifically authorized recipient.

Execution results are recorded in `RELEASE_COMPLETION_RESULTS.md`; this plan does
not itself declare a gate passed.
