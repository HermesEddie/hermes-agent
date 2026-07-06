# Three-Way Reconcile And Test Repair Plan

Goal: reconcile Hermes production-only code, local uncommitted main changes, and Git `origin/main` without mixing incident hotfixes with feature divergence, then repair the current failing test suite.

Scope:
- Base branch: `origin/main` at `d867e80`.
- Working branch: `codex/hermes-three-way-reconcile-tests`.
- Production source: `hermes-agent-production:/opt/hermes`.
- Local dirty source: `/Users/mac/Desktop/hermes-agent` main worktree.
- Primary files expected from prior evidence: `gateway/platforms/api_server.py`, `tests/gateway/test_api_server.py`, plus any files implicated by failing tests.

Out of scope:
- Production deployment or container hot patch.
- Rewriting unrelated Hermes features.
- Pulling into the dirty local main worktree.
- Syncing with the official `NousResearch/hermes-agent` upstream unless the
  project owner explicitly reopens that track.
- Fixing generic Hermes upstream test failures unrelated to Tower's API server
  additions or production/local Tower deltas.

Risk:
- Production contains runtime-only changes that may not belong in Git.
- Local dirty changes mix chat API features with tests and may depend on files not changed locally.
- Full test red lights may reflect older feature branches that were partially merged or stale tests.

Validation strategy:
- Capture and classify production/local/main diffs before applying code.
- Prefer small commits by category: runtime hotfix backfill, feature reconciliation, test repair.
- Run targeted tests for Tower/API server additions after each category.
- Do not use full Hermes upstream test health as acceptance for this task; that
  would pull unrelated official project bugs into Tower's branch.
- Validate against this fork's `origin/main` behavior and Tower deltas. Do not
  require official upstream parity for this task.

Checklist:
- [x] Capture production source fingerprint and relevant files.
- [x] Classify production-only, local-only, and Git-main-only changes.
- [x] Decide inclusion category for each diff hunk.
- [x] Add or repair tests that define intended behavior before foundation edits.
- [x] Apply reconciled code changes to the isolated worktree.
- [x] Repair initial current-main test failures with root-cause evidence.
- [x] Run targeted tests for initial changed modules.
- [x] Record project rule: official upstream sync is opt-in only.
- [x] Run Tower/API-server targeted validation.
- [x] Record validation results and skipped checks.
- [x] Add adversarial regression tests for Tower dashboard async callback, stateless chat sessions, and empty inventory report input.
- [x] Fix only Tower/API additions implicated by those regressions.
- [x] Re-run scoped Tower/API validation and record results.

Checkpoint:
- 2026-07-03: Worktree created from `origin/main`; Tower bridge targeted tests passed: `15 passed`.
- 2026-07-03: Production hash compare found only 3 differing Python files: `gateway/platforms/api_server.py`, `tests/gateway/test_api_server_tower_bridge.py`, `tests/tools/test_tower_tools.py`.
- 2026-07-03: Local dirty compare found only 2 files: `gateway/platforms/api_server.py`, `tests/gateway/test_api_server.py`.
- 2026-07-03: Foundation red lights fixed; targeted API tests passed `43 passed`; logging/approval/reload_env/stub tests passed `62 passed`; py_compile passed.
- 2026-07-03: Tower reconciliation complete; production-only inventory health route/job absorbed; local-only sales-target dashboard route/mode absorbed; Tower bridge tests passed `17 passed`.
- 2026-07-03: Wider related test set passed `208 passed`.
- 2026-07-05: Official upstream investigation stopped by owner direction.
  Removed the half-created upstream worktree/branch and documented that Hermes
  upstream sync is explicit opt-in only.
- 2026-07-05: Scope corrected by owner: stop repairing generic Hermes official
  project bugs. Reverted unrelated Matrix/Feishu/WSL/Tirith/run_agent/file-tool
  fixes from this branch; remaining diff is Tower/API server additions,
  `AGENTS.md`, and this plan.
- 2026-07-05: Validation completed for scoped Tower/API additions:
  `python -m py_compile gateway/platforms/api_server.py`,
  `python -m pytest tests/gateway/test_api_server.py tests/gateway/test_api_server_tower_bridge.py -q --tb=short`
  (`126 passed`), `python -m pytest tests/tools/test_tower_tools.py -q --tb=short`
  (`3 passed`), and `git diff --check`. Full Hermes upstream/general test
  suite intentionally skipped because generic official-project failures are
  outside this task's acceptance boundary.
- 2026-07-05: Adversarial review scope accepted: fix sales-target dashboard
  callback async contract, chat-completions stateless session isolation, and
  inventory-health dashboard empty `report_input` validation only.
  CodeGraph initialized in the isolated worktree; `codegraph context` was broad,
  so impact checks were used for `_handle_tower_dashboard_analysis_body`,
  `_handle_chat_completions`, and `_handle_tower_inventory_health_dashboard_analysis`.
- 2026-07-05: Red tests confirmed current regressions:
  dashboard callback route returned `200` instead of async `202`, inventory
  dashboard rejected empty `report_input` with `400`, and identical stateless
  chat requests reused the same derived session id.
- 2026-07-05: Fixed scoped adversarial findings. Dashboard analysis keeps the
  synchronous preview path without `callback_url`, but Tower callback requests
  now return `202` and run in a background job; callback requests require
  `tenant_id` before scheduling. Chat completions now create a fresh `api-*`
  session id when `X-Hermes-Session-Id` is absent, while explicit session
  headers still load persisted history. Inventory-health dashboard validation
  now allows `report_input={}` while still requiring the key to be present.
- 2026-07-05: Scoped validation after fixes passed:
  `python -m py_compile gateway/platforms/api_server.py`,
  `python -m pytest tests/gateway/test_api_server.py tests/gateway/test_api_server_tower_bridge.py -q --tb=short`
  (`125 passed`), `python -m pytest tests/tools/test_tower_tools.py -q --tb=short`
  (`3 passed`), and `git diff --check`. Full Hermes upstream/general tests
  remain intentionally skipped per owner scope: do not repair generic official
  project bugs in this branch.
- 2026-07-05: Pre-PR production check completed against server
  `175.178.10.185`. Running container `hermes-agent-production` uses
  `/opt/hermes`; runtime file comparison found only
  `gateway/platforms/api_server.py` differs from this branch, and the
  difference is this branch being ahead with the reconciled Tower/API changes.
  Production host source under `/srv/hermes-agent/src/hermes-agent` is dirty
  and differs from the running container, so it was treated as stale source
  state rather than deploy truth. No additional production-only hotfix was found
  that must be merged before PR.
