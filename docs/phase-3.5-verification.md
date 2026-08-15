# Phase 3.5 Focused Repair Verification Report (Interim Invariant Fixes)

- **Phase**: Phase 3.5 Focused Invariant Repair
- **Date**: 2026-08-15
- **Evaluator**: Antigravity Quality Gate Reviewer & Verifier

---

## 1. Scope of Interim Invariant Fixes

| # | Invariant Area | Repair Performed | Notes / Residual Gaps Addressed in 3.6 |
|---|---|---|---|
| **1** | **State Transition CAS** | Added compare-and-set logic to `update_project_state` and state transitions logging. | *Residual*: `save_video_project` still contained a direct upsert path; fully locked down in Phase 3.6. |
| **2** | **SQLite Foreign Keys** | Enabled `PRAGMA foreign_keys = ON;` in SQLite connections and added orphan insertion rejection tests. | Verified with `PRAGMA foreign_keys == 1`. |
| **3** | **Privacy Typing** | Introduced `PrivacyStatus` enum (`PRIVATE`, `UNLISTED`, `PUBLIC`) defaulting to `PRIVATE`. | Data-level safeguard; Phase 6 gate enforces runtime human approval. |
| **4** | **Provenance Defaults** | `ResearchSource.license_type` default changed to `"UNKNOWN"`. | Removed fabricated `"Public Domain / CC"`. |
| **5** | **QA Status Default** | Added `QualityStatus.PENDING` as default for `QualityResult`. | Removed implicit `PASSED` default. |
| **6** | **Topic CPM Assumption** | Removed hardcoded `$10.0` CPM; `estimated_cpm` set to nullable (`None`). | Database schema updated accordingly. |
| **7** | **Runtime Dependencies** | Explicit dependencies added to `pyproject.toml`. | Verified clean editable install. |
| **8** | **Phase 2 Evidence Wording** | Clarified `agy` CLI reasoning (`REAL VERIFIED`) vs Python SDK symbol import (`VERIFIED`). | Python SDK reasoning execution noted as `NOT RUN / NOT VERIFIED`. |
| **9** | **Scoped Idempotency** | Converted idempotency table to composite PK `(scope, key)` and added `expires_at`. | Basic lease timeout added; multi-thread atomic CAS recovery completed in 3.6. |

---

## 2. Actual Historical RED Evidence Observed

```
=================================== ERRORS ====================================
__________________ ERROR collecting tests/api/test_routes.py __________________
ImportError while importing test module 'D:\Download\YoutubeAgents\tests\api\test_routes.py'.
Traceback:
tests\api\test_routes.py:12: in <module>
    from app.domain.enums import VideoLifecycleState, PlatformFormat, PublicationStatus, PrivacyStatus
E   ImportError: cannot import name 'PrivacyStatus' from 'app.domain.enums'
________________ ERROR collecting tests/db/test_persistence.py ________________
ImportError while importing test module 'D:\Download\YoutubeAgents\tests\db\test_persistence.py'.
Traceback:
tests\db\test_persistence.py:9: in <module>
    from app.domain.enums import VideoLifecycleState, PlatformFormat, PublicationStatus, PrivacyStatus
E   ImportError: cannot import name 'PrivacyStatus' from 'app.domain.enums'
________________ ERROR collecting tests/domain/test_schemas.py ________________
ImportError while importing test module 'D:\Download\YoutubeAgents\tests\domain\test_schemas.py'.
Traceback:
tests\domain\test_schemas.py:7: in <module>
    from app.domain.enums import (
E   ImportError: cannot import name 'PrivacyStatus' from 'app.domain.enums'
=========================== short test summary info ===========================
ERROR tests/api/test_routes.py
ERROR tests/db/test_persistence.py
ERROR tests/domain/test_schemas.py
!!!!!!!!!!!!!!!!!!! Interrupted: 3 errors during collection !!!!!!!!!!!!!!!!!!!
============================== 3 errors in 4.40s ==============================
```

---

## 3. Interim Gate Summary

- **Tests Passed**: `33/33 passed`
- **Follow-up Identified**: Phase 3.6 was required to eliminate `save_video_project` state bypass, enforce atomic compare-and-set for multi-thread lease recovery, add schema version migration, and update Claim confidence score semantics.
