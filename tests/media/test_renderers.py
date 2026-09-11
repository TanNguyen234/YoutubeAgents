"""Tests for local visual renderers: DiagramRenderer, ChartRenderer, MotionGraphicsRenderer, EvidenceRenderer."""

from pathlib import Path
from PIL import Image
import pytest

from app.media.renderers.chart_renderer import ChartRenderer
from app.media.renderers.diagram_renderer import DiagramRenderer
from app.media.renderers.evidence_renderer import EvidenceRenderer
from app.media.renderers.motion_graphics import MotionGraphicsRenderer


def test_diagram_renderer_flow(tmp_path: Path):
    """Verify DiagramRenderer generates a valid 1080x1920 flow diagram image."""
    renderer = DiagramRenderer(width=1080, height=1920)
    out_path = tmp_path / "diagram_flow.png"

    nodes = ["User Query", "Retriever", "Context Window", "LLM Generation", "Grounded Response"]
    res_path, sha256 = renderer.render_architecture_diagram(
        title="RAG Architecture Flow",
        steps_or_nodes=nodes,
        output_path=out_path,
        active_step_index=2,
    )

    assert Path(res_path).exists()
    assert len(sha256) == 64
    with Image.open(res_path) as img:
        assert img.size == (1080, 1920)
        assert img.format == "PNG"


def test_diagram_renderer_from_instruction(tmp_path: Path):
    """Verify parsing diagram instructions into structured flow blocks."""
    renderer = DiagramRenderer(width=1080, height=1920)
    out_path = tmp_path / "diagram_instruction.png"

    res_path, sha256 = renderer.render_from_instruction(
        instruction="Query -> Embedding Model -> Vector Search -> Top-K Chunks -> Synthesis",
        output_path=out_path,
        title="Vector Search Pipeline",
    )

    assert Path(res_path).exists()
    with Image.open(res_path) as img:
        assert img.size == (1080, 1920)


def test_chart_renderer_horizontal_bars(tmp_path: Path):
    """Verify ChartRenderer generates a high-contrast horizontal bar chart."""
    renderer = ChartRenderer(width=1080, height=1920)
    out_path = tmp_path / "bar_chart.png"

    categories = ["Paris", "Lyon", "Marseille", "London"]
    values = [82.0, 6.0, 4.0, 2.0]
    res_path, sha256 = renderer.render_horizontal_bar_chart(
        title="Token Probability Distribution",
        categories=categories,
        values=values,
        output_path=out_path,
        unit="%",
        subtitle="Prompt: 'The capital of France is ...'",
        highlight_index=0,
    )

    assert Path(res_path).exists()
    assert len(sha256) == 64
    with Image.open(res_path) as img:
        assert img.size == (1080, 1920)


def test_motion_graphics_comparison(tmp_path: Path):
    """Verify MotionGraphicsRenderer creates a side-by-side comparison card."""
    renderer = MotionGraphicsRenderer(width=1080, height=1920)
    out_path = tmp_path / "comparison.png"

    left_points = ["Shared Host OS Kernel", "Instant Startup (<100ms)", "Minimal RAM Footprint (~50MB)"]
    right_points = ["Dedicated Guest OS Kernel", "Slow Boot (30s+)", "Heavy Memory Overhead (~2GB+)"]

    res_path, sha256 = renderer.render_before_after_comparison(
        title="Architecture Comparison",
        before_label="Virtual Machines",
        before_points=right_points,
        after_label="Docker Containers",
        after_points=left_points,
        output_path=out_path,
    )

    assert Path(res_path).exists()
    with Image.open(res_path) as img:
        assert img.size == (1080, 1920)


def test_motion_graphics_terminal(tmp_path: Path):
    """Verify MotionGraphicsRenderer renders a developer terminal simulation."""
    renderer = MotionGraphicsRenderer(width=1080, height=1920)
    out_path = tmp_path / "terminal.png"

    command = "git commit -m 'feat: multi-shot visual director'"
    output_lines = [
        "[main 4a7c81b] feat: multi-shot visual director",
        " 3 files changed, 280 insertions(+)",
        " create mode 100644 app/media/director.py",
    ]
    res_path, sha256 = renderer.render_code_terminal(
        command=command,
        output_lines=output_lines,
        output_path=out_path,
        window_title="bash — git terminal",
    )

    assert Path(res_path).exists()
    with Image.open(res_path) as img:
        assert img.size == (1080, 1920)


def test_evidence_renderer(tmp_path: Path):
    """Verify EvidenceRenderer generates a grounded research citation card."""
    renderer = EvidenceRenderer(width=1080, height=1920)
    out_path = tmp_path / "evidence.png"

    res_path, sha256 = renderer.render_evidence_card(
        source_title="NVIDIA Q4 Fiscal 2024 Revenue Report",
        source_url="https://nvidianews.nvidia.com/news/q4-fy24-earnings",
        highlighted_claim="Data Center revenue for the fourth quarter was a record $18.4 billion, up 409% from a year ago.",
        output_path=out_path,
        benchmark_name="Q4 FY24 Earnings",
    )

    assert Path(res_path).exists()
    with Image.open(res_path) as img:
        assert img.size == (1080, 1920)
