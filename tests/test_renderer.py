"""Unit tests for renderer.py: Mermaid diagram validation and markdown document rendering."""

import tempfile
from pathlib import Path

from repo_architect.agent import ArchitectureAnalysis, ComponentInfo
from repo_architect.renderer import ArchitectureRenderer, sanitize_and_validate_mermaid
from repo_architect.scanner import ScanStats


def test_sanitize_and_validate_mermaid():
    """Verifies that Mermaid diagram errors are sanitized."""
    # Test 1: Missing header and markdown wrappers
    raw_1 = """```mermaid
subgraph Core
    A[Scanner Module (Privacy)]
    B[Analyzer Module]
    A --> B
end
```"""
    is_valid, cleaned, warnings = sanitize_and_validate_mermaid(raw_1)
    assert cleaned.startswith("flowchart TD")
    assert 'A["Scanner Module (Privacy)"]' in cleaned
    assert 'B["Analyzer Module"]' in cleaned
    assert "```" not in cleaned
    assert is_valid is True

    # Test 2: Unclosed subgraph
    raw_2 = """flowchart LR
subgraph Core ["Core Modules"]
    node1["Item 1"]
"""
    is_valid, cleaned, warnings = sanitize_and_validate_mermaid(raw_2)
    assert "end" in cleaned
    assert is_valid is True


def test_architecture_renderer_output():
    """Verifies complete ARCHITECTURE.md markdown generation."""
    analysis = ArchitectureAnalysis(
        project_name="TestProject",
        tagline="Test architectural summary",
        overview="High-level test overview description.",
        key_components=[
            ComponentInfo(
                name="Core Engine",
                description="Processes events.",
                responsibilities=["Task execution", "State management"],
                files=["src/core.py"],
            )
        ],
        entry_points=["`src/main.py` - Primary CLI"],
        data_flows=["1. Ingest", "2. Process", "3. Render"],
        mermaid_diagram="flowchart TD\n    A[\"Core\"] --> B[\"Output\"]",
        architectural_highlights=["Clean Architecture", "Modular Boundary"],
        privacy_notes="100% Local Execution Verified.",
    )

    stats = ScanStats(
        total_files_visited=10,
        files_indexed=5,
        files_excluded=5,
        secrets_masked=2,
    )

    renderer = ArchitectureRenderer(analysis, stats)
    markdown = renderer.render()

    assert "# Architecture Overview: TestProject" in markdown
    assert "```mermaid" in markdown
    assert 'A["Core"] --> B["Output"]' in markdown
    assert "Core Engine" in markdown
    assert "Files Indexed:** 5" in markdown
    assert "Masked Secrets:** 2" in markdown

    with tempfile.TemporaryDirectory() as tmpdir:
        out_file = Path(tmpdir) / "ARCHITECTURE.md"
        written = renderer.write_to_file(out_file)
        assert written.exists()
        assert written.read_text(encoding="utf-8") == markdown
