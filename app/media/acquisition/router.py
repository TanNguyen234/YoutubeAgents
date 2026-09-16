"""Visual Acquisition Router orchestrating evidence sourcing, rendering, and candidate selection."""

import logging
from pathlib import Path
from typing import Any, List, Optional, Tuple

from app.domain.models import FactCheckReport, ResearchDossier
from app.media.acquisition.candidate_ranker import CandidateRanker
from app.media.acquisition.models import (
    BrowserAction,
    BrowserActionType,
    VisualAcquisitionRequest,
    VisualAcquisitionResult,
    VisualAssetCandidate,
    VisualAssetProvenance,
    VisualSourceType,
)
from app.media.acquisition.stock import PexelsStockProvider, StockMediaProvider
from app.media.acquisition.web_capture import WebCaptureService, is_within_canonical_url_scope
from app.media.director.models import (
    BeatPurpose,
    NarrativeBeat,
    ShotSpec,
    VisualIntent,
    VisualModality,
)
from app.media.semantic_qa.judge import VisualCandidateJudge
from app.media.semantic_qa.models import (
    VisualSemanticQAError,
    VisualSemanticQAMode,
    VisualSemanticVerdict,
)

logger = logging.getLogger(__name__)


def resolve_canonical_research_source(
    binding: Optional[Any] = None,
    source_refs: Optional[List[str]] = None,
    dossier: Optional[ResearchDossier] = None,
) -> Optional[Any]:
    """Resolve an authorized canonical ResearchSource from ResearchDossier.

    Strict rules:
    1. If EvidenceBinding.source_ref is present:
       - It MUST resolve to ResearchDossier.sources by exact ResearchSource.id or source_id.
       - If it does not resolve: FAIL (return None). Do NOT fall through to shot.source_refs or binding.source_url!
       - If it resolves to source A and binding.source_url is also present:
         binding.source_url MUST be within CANONICAL_URL_SCOPE(A).
         If not: FAIL (return None). Do NOT silently replace inconsistent source_url with A.url.
    2. If EvidenceBinding.source_ref is absent:
       - Fall back to shot / request source_refs exact id or source_id match.
       - If resolved, and binding.source_url is present, verify consistency with canonical scope.
    3. If source_ref and source_refs are absent:
       - Only allow binding.source_url resolution when URL matches an existing canonical
         ResearchSource inside ResearchDossier (exact match or within CANONICAL_URL_SCOPE).
       - Never creates authority; only matches an already-existing canonical source.

    Returns None if dossier is missing, empty, or no source matches / consistency fails.
    """
    if not dossier or not getattr(dossier, "sources", None):
        return None

    sources = dossier.sources

    def _matches_id(src: Any, ref: str) -> bool:
        if not ref:
            return False
        s_id = getattr(src, "id", None)
        if s_id is not None and str(s_id) == str(ref):
            return True
        s_src_id = getattr(src, "source_id", None)
        if s_src_id is not None and str(s_src_id) == str(ref):
            return True
        return False

    has_explicit_source_ref = bool(
        binding and getattr(binding, "source_ref", None) and str(binding.source_ref).strip()
    )

    # 1. Strict source_ref semantics: if present, MUST resolve to a source in dossier
    if has_explicit_source_ref:
        b_ref = str(binding.source_ref).strip()
        matched_source = None
        for s in sources:
            if _matches_id(s, b_ref):
                matched_source = s
                break

        if not matched_source:
            # Do NOT fall through to shot.source_refs or binding.source_url!
            return None

        # Source_ref + source_url consistency check:
        # If source_ref resolves to ResearchSource A and binding.source_url is also present:
        # source_url MUST be within CANONICAL_URL_SCOPE(A).
        b_url = getattr(binding, "source_url", None)
        if b_url and str(b_url).strip():
            s_url = getattr(matched_source, "url", None) or getattr(matched_source, "final_url", None)
            if not s_url or not is_within_canonical_url_scope(str(b_url).strip(), str(s_url).strip()):
                return None

        return matched_source

    # 2. source_refs fallback: ONLY when EvidenceBinding.source_ref is absent
    if source_refs:
        for s_ref in source_refs:
            for s in sources:
                if _matches_id(s, s_ref):
                    b_url = getattr(binding, "source_url", None) if binding else None
                    if b_url and str(b_url).strip():
                        s_url = getattr(s, "url", None) or getattr(s, "final_url", None)
                        if not s_url or not is_within_canonical_url_scope(str(b_url).strip(), str(s_url).strip()):
                            continue
                    return s

    # 3. URL fallback: ONLY when source_ref is absent AND URL matches existing canonical ResearchSource
    if binding and getattr(binding, "source_url", None):
        b_url = (binding.source_url or "").strip()
        if b_url:
            for s in sources:
                s_url = (getattr(s, "url", None) or getattr(s, "final_url", None) or "").strip()
                if s_url and is_within_canonical_url_scope(b_url, s_url):
                    return s

    return None


