"""Channel Creative Profiles and Anti-AI-Slop Styling Policies."""

from app.media.director.models import ChannelCreativeProfile, ContentFormat, VisualModality


EDITORIAL_TECH_PROFILE = ChannelCreativeProfile(
    name="Editorial Tech Shorts",
    niche=["technology", "software_engineering", "ai", "cloud"],
    content_formats=[
        ContentFormat.EXPLAINER,
        ContentFormat.DEMO,
        ContentFormat.COMPARISON,
        ContentFormat.CASE_STUDY,
    ],
    primary_style="editorial-tech",
    secondary_style="clean-motion-graphics",
    target_average_shot_length=2.4,
    preferred_modalities=[
        VisualModality.UI_SIMULATION,
        VisualModality.DIAGRAM,
        VisualModality.CODE_ANIMATION,
        VisualModality.DATA_VISUALIZATION,
        VisualModality.MOTION_GRAPHICS,
        VisualModality.DOCUMENT_EVIDENCE,
        VisualModality.GENERATED_VIDEO,
    ],
    avoid_modalities=[VisualModality.STATIC_CARD],
    avoid_tropes=[
        "generic cyberpunk",
        "glowing AI brain",
        "humanoid robot",
        "meaningless code backgrounds",
        "random neon lights",
        "generic businessman",
        "generic office stock",
        "text slides repeating narration",
    ],
    music_mood="light_modern_technology",
    music_bpm=95,
    sfx_intensity=1.0,
)

CODE_TUTORIAL_PROFILE = ChannelCreativeProfile(
    name="Code Native Developer Shorts",
    niche=["programming", "python", "devops", "systems"],
    content_formats=[ContentFormat.DEMO, ContentFormat.BREAKDOWN, ContentFormat.BEFORE_AFTER],
    primary_style="code-native-dark",
    secondary_style="terminal-matrix",
    target_average_shot_length=3.0,
    preferred_modalities=[
        VisualModality.CODE_ANIMATION,
        VisualModality.UI_SIMULATION,
        VisualModality.DIAGRAM,
        VisualModality.MOTION_GRAPHICS,
    ],
    avoid_modalities=[VisualModality.STATIC_CARD, VisualModality.GENERATED_IMAGE],
    avoid_tropes=[
        "neon matrix rain",
        "laptop with green zeroes and ones",
        "hooded hacker",
        "generic stock typing",
    ],
    music_mood="minimal_electronic",
    music_bpm=80,
    sfx_intensity=0.8,
)

BENCHMARK_ANALYSIS_PROFILE = ChannelCreativeProfile(
    name="Data Journalism & Benchmark Review",
    niche=["ai_research", "benchmarks", "hardware", "models"],
    content_formats=[ContentFormat.COMPARISON, ContentFormat.CASE_STUDY, ContentFormat.EXPERIMENT],
    primary_style="data-journalism",
    secondary_style="academic-clean",
    target_average_shot_length=2.2,
    preferred_modalities=[
        VisualModality.DATA_VISUALIZATION,
        VisualModality.COMPARISON,
        VisualModality.DOCUMENT_EVIDENCE,
        VisualModality.DIAGRAM,
        VisualModality.MOTION_GRAPHICS,
    ],
    avoid_modalities=[VisualModality.STATIC_CARD],
    avoid_tropes=[
        "arbitrary glowing pie charts",
        "abstract floating particles",
        "slides repeating spoken numbers",
    ],
    music_mood="editorial_pulse",
    music_bpm=105,
    sfx_intensity=1.2,
)

TECH_DOCUMENTARY_PROFILE = ChannelCreativeProfile(
    name="Tech Deep-Dive & Origins",
    niche=["tech_history", "industry_origins", "biography"],
    content_formats=[ContentFormat.STORY, ContentFormat.CASE_STUDY, ContentFormat.BREAKDOWN],
    primary_style="cinematic-documentary",
    secondary_style="archival-editorial",
    target_average_shot_length=3.2,
    preferred_modalities=[
        VisualModality.STOCK_VIDEO,
        VisualModality.GENERATED_VIDEO,
        VisualModality.TIMELINE,
        VisualModality.MAP,
        VisualModality.DOCUMENT_EVIDENCE,
    ],
    avoid_modalities=[VisualModality.STATIC_CARD],
    avoid_tropes=[
        "cheesy stock handshakes",
        "robot shaking hands with human",
        "floating futuristic compass",
    ],
    music_mood="ambient_cinematic",
    music_bpm=75,
    sfx_intensity=1.1,
)


def get_channel_profile_for_niche(niche: str) -> ChannelCreativeProfile:
    """Retrieve the optimal ChannelCreativeProfile for a given channel niche."""
    n_lower = niche.lower()
    if any(k in n_lower for k in ["code", "developer", "programming", "devops"]):
        return CODE_TUTORIAL_PROFILE
    if any(k in n_lower for k in ["benchmark", "data", "paper", "metric", "hardware", "gpu"]):
        return BENCHMARK_ANALYSIS_PROFILE
    if any(k in n_lower for k in ["history", "doc", "story", "founder"]):
        return TECH_DOCUMENTARY_PROFILE
    return EDITORIAL_TECH_PROFILE
