# Definition of Done (DoD)

This document establishes the non-negotiable criteria for completing tasks, phases, and releases in the **YouTube Autopilot** repository.

---

## 1. Task-Level Definition of Done

A task is complete only when all of the following conditions are satisfied:

1. **Specification & Plan Compliance**: The delivered changes strictly align with the approved task specification.
2. **Test-Driven Verification**:
   - For behavioral additions or bug fixes, a focused test was written/updated and observed failing (**RED**).
   - The minimal implementation was written and observed passing (**GREEN**).
   - All tests in the test suite pass with exit code 0 and zero unresolved warnings.
3. **No Unrequested Complexity (Ponytail / YAGNI)**:
   - No speculative abstractions, dead code, or redundant layers.
   - Standard library or existing helpers used where possible.
4. **Code Quality & Linting**:
   - Type annotations on all public functions, classes, and methods.
   - Clean formatting, zero syntax errors, and zero unresolved warnings.
5. **Clear Execution State**:
   - Any runtime output or test double is explicitly tagged (`REAL`, `TEST`, `DRY_RUN`, `BLOCKED`, `FAILED`).
   - No silent error swallowing or fake success placeholders.

---

## 2. Phase-Level Definition of Done & Quality Gates

A phase is officially complete and allows transitioning to the next phase ONLY when:

- [ ] **Planner / Review Evidence**: Documented plan compliance, repository audit (if applicable), and stable diff inspection.
- [ ] **Tests Executed**: Unit and integration suites run cleanly with exact commands recorded.
- [ ] **Reviewer Result**: Architecture, copyright safeguards, error handling, and state contracts reviewed with explicit findings.
- [ ] **Verifier Verdict**: Formally evaluated and committed in `docs/phase-<N>-verification.md` with verdict `PASS`.
- [ ] **Remaining Blockers**: Documented as `NONE` (or explicit deferrals approved by human).

> [!CAUTION]
> **Strict Gate Invariant**: If the verifier verdict is `FAIL`, `PARTIAL`, or if verification has not been committed to the repository, work on the next phase **CANNOT** proceed.

---

## 3. Phase-Specific Verification Rules

### Phase 0: Workspace Bootstrap Exception
- **Exception Rule**: Phase 0 establishes the initial workspace skeleton, contracts, and baseline configuration. Because no business logic or runtime features exist, smoke verification (`tests/test_smoke.py` passing with exit code 0) is sufficient evidence for initial workspace bootstrap.

### Phase $\ge$ 1: Mandatory Committed Verification Artifact
- For every phase from Phase 1 onward, a dedicated verification document `docs/phase-<N>-verification.md` MUST be committed to version control prior to marking the phase complete.
- The verification document must explicitly record:
  1. `Phase`: Phase number and title
  2. `Planner`: Plan status and scope coverage
  3. `Tests Executed`: Exact commands and output pass/fail summary
  4. `Reviewer`: Detailed inspection results across security, data contracts, and anti-patterns
  5. `Verifier Verdict`: `PASS` | `PARTIAL` | `FAIL`
  6. `Known Limitations`: Explicit list of unexecuted live paths, mock boundaries, or static constraints
  7. `Remaining Blockers`: `NONE` or documented blockers
