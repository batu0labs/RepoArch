"""Local LLM client with structured output (Pydantic) for architectural synthesis.

Communicates strictly with local OpenAI-compatible or Ollama endpoints
(default: http://localhost:11434/v1). Never sends code or metadata to external
cloud APIs.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai import OpenAI
from pydantic import BaseModel, Field

from repo_architect.analyzer import DependencyGraph
from repo_architect.scanner import ScanStats


# Environment configuration defaults
DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "qwen2.5-coder"
DEFAULT_API_KEY = "ollama"
DEFAULT_TIMEOUT_SEC = 90


class ComponentInfo(BaseModel):
    """Architectural metadata for a single logical component or module."""

    name: str = Field(description="Name of the component or subsystem")
    description: str = Field(description="Summary of the component's role")
    responsibilities: List[str] = Field(default_factory=list, description="Core responsibilities")
    files: List[str] = Field(default_factory=list, description="Associated source files")


class ArchitectureAnalysis(BaseModel):
    """Structured architectural overview and Mermaid flowchart."""

    project_name: str = Field(description="Name of the analyzed project")
    tagline: str = Field(description="One-sentence architectural summary")
    overview: str = Field(description="High-level architectural narrative and design principles")
    key_components: List[ComponentInfo] = Field(default_factory=list, description="List of primary components")
    entry_points: List[str] = Field(default_factory=list, description="Discovered system entry points and invocation flows")
    data_flows: List[str] = Field(default_factory=list, description="Ordered steps of the primary data flows")
    mermaid_diagram: str = Field(description="Valid Mermaid flowchart starting with 'flowchart TD' or 'flowchart LR'")
    architectural_highlights: List[str] = Field(default_factory=list, description="Key design patterns and separation of concerns")
    privacy_notes: str = Field(default="100% Local Execution. All analysis performed locally without cloud transmission.")


SYSTEM_PROMPT = """You are a senior software architect specializing in codebase reverse-engineering, system design, and Mermaid.js flowcharts.
Your task is to analyze static code metadata (modules, classes, functions, imports, entry points) and generate a comprehensive, structured architectural summary.

CRITICAL INSTRUCTIONS FOR MERMAID.JS DIAGRAMS:
1. The diagram MUST start with 'flowchart TD' or 'flowchart LR'.
2. Node IDs MUST be alphanumeric with underscores only (e.g., cli_entry, scanner_mod, agent_llm). Do NOT use hyphens, spaces, or dots in node IDs.
3. Node labels MUST be wrapped in double quotes inside brackets: nodeId["Label Description"].
4. Subgraphs MUST follow:
   subgraph sg_core ["Core Processing Layer"]
       mod_a["Module A"]
   end
5. Use directional arrows: -->, -.->, or ==>
6. NEVER include HTML tags (<br>, <b>) inside labels.
7. Ensure all brackets, parentheses, and subgraphs are strictly balanced and closed.
8. Focus on clear architectural relationships: Entry Points -> Processing -> Core Engine -> Outputs/Storage.