class VisualAcquisitionRouter:
    """Routes visual acquisition requests to appropriate evidence providers, renderers, or generative backends."""

    def __init__(
        self,
        web_capture: Optional[WebCaptureService] = None,
        stock_provider: Optional[StockMediaProvider] = None,
        gflow_provider: Optional[any] = None,
        diagram_renderer: Optional[any] = None,
        chart_renderer: Optional[any] = None,
        motion_renderer: Optional[any] = None,
        visual_factory: Optional[any] = None,
        ranker: Optional[CandidateRanker] = None,
        semantic_judge: Optional[VisualCandidateJudge] = None,
        qa_mode: VisualSemanticQAMode = VisualSemanticQAMode.ADVISORY,
    ):
        self.web_capture = web_capture or WebCaptureService()
        self.stock_provider = stock_provider or PexelsStockProvider()
        self.gflow_provider = gflow_provider
        self.diagram_renderer = diagram_renderer
        self.chart_renderer = chart_renderer
        self.motion_renderer = motion_renderer
        self.visual_factory = visual_factory
        self.ranker = ranker or CandidateRanker()
        self.semantic_judge = semantic_judge
        self.qa_mode = qa_mode

    def build_acquisition_request(
        self,
        shot: ShotSpec,
        project_id: str,
        dossier: Optional[ResearchDossier] = None,
        fact_report: Optional[FactCheckReport] = None,
    ) -> VisualAcquisitionRequest:
        """Translate a Director ShotSpec into a structured VisualAcquisitionRequest."""
        # Derive preferred source types from modality
        preferred: List[VisualSourceType] = []
        mod = shot.visual_modality

        if mod in (VisualModality.DOCUMENT_EVIDENCE, VisualModality.SCREENSHOT):
            preferred = [VisualSourceType.RESEARCH_SOURCE, VisualSourceType.DOCUMENT, VisualSourceType.WEB_PAGE]
        elif mod == VisualModality.SCREEN_CAPTURE:
            preferred = [VisualSourceType.LOCAL_WEB_APP, VisualSourceType.WEB_PAGE]
        elif mod in (VisualModality.CODE_ANIMATION, VisualModality.UI_SIMULATION):
            preferred = [VisualSourceType.CODE_OUTPUT, VisualSourceType.RENDERED]
        elif mod in (VisualModality.DATA_VISUALIZATION, VisualModality.DIAGRAM, VisualModality.COMPARISON):
            preferred = [VisualSourceType.RENDERED]
        elif mod == VisualModality.STOCK_VIDEO:
            preferred = [VisualSourceType.STOCK_MEDIA]
        elif mod in (VisualModality.GENERATED_VIDEO, VisualModality.GENERATED_IMAGE):
            preferred = [VisualSourceType.GENERATED]
        else:
            preferred = [VisualSourceType.RENDERED, VisualSourceType.FALLBACK_CARD]

        # Extract target url:
        # For DOCUMENT_EVIDENCE and SCREENSHOT: strictly enforce server-side binding from evidence_binding or dossier!
        # LLM screen_instruction text MUST NOT become a trusted evidence URL!
        target_url = None
        if mod in (VisualModality.DOCUMENT_EVIDENCE, VisualModality.SCREENSHOT):
            canonical_source = resolve_canonical_research_source(
                binding=shot.evidence_binding,
                source_refs=shot.source_refs,
                dossier=dossier,
            )
            if canonical_source:
                canonical_url = getattr(canonical_source, "url", None) or getattr(canonical_source, "final_url", None)
                adv_url = shot.evidence_binding.source_url if shot.evidence_binding else None
                if adv_url:
                    if is_within_canonical_url_scope(adv_url, canonical_url):
                        target_url = adv_url
                    else:
                        target_url = None
                else:
                    target_url = canonical_url
            else:
                target_url = None
        elif mod == VisualModality.SCREEN_CAPTURE:
            # Separate LOCAL_WEB_APP (localhost/127.0.0.1) from REMOTE SCREEN_CAPTURE
            candidate_url = None
            if shot.evidence_binding and shot.evidence_binding.source_url:
                candidate_url = shot.evidence_binding.source_url
            elif shot.screen_instruction and ("http://" in shot.screen_instruction or "https://" in shot.screen_instruction):
                import re
                m = re.search(r"https?://[^\s]+", shot.screen_instruction)
                if m:
                    candidate_url = m.group(0).rstrip(".,;\"'")

            if candidate_url:
                if "localhost" in candidate_url or "127.0.0.1" in candidate_url or "::1" in candidate_url:
                    target_url = candidate_url
                elif dossier and getattr(dossier, "sources", None):
                    # Remote screen capture must resolve to trusted ResearchSource!
                    for s in dossier.sources:
                        s_url = getattr(s, "url", None)
                        if s_url and is_within_canonical_url_scope(candidate_url, s_url):
                            target_url = candidate_url
                            break

        return VisualAcquisitionRequest(
            project_id=project_id,
            shot_id=shot.shot_id,
            modality=shot.visual_modality,
            visual_intent=VisualIntent.SHOW_EVIDENCE if mod == VisualModality.DOCUMENT_EVIDENCE else VisualIntent.SHOW_MECHANISM,
            subject=shot.subject or shot.narration_segment[:40],
            action=shot.action,
            environment=shot.environment,
            query=shot.asset_query,
            evidence_binding=shot.evidence_binding,
            source_refs=shot.source_refs,
            preferred_source_types=preferred,
            target_width=1080,
            target_height=1920,
            duration_seconds=shot.duration_seconds,
            target_url=target_url,
        )

    def acquire_visual(
        self,
        request: VisualAcquisitionRequest,
        output_dir: Path,
        script_title: str = "Video Topic",
        channel_name: str = "Tech Channel",
        dossier: Optional[ResearchDossier] = None,
        fact_report: Optional[FactCheckReport] = None,
        shot: Optional[ShotSpec] = None,
        qa_mode: Optional[VisualSemanticQAMode] = None,
    ) -> VisualAcquisitionResult:
        """Execute source-aware visual acquisition, candidate ranking, and fallback resolution."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        shot_id = request.shot_id
        modality = request.modality
        candidates: List[VisualAssetCandidate] = []
        failures: List[str] = []

        # -------------------------------------------------------------
        # Branch 1: DOCUMENT_EVIDENCE / SCREENSHOT
        # -------------------------------------------------------------
        if modality in (VisualModality.DOCUMENT_EVIDENCE, VisualModality.SCREENSHOT):
            binding = request.evidence_binding
            canonical_source = resolve_canonical_research_source(
                binding=binding,
                source_refs=request.source_refs,
                dossier=dossier,
            )
            target_url = None
            if not canonical_source:
                failures.append(
                    "UNTRUSTED_VISUAL_SOURCE: UNTRUSTED_SOURCE_URL: Evidence modality requires a canonical ResearchDossier source, "
                    "but no verified source could be resolved"
                )
            else:
                canonical_url = getattr(canonical_source, "url", None) or getattr(canonical_source, "final_url", None)
                if binding and getattr(binding, "source_url", None):
                    if not is_within_canonical_url_scope(binding.source_url, canonical_url):
                        failures.append(
                            f"UNTRUSTED_VISUAL_SOURCE: UNTRUSTED_SOURCE_URL: EvidenceBinding.source_url '{binding.source_url}' "
                            f"is outside CANONICAL_URL_SCOPE for resolved canonical source '{canonical_url}'"
                        )
                candidate_target = request.target_url or (binding.source_url if binding else None) or canonical_url
                if not is_within_canonical_url_scope(candidate_target, canonical_url):
                    failures.append(
                        f"UNTRUSTED_VISUAL_SOURCE: UNTRUSTED_SOURCE_URL: Target URL '{candidate_target}' "
                        f"is outside CANONICAL_URL_SCOPE for '{canonical_url}'"
                    )
                elif not failures:
                    target_url = candidate_target

            trusted_urls = [s.url for s in (dossier.sources if dossier else []) if getattr(s, "url", None)]
            if canonical_source and getattr(canonical_source, "url", None) and canonical_source.url not in trusted_urls:
                trusted_urls.append(canonical_source.url)

            if target_url and binding:
                target_path = output_dir / f"{shot_id}_evidence_capture.png"

                cand, errs = self.web_capture.capture_evidence(
                    url=target_url,
                    output_path=target_path,
                    source_excerpt=binding.source_excerpt,
                    source_title=binding.source_title,
                    trusted_urls=trusted_urls if trusted_urls else None,
                )
                if cand:
                    cand.evidence_claim_ids = [binding.claim_id] if binding.claim_id else []
                    cand.source_ref = getattr(canonical_source, "id", None) or getattr(canonical_source, "source_id", None) or binding.source_ref
                    candidates.append(cand)
                else:
                    failures.extend(errs)

            # Fallback for DOCUMENT_EVIDENCE:
            # If evidence capture failed or binding ungrounded, DO NOT fabricate fake screenshot!
            # Route gracefully to grounded Diagram or Motion Graphics
            if not candidates and self.diagram_renderer:
                fallback_path = output_dir / f"{shot_id}_evidence_diagram_fallback.png"
                try:
                    p, h = self.diagram_renderer.render_from_instruction(
                        instruction=request.subject or script_title,
                        output_path=fallback_path,
                        title=request.subject or script_title,
                    )
                    candidates.append(
                        VisualAssetCandidate(
                            candidate_id=f"fallback_diagram_{shot_id}",
                            source_type=VisualSourceType.RENDERED,
                            file_path=str(p),
                            content_sha256=h,
                            width=1080,
                            height=1920,
                            acquisition_method="diagram_renderer_evidence_fallback",
                            is_synthetic=False,
                        )
                    )
                except Exception as e:
                    failures.append(f"DIAGRAM_FALLBACK_FAILED: {e}")

        # -------------------------------------------------------------
        # Branch 2: SCREEN_CAPTURE / LOCAL_WEB_APP
        # -------------------------------------------------------------
        elif modality == VisualModality.SCREEN_CAPTURE:
            target_url = request.target_url
            if target_url:
                target_path = output_dir / f"{shot_id}_screencap.png"
                if "localhost" in target_url or "127.0.0.1" in target_url or "::1" in target_url:
                    cand, errs = self.web_capture.capture_local_ui(
                        url=target_url,
                        output_path=target_path,
                        interaction_plan=request.interaction_plan,
                    )
                else:
                    # Remote screen capture must resolve to trusted ResearchSource!
                    if not dossier or not getattr(dossier, "sources", None):
                        failures.append(
                            f"UNTRUSTED_VISUAL_SOURCE: UNTRUSTED_SOURCE_URL: Remote screen capture requires a verified ResearchDossier source, "
                            f"got URL '{target_url}' with no dossier"
                        )
                        cand, errs = None, []
                    else:
                        trusted_sources = [s for s in dossier.sources if getattr(s, "url", None)]
                        matching_source = next(
                            (s for s in trusted_sources if is_within_canonical_url_scope(target_url, s.url)),
                            None
                        )
                        if not matching_source:
                            failures.append(
                                f"UNTRUSTED_VISUAL_SOURCE: UNTRUSTED_SOURCE_URL: Remote screen capture URL '{target_url}' is outside ResearchDossier verified source scope"
                            )
                            cand, errs = None, []
                        else:
                            trusted_urls = [s.url for s in trusted_sources if s.url]
                            cand, errs = self.web_capture.capture_evidence(
                                url=target_url,
                                output_path=target_path,
                                trusted_urls=trusted_urls,
                            )
                if cand:
                    candidates.append(cand)
                else:
                    failures.extend(errs)

            if not candidates and self.diagram_renderer:
                fallback_path = output_dir / f"{shot_id}_screencap_fallback.png"
                try:
                    p, h = self.diagram_renderer.render_from_instruction(
                        instruction=request.subject or script_title,
                        output_path=fallback_path,
                        title=request.subject or script_title,
                    )
                    candidates.append(
                        VisualAssetCandidate(
                            candidate_id=f"fallback_screencap_diag_{shot_id}",
                            source_type=VisualSourceType.RENDERED,
                            file_path=str(p),
                            content_sha256=h,
                            width=1080,
                            height=1920,
                            acquisition_method="diagram_renderer_screencap_fallback",
                            is_synthetic=False,
                        )
                    )
                except Exception as e:
                    failures.append(f"SCREENCAP_FALLBACK_FAILED: {e}")

        # -------------------------------------------------------------
        # Branch 3: STOCK_VIDEO
        # -------------------------------------------------------------
        elif modality == VisualModality.STOCK_VIDEO:
            if self.stock_provider and self.stock_provider.is_available():
                results = self.stock_provider.search_video(
                    subject=request.subject,
                    action=request.action,
                    environment=request.environment,
                    min_duration_seconds=request.duration_seconds or 2.0,
                )
                if results:
                    target_vid = output_dir / f"{shot_id}_stock.mp4"
                    cand = self.stock_provider.download_candidate(results[0], target_vid)
                    if cand:
                        candidates.append(cand)
                    else:
                        failures.append("STOCK_DOWNLOAD_FAILED: Could not download stock asset")
                else:
                    failures.append("STOCK_NO_RESULTS: No stock video matched semantic query")
            else:
                failures.append("STOCK_UNAVAILABLE: Stock provider missing API credentials")

            # Stock failure fallback: Diagram or Motion Graphics
            if not candidates and self.diagram_renderer:
                fallback_path = output_dir / f"{shot_id}_stock_diagram_fallback.png"
                try:
                    p, h = self.diagram_renderer.render_from_instruction(
                        instruction=request.subject or script_title,
                        output_path=fallback_path,
                        title=request.subject or script_title,
                    )
                    candidates.append(
                        VisualAssetCandidate(
                            candidate_id=f"fallback_stock_diag_{shot_id}",
                            source_type=VisualSourceType.RENDERED,
                            file_path=str(p),
                            content_sha256=h,
                            width=1080,
                            height=1920,
                            acquisition_method="diagram_renderer_stock_fallback",
                            is_synthetic=False,
                        )
                    )
                except Exception as e:
                    failures.append(f"STOCK_FALLBACK_FAILED: {e}")

        # -------------------------------------------------------------
        # Branch 4: Fallback of Last Resort (STATIC_CARD)
        # -------------------------------------------------------------
        if not candidates and self.visual_factory:
            card_path = output_dir / f"{shot_id}_last_resort_card.png"
            try:
                p, h = self.visual_factory.render_scene_card(
                    scene_index=0,
                    channel_name=channel_name,
                    topic_title=script_title,
                    scene_headline=request.subject,
                    output_path=card_path,
                )
                candidates.append(
                    VisualAssetCandidate(
                        candidate_id=f"static_card_{shot_id}",
                        source_type=VisualSourceType.FALLBACK_CARD,
                        file_path=str(p),
                        content_sha256=h,
                        width=1080,
                        height=1920,
                        acquisition_method="visual_factory_static_card",
                        is_synthetic=False,
                    )
                )
            except Exception as e:
                failures.append(f"STATIC_CARD_FAILED: {e}")

        # Rank candidates deterministically
        selected_id = None
        actual_modality = None
        semantic_audit: Dict[str, Any] = {}

        if candidates:
            ranked = self.ranker.rank_candidates(candidates, request)
            det_scores = {c.candidate_id: score for c, score in ranked}
            active_qa_mode = qa_mode or self.qa_mode

            if active_qa_mode == VisualSemanticQAMode.DISABLED or not self.semantic_judge:
                if ranked:
                    winner, winning_score = ranked[0]
                    selected_id = winner.candidate_id
                    self.ranker.record_selection(winner, request.modality)
            else:
                try:
                    judging_shot = shot
                    if not judging_shot:
                        judging_shot = ShotSpec(
                            shot_id=request.shot_id,
                            beat_id=f"beat_{request.shot_id}",
                            duration_seconds=request.duration_seconds or 3.0,
                            visual_modality=request.modality,
                            visual_intent=request.visual_intent,
                            subject=request.subject,
                            action=request.action,
                            environment=request.environment,
                            narration_segment=request.subject or "",
                            evidence_binding=request.evidence_binding,
                        )

                    judge_res = self.semantic_judge.judge_candidates(
                        candidates=[c for c, _ in ranked],
                        request=request,
                        shot=judging_shot,
                        deterministic_scores=det_scores,
                    )
                    semantic_audit = judge_res.audit_metadata

                    if judge_res.selected_candidate_id:
                        selected_id = judge_res.selected_candidate_id
                        winner = judge_res.winning_candidate
                        self.ranker.record_selection(winner, request.modality)
                    else:
                        failures.extend(judge_res.failure_reasons)
                        if active_qa_mode == VisualSemanticQAMode.REQUIRED:
                            selected_id = None
                        elif active_qa_mode == VisualSemanticQAMode.ADVISORY:
                            logger.warning(
                                "SEMANTIC_QA_UNAVAILABLE: All candidates rejected by semantic QA, advisory fallback to deterministic winner"
                            )
                            if ranked:
                                winner, winning_score = ranked[0]
                                selected_id = winner.candidate_id
                                self.ranker.record_selection(winner, request.modality)
                except Exception as e:
                    if active_qa_mode == VisualSemanticQAMode.REQUIRED:
                        failures.append(f"SEMANTIC_QA_FAILED: {e}")
                        raise VisualSemanticQAError(f"Visual Semantic QA failed in REQUIRED mode: {e}") from e
                    else:
                        logger.warning("SEMANTIC_QA_UNAVAILABLE: %s", e)
                        failures.append(f"SEMANTIC_QA_UNAVAILABLE: {e}")
                        if ranked:
                            winner, winning_score = ranked[0]
                            selected_id = winner.candidate_id
                            self.ranker.record_selection(winner, request.modality)

            if selected_id:
                winner = None
                for c in candidates:
                    if c.candidate_id == selected_id:
                        winner = c
                        break
                if winner:
                    if winner.source_type in (VisualSourceType.RESEARCH_SOURCE, VisualSourceType.DOCUMENT, VisualSourceType.WEB_PAGE):
                        actual_modality = VisualModality.DOCUMENT_EVIDENCE
                    elif winner.source_type == VisualSourceType.LOCAL_WEB_APP:
                        actual_modality = VisualModality.SCREEN_CAPTURE
                    elif winner.source_type == VisualSourceType.RENDERED:
                        actual_modality = VisualModality.DIAGRAM
                    elif winner.source_type == VisualSourceType.FALLBACK_CARD:
                        actual_modality = VisualModality.STATIC_CARD
                    else:
                        actual_modality = request.modality

        return VisualAcquisitionResult(
            request=request,
            candidates=candidates,
            selected_candidate_id=selected_id,
            actual_modality=actual_modality,
            failure_reasons=failures,
            semantic_audit=semantic_audit,
        )

    def to_provenance(
        self,
        candidate: VisualAssetCandidate,
        shot_id: str,
    ) -> VisualAssetProvenance:
        """Create an immutable VisualAssetProvenance record from a selected candidate."""
        return VisualAssetProvenance(
            shot_id=shot_id,
            asset_path=candidate.file_path,
            asset_sha256=candidate.content_sha256,
            source_type=candidate.source_type,
            source_url=candidate.source_url,
            source_ref=candidate.source_ref,
            license_type=candidate.license_type,
            attribution=candidate.attribution,
            evidence_claim_ids=candidate.evidence_claim_ids,
            acquisition_method=candidate.acquisition_method,
            synthetic=candidate.is_synthetic,
        )
