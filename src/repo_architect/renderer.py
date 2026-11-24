"""Renderer and validator for ARCHITECTURE.md and Mermaid.js diagrams.

Validates syntax, ensures clean node/edge structures, and formats professional
architectural documentation.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from repo_architect.agent import ArchitectureAnalysis
from repo_architect.scanner import ScanStats


def sanitize_and_validate_mermaid(raw_diagram: str) -> Tuple[bool, str, List[str]]:
    """Sanitizes and validates Mermaid.js flowchart syntax.

    Fixes common model mistakes:
    - Strips markdown code blocks (```mermaid ... ```).
    - Ensures flowchart header is present (flowchart TD or LR).
    - Wraps raw node text in quotes to prevent bracket/paren syntax errors.
    - Strips unsupported HTML tags.
    - Checks for balanced brackets and subgraph/end pairs.

    Returns:
        Tuple of (is_valid: bool, sanitized_diagram: str, warnings: List[str]).
    """
    warnings: List[str] = []
    lines = raw_diagram.strip().splitlines()

    # Strip code block wrappers if present
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            continue
        cleaned_lines.append(line)

    content = "\n".join(cleaned_lines).strip()

    # 1. Ensure valid header
    first_meaningful_line = ""
    for l in content.splitlines():
        if l.strip() and not l.strip().startswith("%%"):
            first_meaningful_line = l.strip().lower()
            break

    valid_headers = ("flowchart td", "flowchart lr", "flowchart tb", "flowchart rl", "graph td", "graph lr", "graph tb")
    if not any(first_meaningful_line.startswith(h) for h in valid_headers):
        warnings.append("Missing valid Mermaid flowchart header; prepended 'flowchart TD'.")
        content = "flowchart TD\n" + content

    # 2. Strip HTML tags from labels
    content = re.sub(r"<\s*br\s*/?\s*>", " ", content, flags=re.IGNORECASE)
    content = re.sub(r"</?[a-zA-Z0-9]+[^>]*>", "", content)

    # 3. Ensure unquoted bracket labels are quoted: node[Label (Text)] -> node["Label (Text)"]
    # Look for id[text] where text does not start with double quote
    def quote_brackets(match: re.Match[str]) -> str:
        prefix = match.group(1)
        inner = match.group(2).strip()
        if inner.startswith('"') and inner.endswith('"'):
            return f"{prefix}[{inner}]"
        # Escape any internal double quotes
        inner = inner.replace('"', "'")
        return f'{prefix}["{inner}"]'

    content = re.sub(r"([A-Za-z0-9_]+)\[([^\"\]\n]+)\]", quote_brackets, content)

    # 4. Ensure stadium node labels are quoted: node([Label (Text)]) -> node(["Label (Text)"])
    def quote_stadium(match: re.Match[str]) -> str:
        prefix = match.group(1)
        inner = match.group(2).strip()
        if inner.startswith('"') and inner.endswith('"'):
            return f"{prefix}([{inner}])"
        inner = inner.replace('"', "'")
        return f'{prefix}(["{inner}"])'

    content = re.sub(r"([A-Za-z0-9_]+)\(\[([^\"\]\n]+)\]\)", quote_stadium, content)

    # 5. Check balanced subgraphs and 'end'
    subgraph_count = len(re.findall(r"^\s*subgraph\b", content, re.MULTILINE))
    end_count = len(re.findall(r"^\s*end\b", content, re.MULTILINE))
    if subgraph_count > end_count:
        missing = subgraph_count - end_count
        warnings.append(f"Fixed {missing} unclosed subgraph block(s).")
        content += "\n" + "\n".join(["    end"] * missing)

    # 6. Basic bracket balance check
    open_brackets = content.count("[")
    close_brackets = content.count("]")
    if open_brackets != close_brackets:
        warnings.append(f"Bracket count mismatch: {open_brackets} open vs {close_brackets} close.")

    is_valid = len([w for w in warnings if "mismatch" in w]) == 0
    return is_valid, content.strip(), warnings


class ArchitectureRenderer:
    """Renders comprehensive, validated ARCHITECTURE.md documents."""

    def __init__(self, analysis: ArchitectureAnalysis, stats: Optional[ScanStats] = None):
        self.analysis = analysis
        self.stats = stats

    def render(self) -> str:
        """Constructs markdown content string."""
        _valid, mermaid_code, warnings = sanitize_and_validate_mermaid(self.analysis.mermaid_diagram)

        lines: List[str] = []

        # Document Header
        lines.append(f"# Architecture Overview: {self.analysis.project_name}")
        lines.append("")
        lines.append("> **Local-First Codebase Architecture Analysis**  ")
        lines.append(f"> *{self.analysis.tagline}*  ")
        lines.append(f"> Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} • **100% Local Execution**")
        lines.append("")
        lines.append("---")
        lines.append("")

        # Executive Summary
        lines.append("## 1. Executive Summary")
        lines.append("")
        lines.append(self.analysis.overview)
        lines.append("")

        # Architectural Highlights
        if self.analysis.architectural_highlights:
            lines.append("### Architectural Principles & Highlights")
            for highlight in self.analysis.architectural_highlights:
                lines.append(f"- {highlight}")
            lines.append("")

        # Mermaid Flowchart
        lines.append("## 2. High-Level System Flowchart")
        lines.append("")
        if warnings:
            lines.append(f"> [!NOTE]\n> Mermaid Sanitizer Note: {'; '.join(warnings)}\n")
        lines.append("```mermaid")
        lines.append(mermaid_code)
        lines.append("```")
        lines.append("")

        # Key Components
        lines.append("## 3. Core Subsystems & Components")
        lines.append("")
        if self.analysis.key_components:
            lines.append("| Component | Description | Key Files |")
            lines.append("| :--- | :--- | :--- |")
            for comp in self.analysis.key_components:
                files_str = "<br>".join([f"`{f}`" for f in comp.files[:3]])
                if len(comp.files) > 3:
                    files_str += f"<br>*(+{len(comp.files)-3} more)*"
                desc_clean = comp.description.replace("|", "/")
                lines.append(f"| **{comp.name}** | {desc_clean} | {files_str or '—'} |")
            lines.append("")

            lines.append("### Component Details & Responsibilities")
            for comp in self.analysis.key_components:
                lines.append(f"#### {comp.name}")
                lines.append(f"{comp.description}")
                if comp.responsibilities:
                    lines.append("")
                    lines.append("**Responsibilities:**")
                    for resp in comp.responsibilities:
                        lines.append(f"- {resp}")
                if comp.files:
                    lines.append("")
                    lines.append(f"**Associated Files:** {', '.join([f'`{f}`' for f in comp.files])}")
                lines.append("")

        # Entry Points
        lines.append("## 4. System Entry Points")
        lines.append("")
        if self.analysis.entry_points:
            for ep in self.analysis.entry_points:
                lines.append(f"- {ep}")
        else:
            lines.append("- *No discrete executable entry points detected.*")
        lines.append("")

        # Data & Control Flows
        lines.append("## 5. Primary Data & Execution Flows")
        lines.append("")
        if self.analysis.data_flows:
            for df in self.analysis.data_flows:
                lines.append(f"- {df}")
        else:
            lines.append("- *Standard procedural invocation flow.*")
        lines.append("")

        # Privacy & Security Verification Report
        lines.append("## 6. Privacy Guard & Security Verification")
        lines.append("")
        lines.append(f"- **Zero External Egress:** {self.analysis.privacy_notes}")
        if self.stats:
            lines.append(f"- **Files Visited:** {self.stats.total_files_visited}")
            lines.append(f"- **Files Indexed:** {self.stats.files_indexed}")
            lines.append(f"- **Files Excluded (Strict Guard & .gitignore):** {self.stats.files_excluded}")
            lines.append(f"- **Masked Secrets:** {self.stats.secrets_masked}")
            if self.stats.excluded_reasons:
                reasons_str = ", ".join([f"{k}: {v}" for k, v in self.stats.excluded_reasons.items() if v > 0])
                lines.append(f"- **Exclusion Breakdown:** {reasons_str}")
        lines.append("")
        lines.append("---")
        lines.append("*Report generated locally by [Repo-Architect](https://github.com/)*")
        lines.append("")

        return "\n".join(lines)

    def write_to_file(self, output_path: Path) -> Path:
        """Renders and writes output to the given file path."""
        content = self.render()
        resolved_path = output_path.resolve()
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        with open(resolved_path, "w", encoding="utf-8") as f:
            f.write(content)
        return resolved_path
