"""Command Line Interface for Repo-Architect.

Built with Typer and Rich for local-first, privacy-focused codebase architecture
analysis and Mermaid.js diagram generation.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from repo_architect import __app_name__, __version__
from repo_architect.agent import ArchitectureAgent, DEFAULT_BASE_URL, DEFAULT_MODEL
from repo_architect.analyzer import CodeAnalyzer
from repo_architect.renderer import ArchitectureRenderer
from repo_architect.scanner import scan_repository

app = typer.Typer(
    name=__app_name__,
    help="Repo-Architect: Local-first, privacy-focused CLI architecture analyzer.",
    add_completion=False,
)
console = Console()


def version_callback(value: bool):
    if value:
        console.print(f"[bold cyan]{__app_name__}[/bold cyan] version [bold green]{__version__}[/bold green]")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None,
        "--version",
        "-V",
        help="Show version and exit.",
        callback=version_callback,
        is_eager=True,
    ),
):
    """Repo-Architect CLI root."""
    pass


@app.command(name="scan")
def scan(
    path: Path = typer.Argument(
        Path("."),
        help="Path to the local repository directory to analyze.",
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        resolve_path=True,
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Destination path for ARCHITECTURE.md (default: <target>/ARCHITECTURE.md).",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        "-m",
        help=f"Local LLM model name (default: {DEFAULT_MODEL} or env LLM_MODEL).",
    ),
    base_url: Optional[str] = typer.Option(
        None,
        "--base-url",
        "-b",
        help=f"Local OpenAI/Ollama endpoint (default: {DEFAULT_BASE_URL} or env LLM_BASE_URL).",
    ),
    offline: bool = typer.Option(
        False,
        "--offline",
        "--no-llm",
        help="Run pure static AST heuristic analysis without querying the local LLM.",
    ),
    max_files: int = typer.Option(
        500,
        "--max-files",
        help="Maximum number of source files to index.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Display detailed exclusion and scanning logs.",
    ),
):
    """Analyze a local repository and generate ARCHITECTURE.md with Mermaid diagrams."""
    # Banner
    console.print(
        Panel(
            f"[bold cyan]Repo-Architect[/bold cyan] [dim]v{__version__}[/dim]\n"
            f"[green]🛡️  Privacy Shield Active:[/green] [white]100% Local Execution • Cloud Egress Blocked[/white]\n"
            f"[dim]Scanning Target:[/dim] [bold yellow]{path}[/bold yellow]",
            box=box.ROUNDED,
            border_style="cyan",
        )
    )

    output_file = output or (path / "ARCHITECTURE.md")

    # Phase 1: Ingestion & Privacy Guard
    with console.status("[bold cyan]Scanning repository files & applying privacy guards...[/bold cyan]", spinner="dots"):
        scanned_files, stats = scan_repository(path)

    if verbose:
        console.print(f"[dim]Total files visited: {stats.total_files_visited}[/dim]")
        console.print(f"[dim]Files excluded by privacy guard / gitignore: {stats.files_excluded}[/dim]")
        console.print(f"[dim]Files indexed: {stats.files_indexed}[/dim]")
        if stats.secrets_masked > 0:
            console.print(f"[yellow]Masked {stats.secrets_masked} potential hardcoded secret(s)[/yellow]")

    if not scanned_files:
        console.print("[bold red]Error:[/bold red] No readable source files found after privacy exclusion filters.")
        raise typer.Exit(code=1)

    if len(scanned_files) > max_files:
        console.print(f"[yellow]Notice:[/yellow] Limiting scan to first {max_files} files (out of {len(scanned_files)} indexed).")
        scanned_files = scanned_files[:max_files]

    # Phase 2: Static Analysis
    with console.status("[bold cyan]Performing static AST parsing & dependency mapping...[/bold cyan]", spinner="dots"):
        analyzer = CodeAnalyzer(scanned_files)
        graph = analyzer.analyze()

    # Phase 3: Architectural Synthesis
    mode_text = "[yellow](Offline Heuristics)[/yellow]" if offline else f"[green](Endpoint: {base_url or DEFAULT_BASE_URL})[/green]"
    with console.status(f"[bold cyan]Synthesizing architecture & Mermaid diagram {mode_text}...[/bold cyan]", spinner="dots"):
        agent = ArchitectureAgent(base_url=base_url, model=model)
        project_name = path.name if path.name not in ("", ".") else "Repository"
        analysis = agent.analyze(graph, stats, project_name=project_name, offline=offline)

    # Phase 4: Rendering & Validation
    with console.status("[bold cyan]Validating Mermaid flowchart & rendering markdown...[/bold cyan]", spinner="dots"):
        renderer = ArchitectureRenderer(analysis, stats)
        written_path = renderer.write_to_file(output_file)

    # Metrics Summary Table
    table = Table(title="Repo-Architect Scan Summary", box=box.SIMPLE_HEAVY)
    table.add_column("Metric", style="cyan")
    table.add_column("Count / Status", style="green")

    table.add_row("Files Visited", str(stats.total_files_visited))
    table.add_row("Files Indexed", str(stats.files_indexed))
    table.add_row("Files Excluded (Privacy Guard)", str(stats.files_excluded))
    table.add_row("Hardcoded Secrets Masked", str(stats.secrets_masked))
    table.add_row("Modules Mapped", str(len(graph.modules)))
    table.add_row("Entry Points Detected", str(len(graph.entry_points)))
    table.add_row("Internal Dependency Edges", str(len(graph.internal_edges)))
    table.add_row("External Dependencies", str(len(graph.external_dependencies)))
    table.add_row("Output File", str(written_path))

    console.print(table)

    # Preview Mermaid snippet
    mermaid_preview = "\n".join(analysis.mermaid_diagram.strip().splitlines()[:12])
    if len(analysis.mermaid_diagram.splitlines()) > 12:
        mermaid_preview += "\n    %% ... [truncated preview]"

    console.print(
        Panel(
            Syntax(mermaid_preview, "mermaid", theme="monokai", line_numbers=True),
            title="[bold green]Generated Mermaid.js Flowchart Preview[/bold green]",
            border_style="green",
        )
    )

    console.print(
        Panel(
            f"[bold green]Success![/bold green] Architectural overview generated at:\n"
            f"[bold white]{written_path}[/bold white]\n\n"
            f"[dim]View in any Markdown / Mermaid viewer (VS Code, GitHub, Obsidian).[/dim]",
            border_style="bright_green",
        )
    )


if __name__ == "__main__":
    app()
