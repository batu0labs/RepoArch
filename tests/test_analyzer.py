"""Unit tests for analyzer.py: AST dependency extraction and entry point detection."""

from pathlib import Path

from repo_architect.analyzer import CodeAnalyzer
from repo_architect.scanner import ScannedFile


def test_python_ast_extraction():
    """Verifies class, function, and import extraction from Python source."""
    code = '''"""Core processor module."""

import os
import sys
from typing import List, Optional
import requests
from my_package.utils import helper_func

class Processor:
    """Processes items."""
    def run(self):
        return helper_func()

def standalone_task(items: List[str]) -> bool:
    return len(items) > 0

if __name__ == "__main__":
    p = Processor()
    p.run()
'''
    scanned = [
        ScannedFile(
            path=Path("src/my_package/processor.py"),
            relative_path="src/my_package/processor.py",
            extension=".py",
            size_bytes=len(code),
            content=code,
        ),
        ScannedFile(
            path=Path("src/my_package/utils.py"),
            relative_path="src/my_package/utils.py",
            extension=".py",
            size_bytes=50,
            content="def helper_func(): return True",
        ),
    ]

    analyzer = CodeAnalyzer(scanned)
    graph = analyzer.analyze()

    mod = graph.modules["src/my_package/processor.py"]
    assert "Processor" in mod.classes
    assert "standalone_task" in mod.functions
    assert mod.is_entry_point is True
    assert mod.entry_point_type == "main_block"
    assert mod.docstring == "Core processor module."

    # External dependency check
    assert "requests" in graph.external_dependencies
    assert "os" not in graph.external_dependencies
    assert "sys" not in graph.external_dependencies

    # Internal edge check: processor -> utils
    internal_targets = [tgt for src, tgt in graph.internal_edges if src == "src/my_package/processor.py"]
    assert "src/my_package/utils.py" in internal_targets


def test_cli_and_fastapi_entry_point_detection():
    """Verifies detection of Typer, Click, and FastAPI entry points."""
    cli_code = """
import typer

app = typer.Typer()

@app.command()
def deploy():
    print("Deploying...")
"""
    api_code = """
from fastapi import FastAPI

api = FastAPI()

@api.get("/health")
def health():
    return {"status": "ok"}
"""
    scanned = [
        ScannedFile(
            path=Path("cli.py"),
            relative_path="cli.py",
            extension=".py",
            size_bytes=len(cli_code),
            content=cli_code,
        ),
        ScannedFile(
            path=Path("api.py"),
            relative_path="api.py",
            extension=".py",
            size_bytes=len(api_code),
            content=api_code,
        ),
    ]

    analyzer = CodeAnalyzer(scanned)
    graph = analyzer.analyze()

    cli_mod = graph.modules["cli.py"]
    assert cli_mod.is_entry_point is True

    api_mod = graph.modules["api.py"]
    assert api_mod.is_entry_point is True

    assert len(graph.entry_points) >= 2


def test_mermaid_fallback_generation():
    """Verifies that to_mermaid_fallback outputs valid flowchart syntax."""
    scanned = [
        ScannedFile(
            path=Path("src/app.py"),
            relative_path="src/app.py",
            extension=".py",
            size_bytes=100,
            content="import src.core\nif __name__ == '__main__': pass",
        ),
        ScannedFile(
            path=Path("src/core.py"),
            relative_path="src/core.py",
            extension=".py",
            size_bytes=100,
            content="def execute(): return True",
        ),
    ]

    analyzer = CodeAnalyzer(scanned)
    graph = analyzer.analyze()
    mermaid_code = graph.to_mermaid_fallback()

    assert mermaid_code.startswith("flowchart TD")
    assert "subgraph" in mermaid_code
    assert "src_app" in mermaid_code
    assert "src_core" in mermaid_code
    assert "-->" in mermaid_code