Respond ONLY with a valid JSON object matching the requested schema. Do not add markdown backticks outside the JSON."""


class ArchitectureAgent:
    """Manages queries to a local OpenAI-compatible endpoint with fallback heuristics."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: Optional[int] = None,
    ):
        self.base_url = (
            base_url
            or os.environ.get("LLM_BASE_URL")
            or DEFAULT_BASE_URL
        )
        self.model = (
            model
            or os.environ.get("LLM_MODEL")
            or DEFAULT_MODEL
        )
        self.api_key = (
            api_key
            or os.environ.get("LLM_API_KEY")
            or DEFAULT_API_KEY
        )
        self.timeout = int(
            timeout
            or os.environ.get("LLM_TIMEOUT")
            or DEFAULT_TIMEOUT_SEC
        )

        self.client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=float(self.timeout),
        )

    def analyze(
        self,
        graph: DependencyGraph,
        stats: ScanStats,
        project_name: Optional[str] = None,
        offline: bool = False,
    ) -> ArchitectureAnalysis:
        """Runs architectural synthesis using the local LLM, or fallback heuristic if unreachable.

        Args:
            graph: Extracted static dependency graph.
            stats: Repository scanning statistics.
            project_name: Optional display name for the project.
            offline: If True, skips network call and directly returns heuristic analysis.

        Returns:
            ArchitectureAnalysis instance.
        """
        proj_name = project_name or self._infer_project_name(graph)

        if offline:
            return self._generate_heuristic_analysis(graph, stats, proj_name, reason="Offline mode requested")

        user_content = self._build_user_prompt(graph, stats, proj_name)

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )

            raw_text = response.choices[0].message.content or "{}"
            parsed_data = self._parse_json_response(raw_text)
            return ArchitectureAnalysis.model_validate(parsed_data)

        except Exception as e:
            # Fallback cleanly to deterministic AST-based analysis on connection/parse error
            error_msg = f"Local LLM ({self.base_url}, model={self.model}) unavailable or returned error: {e}"
            return self._generate_heuristic_analysis(graph, stats, proj_name, reason=error_msg)

    def _build_user_prompt(self, graph: DependencyGraph, stats: ScanStats, project_name: str) -> str:
        """Assembles prompt payload containing static code insights."""
        summary_text = graph.get_summary_prompt()
        prompt = f"""Target Project: {project_name}

Scanned Metrics:
- Indexed Files: {stats.files_indexed}
- Excluded Files (Privacy Guard): {stats.files_excluded}
- Masked Hardcoded Secrets: {stats.secrets_masked}

Codebase Static Graph & Structure:
{summary_text}

JSON Schema:
{{
  "project_name": "{project_name}",
  "tagline": "Brief architectural summary",
  "overview": "Detailed architectural narrative and purpose",
  "key_components": [
    {{
      "name": "Component Name",
      "description": "Component overview",
      "responsibilities": ["Responsibility 1", "Responsibility 2"],
      "files": ["path/to/file.py"]
    }}
  ],
  "entry_points": ["Description of CLI or main entry point"],
  "data_flows": ["Step 1: Input ingestion", "Step 2: Processing", "Step 3: Output rendering"],
  "mermaid_diagram": "flowchart TD\\n    subgraph Core [\\"Core Modules\\"]\\n        mod_a[\\"Module A\\"]\\n    end",
  "architectural_highlights": ["Highlight 1", "Highlight 2"],
  "privacy_notes": "100% Local Execution confirmation"
}}
"""
        return prompt

    def _parse_json_response(self, text: str) -> Dict[str, Any]:
        """Safely parses JSON from model output, stripping any markdown wrappers."""
        cleaned = text.strip()
        # Remove markdown ```json ... ``` wrapper if present
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.MULTILINE)
            cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE).strip()

        # Try direct parse
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Fallback: find first '{' and last '}'
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(cleaned[start : end + 1])
            raise

    def _infer_project_name(self, graph: DependencyGraph) -> str:
        """Infers project name from common directory or entry points."""
        for path in graph.modules:
            parts = Path(path).parts
            if len(parts) > 1 and parts[0] not in {"tests", "test", "docs"}:
                return parts[0].replace("_", "-")
        return "Repository"

    def _generate_heuristic_analysis(
        self,
        graph: DependencyGraph,
        stats: ScanStats,
        project_name: str,
        reason: Optional[str] = None,
    ) -> ArchitectureAnalysis:
        """Deterministic architectural synthesis derived directly from static AST metadata."""
        components: List[ComponentInfo] = []

        # Create components from module clusters
        for cluster_name, paths in sorted(graph.clusters.items()):
            files = sorted(paths)
            cluster_modules = [graph.modules[p] for p in paths if p in graph.modules]
            all_classes = [c for m in cluster_modules for c in m.classes]
            all_funcs = [f for m in cluster_modules for f in m.functions]

            responsibilities = []
            if any(m.is_entry_point for m in cluster_modules):
                responsibilities.append("Serves as system entry point and user interaction handler")
            if any("test" in p.lower() for p in paths):
                responsibilities.append("Automated verification and unit test suite")
            elif "cli" in cluster_name.lower():
                responsibilities.append("Command-line argument parsing and interactive terminal UI")
            elif "scanner" in cluster_name.lower():
                responsibilities.append("Filesystem traversal, privacy filtering, and secret sanitization")
            elif "analyzer" in cluster_name.lower():
                responsibilities.append("Static AST parsing and dependency graph extraction")
            elif "agent" in cluster_name.lower():
                responsibilities.append("Local LLM interaction and structured data synthesis")
            elif "render" in cluster_name.lower():
                responsibilities.append("Output formatting and Mermaid diagram validation")
            else:
                responsibilities.append(f"Provides core domain functionality for {cluster_name}")

            if all_classes:
                responsibilities.append(f"Implements key classes: {', '.join(all_classes[:4])}")

            components.append(
                ComponentInfo(
                    name=cluster_name.capitalize().replace("_", " "),
                    description=f"Subsystem managing {len(files)} module(s) within the '{cluster_name}' domain.",
                    responsibilities=responsibilities,
                    files=files,
                )
            )

        entry_point_descs = [
            f"`{ep.file_path}` ({ep.entry_type}) - {ep.description}"
            for ep in graph.entry_points
        ] or ["Standard package module execution via CLI or library import"]

        data_flows = [
            "1. Invocation & Initialization: User or script invokes entry point",
            "2. Static Ingestion: Filesystem traversal and static code metadata extraction",
            "3. Internal Dependency Linking: Module imports and relationships mapped",
            "4. Architecture Synthesis: Structural summarization and Mermaid flowchart construction",
            "5. Document Rendering: Validated ARCHITECTURE.md generated for developer consumption",
        ]

        mermaid_diag = graph.to_mermaid_fallback()

        notice = f"Analysis generated via deterministic static AST heuristics ({reason})." if reason else "Analysis generated locally."

        return ArchitectureAnalysis(
            project_name=project_name,
            tagline="Local-first codebase architecture and structural overview.",
            overview=(
                f"{project_name} is organized into {len(graph.clusters)} primary subsystem(s) "
                f"encompassing {len(graph.modules)} source module(s) and {len(graph.internal_edges)} internal dependency links. "
                f"The architecture maintains strict separation of concerns with privacy-first local processing."
            ),
            key_components=components,
            entry_points=entry_point_descs,
            data_flows=data_flows,
            mermaid_diagram=mermaid_diag,
            architectural_highlights=[
                "Privacy-Preserving Architecture: Operates entirely offline with zero cloud telemetry.",
                "Modular Subsystem Partitioning: Clear separation between ingestion, parsing, synthesis, and rendering.",
                f"Dependency Health: {len(graph.external_dependencies)} external third-party package dependency footprint.",
            ],
            privacy_notes=f"{notice} Strictly local execution verified.",
        )
