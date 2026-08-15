# Definition of Done (DoD)

This document establishes the non-negotiable criteria for completing tasks, phases, and releases in the **YouTube Autopilot** repository.

---

## 1. Task-Level Definition of Done

A task is complete only when all of the following conditions are satisfied:

1. **Specification & Plan Compliance**: The delivered changes strictly align with the approved task specification.
2. **Test-Driven Verification**:
   - For behavioral additions or bug fixes, a focused test was written/updated and observed failing (**RED**).
   - The minimal implementation was written and observed passing (**GREEN**).
   - All tests in the test suite pass with exit code 0.
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

## 2. Phase-Level Definition of Done

A phase is officially complete and allows transitioning to the next phase ONLY when:

- [ ] **Planner / Review Evidence**: Documented stable diff inspection and verification logs.
- [ ] **Tests Executed**: Unit and integration suites run cleanly with exact commands recorded.
- [ ] **Reviewer Result**: Architecture, copyright safeguards, error handling, and state contracts verified.
- [ ] **Verifier Verdict**: Explicitly marked `PASS`.
- [ ] **Remaining Blockers**: Documented as `NONE` (or explicit deferrals approved by human).

> [!CAUTION]
> **Strict Gate Invariant**: If the verifier verdict is `FAIL` or if verification has not been performed, work on the next phase **CANNOT** proceed.

---

## 3. Phase 0 Specific Done Criteria

Phase 0 (Workspace Bootstrap & Engineering Contract) is done when:

- [x] Clean Python project skeleton initialized (`app/`, `config/`, `docs/`, `tests/`, `scripts/`, `data/`, `output/`, `references/`).
- [x] Project imports cleanly via Python (`import app`, `import config`).
- [x] Pytest runs cleanly and exits with code 0 (`pytest`).
- [x] Git repository initialized (`git init`).
- [x] Engineering rules frozen in `AGENTS.md`.
- [x] Project contract created in `docs/project-contract.md`.
- [x] Definition of Done documented in `docs/definition-of-done.md`.
- [x] No fake AI code, no premature feature logic, no external AI API wrappers.
- [x] Execution stopped immediately after Phase 0.
