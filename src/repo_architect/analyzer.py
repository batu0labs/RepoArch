"""Static code analysis and dependency extraction.

Performs lightweight AST and regex-based parsing to extract entry points,
class/function definitions, module relationships, and dependency graphs
without executing untrusted code.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from repo_architect.scanner import ScannedFile


@dataclass
class ImportRelation:
    """Represents an imported module dependency."""

    source_path: str
    target_module: str
    imported_names: List[str] = field(default_factory=list)
    is_internal: bool = False
    is_standard_lib: bool = False


@dataclass
class EntryPoint:
    """Represents a discovered program entry point."""

    file_path: str
    entry_type: str  # e.g., 'main_block', 'cli_command', 'web_app', 'cli_script'
    identifier: str
    description: str


@dataclass
class ModuleInfo:
    """Static metadata for a single scanned module or file."""

    path: str
    module_name: str
    language: str
    lines_of_code: int
    classes: List[str] = field(default_factory=list)
    functions: List[str] = field(default_factory=list)
    imports: List[ImportRelation] = field(default_factory=list)
    is_entry_point: bool = False
    entry_point_type: Optional[str] = None
    docstring: Optional[str] = None


@dataclass
class DependencyGraph:
    """Graph of internal and external dependencies across the repository."""

    modules: Dict[str, ModuleInfo] = field(default_factory=dict)
    internal_edges: List[Tuple[str, str]] = field(default_factory=list)  # (source, target)
    external_dependencies: Set[str] = field(default_factory=set)
    entry_points: List[EntryPoint] = field(default_factory=list)
    clusters: Dict[str, List[str]] = field(default_factory=dict)

    def get_summary_prompt(self, max_tokens_approx: int = 4000) -> str:
        """Constructs a concise architectural summary for LLM prompt ingestion."""
        lines = []
        lines.append(f"Repository Overview: {len(self.modules)} source modules mapped.")
        lines.append("")

        if self.entry_points:
            lines.append("Detected Entry Points:")
            for ep in self.entry_points:
                lines.append(f"  - [{ep.entry_type}] {ep.file_path}: {ep.identifier} ({ep.description})")
            lines.append("")

        if self.external_dependencies:
            lines.append(f"Key External Dependencies: {', '.join(sorted(self.external_dependencies)[:25])}")
            lines.append("")

        lines.append("Module Catalog & Architecture Details:")
        for path, mod in sorted(self.modules.items()):
            classes_str = f", Classes: [{', '.join(mod.classes[:5])}]" if mod.classes else ""
            funcs_str = f", Functions: [{', '.join(mod.functions[:7])}]" if mod.functions else ""
            internal_imports = [imp.target_module for imp in mod.imports if imp.is_internal]
            imp_str = f", Imports: [{', '.join(internal_imports[:6])}]" if internal_imports else ""

            doc_snippet = f" - {mod.docstring.splitlines()[0]}" if mod.docstring else ""
            lines.append(f"  * {path} ({mod.language}, {mod.lines_of_code} LOC){classes_str}{funcs_str}{imp_str}{doc_snippet}")

        if self.internal_edges:
            lines.append("")
            lines.append("Internal Dependency Flow (Top connections):")
            for src, tgt in sorted(set(self.internal_edges))[:30]:
                lines.append(f"  - {src} -> {tgt}")

        summary = "\n".join(lines)
        return summary[:max_tokens_approx * 4]

    def to_mermaid_fallback(self) -> str:
        """Generates a deterministic, valid Mermaid flowchart without LLM inference."""
        lines = ["flowchart TD"]
        lines.append("    %% Auto-generated deterministic architecture flowchart")
        lines.append("    classDef entryPoint fill:#4CAF50,stroke:#2E7D32,color:#ffffff,stroke-width:2px;")
        lines.append("    classDef internal fill:#2196F3,stroke:#1565C0,color:#ffffff,stroke-width:1px;")
        lines.append("    classDef external fill:#9E9E9E,stroke:#616161,color:#ffffff,stroke-width:1px;")

        # Helper to format safe Mermaid node IDs
        def safe_id(val: str) -> str:
            cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", val).strip("_")
            return cleaned or "node"

        node_ids: Dict[str, str] = {}
        for path in self.modules:
            node_ids[path] = safe_id(path)

        # Cluster by directory if multiple exist
        dir_clusters: Dict[str, List[str]] = {}
        for path in self.modules:
            p = Path(path)
            parent = str(p.parent).replace("\\", "/")
            if parent in ("", "."):
                parent = "root"
            dir_clusters.setdefault(parent, []).append(path)

        for cluster_name, paths in sorted(dir_clusters.items()):
            cluster_id = safe_id(f"sub_{cluster_name}")
            lines.append(f"    subgraph {cluster_id} [\"{cluster_name.capitalize()}\"]")
            for path in paths:
                mod = self.modules[path]
                nid = node_ids[path]
                display_name = Path(path).name
                if mod.is_entry_point:
                    lines.append(f"        {nid}([\"{display_name} (Entry)\"]):::entryPoint")
                else:
                    lines.append(f"        {nid}[\"{display_name}\"]:::internal")
            lines.append("    end")

        # Add edges
        added_edges = set()
        for src, tgt in self.internal_edges:
            src_id = node_ids.get(src)
            tgt_id = node_ids.get(tgt)
            if src_id and tgt_id and src_id != tgt_id:
                edge_key = (src_id, tgt_id)
                if edge_key not in added_edges:
                    added_edges.add(edge_key)
                    lines.append(f"    {src_id} --> {tgt_id}")

        # If external dependencies exist, show top 5
        if self.external_dependencies:
            lines.append("    subgraph ext_deps [\"External Dependencies\"]")
            for dep in sorted(self.external_dependencies)[:5]:
                dep_id = safe_id(f"ext_{dep}")
                lines.append(f"        {dep_id}[\"{dep}\"]:::external")
            lines.append("    end")

        return "\n".join(lines)


# Standard Python library module names to filter out of external package lists
STD_LIB_MODULES: Set[str] = {
    "__future__", "abc", "argparse", "ast", "asyncio", "base64", "collections", "contextlib",
    "copy", "csv", "dataclasses", "datetime", "enum", "fnmatch", "functools",
    "gc", "glob", "hashlib", "http", "importlib", "inspect", "io", "itertools",
    "json", "logging", "math", "multiprocessing", "os", "pathlib", "pickle",
    "platform", "pprint", "queue", "random", "re", "shutil", "signal", "socket",
    "sqlite3", "ssl", "stat", "string", "subprocess", "sys", "tempfile",
    "threading", "time", "traceback", "types", "typing", "unittest", "urllib",
    "uuid", "warnings", "weakref", "zipfile"
}


class CodeAnalyzer:
    """Extracts architectural elements and dependencies from scanned files."""

    def __init__(self, scanned_files: List[ScannedFile]):
        self.scanned_files = scanned_files
        self.known_files: Set[str] = {f.relative_path for f in scanned_files}
        self.known_modules: Dict[str, str] = {}  # module dot-path -> relative_path

        for f in scanned_files:
            rel = f.relative_path
            # Build dotted import candidates: src/repo_architect/cli.py -> repo_architect.cli, cli
            stem = str(Path(rel).with_suffix("")).replace("/", ".").replace("\\", ".")
            self.known_modules[stem] = rel
            parts = stem.split(".")
            for i in range(len(parts)):
                sub = ".".join(parts[i:])
                if sub not in self.known_modules:
                    self.known_modules[sub] = rel

    def analyze(self) -> DependencyGraph:
        """Executes static analysis across all files and builds a DependencyGraph."""
        graph = DependencyGraph()

        for file_obj in self.scanned_files:
            ext = file_obj.extension
            if ext == ".py":
                mod_info = self._analyze_python_file(file_obj)
            elif ext in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
                mod_info = self._analyze_js_ts_file(file_obj)
            else:
                mod_info = self._analyze_generic_file(file_obj)

            graph.modules[file_obj.relative_path] = mod_info

            if mod_info.is_entry_point:
                graph.entry_points.append(
                    EntryPoint(
                        file_path=file_obj.relative_path,
                        entry_type=mod_info.entry_point_type or "entry_point",
                        identifier=mod_info.module_name,
                        description=(mod_info.docstring.splitlines()[0] if mod_info.docstring else f"Executable entry point in {file_obj.relative_path}"),
                    )
                )

        # Build internal edges and extract external dependencies
        for mod_path, mod in graph.modules.items():
            for imp in mod.imports:
                if imp.is_internal:
                    resolved_target = self._resolve_internal_import(mod_path, imp.target_module)
                    if resolved_target and resolved_target != mod_path:
                        graph.internal_edges.append((mod_path, resolved_target))
                elif not imp.is_standard_lib and imp.target_module:
                    root_pkg = imp.target_module.split(".")[0]
                    if root_pkg and root_pkg not in STD_LIB_MODULES:
                        graph.external_dependencies.add(root_pkg)

        # Cluster modules by folder / responsibility
        for path in graph.modules:
            parts = Path(path).parts
            cluster_name = parts[0] if len(parts) > 1 else "root"
            graph.clusters.setdefault(cluster_name, []).append(path)

        return graph

    def _resolve_internal_import(self, source_path: str, target_module: str) -> Optional[str]:
        """Resolves target import dot-path to relative source file path."""
        # 1. Exact match in known_modules
        if target_module in self.known_modules:
            return self.known_modules[target_module]

        # 2. Relative file sibling resolution
        src_dir = Path(source_path).parent
        candidate = (src_dir / f"{target_module}.py").as_posix()
        if candidate in self.known_files:
            return candidate

        candidate_init = (src_dir / target_module / "__init__.py").as_posix()
        if candidate_init in self.known_files:
            return candidate_init

        # 3. Suffix match
        for stem, path in self.known_modules.items():
            if stem.endswith("." + target_module) or stem == target_module:
                return path

        return None

    def _analyze_python_file(self, file_obj: ScannedFile) -> ModuleInfo:
        """Extracts AST nodes from Python source code."""
        code = file_obj.content
        lines_count = len(code.splitlines())
        classes: List[str] = []
        functions: List[str] = []
        imports: List[ImportRelation] = []
        is_entry_point = False
        entry_type: Optional[str] = None
        docstring: Optional[str] = None

        try:
            tree = ast.parse(code, filename=file_obj.relative_path)
        except SyntaxError:
            # Fallback regex parsing if invalid Python syntax (e.g. template or dialect)
            return self._analyze_python_fallback(file_obj)

        docstring = ast.get_docstring(tree)

        for node in ast.walk(tree):
            # Class definitions
            if isinstance(node, ast.ClassDef):
                classes.append(node.name)

            # Function definitions
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(node.name)
                # Check for CLI command decorators: @app.command(), @cli.command()
                for dec in node.decorator_list:
                    dec_str = ""
                    if isinstance(dec, ast.Call):
                        if isinstance(dec.func, ast.Attribute):
                            dec_str = dec.func.attr
                        elif isinstance(dec.func, ast.Name):
                            dec_str = dec.func.id
                    elif isinstance(dec, ast.Attribute):
                        dec_str = dec.attr
                    if dec_str in {"command", "cli", "get", "post", "put", "delete", "route"}:
                        is_entry_point = True
                        entry_type = f"handler_{dec_str}"

            # Import statements: import x, y
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    target = alias.name
                    is_int, is_std = self._classify_import(target)
                    imports.append(
                        ImportRelation(
                            source_path=file_obj.relative_path,
                            target_module=target,
                            imported_names=[alias.asname or alias.name],
                            is_internal=is_int,
                            is_standard_lib=is_std,
                        )
                    )

            # Import-from statements: from x import y
            elif isinstance(node, ast.ImportFrom):
                target = node.module or ""
                # Handle relative imports (.scanner)
                if node.level > 0:
                    target = "." * node.level + target
                    is_int, is_std = True, False
                else:
                    is_int, is_std = self._classify_import(target)

                names = [alias.name for alias in node.names]
                imports.append(
                    ImportRelation(
                        source_path=file_obj.relative_path,
                        target_module=target,
                        imported_names=names,
                        is_internal=is_int,
                        is_standard_lib=is_std,
                    )
                )

            # Detect if __name__ == '__main__':
            elif isinstance(node, ast.If):
                if self._is_main_check(node.test):
                    is_entry_point = True
                    entry_type = "main_block"

            # Detect Typer, FastAPI, Click, Flask instances
            elif isinstance(node, ast.Assign):
                if isinstance(node.value, ast.Call):
                    func_id = ""
                    if isinstance(node.value.func, ast.Name):
                        func_id = node.value.func.id
                    elif isinstance(node.value.func, ast.Attribute):
                        func_id = node.value.func.attr
                    if func_id in {"Typer", "FastAPI", "Flask", "ArgumentParser"}:
                        is_entry_point = True
                        entry_type = f"app_{func_id.lower()}"

        # Deduplicate
        classes = list(dict.fromkeys(classes))
        functions = list(dict.fromkeys(functions))

        return ModuleInfo(
            path=file_obj.relative_path,
            module_name=Path(file_obj.relative_path).stem,
            language="Python",
            lines_of_code=lines_count,
            classes=classes,
            functions=functions,
            imports=imports,
            is_entry_point=is_entry_point,
            entry_point_type=entry_type,
            docstring=docstring,
        )

    def _is_main_check(self, node: ast.AST) -> bool:
        """Checks if an AST expression matches `__name__ == '__main__'."""
        if not isinstance(node, ast.Compare):
            return False
        if not (isinstance(node.left, ast.Name) and node.left.id == "__name__"):
            return False
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant) and comparator.value == "__main__":
                return True
        return False

    def _classify_import(self, module_name: str) -> Tuple[bool, bool]:
        """Classifies an import as (is_internal, is_standard_library)."""
        root_name = module_name.split(".")[0].lstrip(".")
        if not root_name:
            return True, False

        if root_name in STD_LIB_MODULES:
            return False, True

        # Check if root_name matches any internal file or directory
        for stem in self.known_modules:
            if stem == root_name or stem.startswith(root_name + "."):
                return True, False

        return False, False

    def _analyze_python_fallback(self, file_obj: ScannedFile) -> ModuleInfo:
        """Regex fallback for Python when AST parsing fails."""
        lines = file_obj.content.splitlines()
        classes = re.findall(r"^class\s+([A-Za-z0-9_]+)", file_obj.content, re.MULTILINE)
        functions = re.findall(r"^(?:async\s+)?def\s+([A-Za-z0-9_]+)", file_obj.content, re.MULTILINE)
        is_entry = "__name__ == '__main__'" in file_obj.content or '__name__ == "__main__"' in file_obj.content

        return ModuleInfo(
            path=file_obj.relative_path,
            module_name=Path(file_obj.relative_path).stem,
            language="Python",
            lines_of_code=len(lines),
            classes=classes,
            functions=functions,
            imports=[],
            is_entry_point=is_entry,
            entry_point_type="main_block" if is_entry else None,
        )

    def _analyze_js_ts_file(self, file_obj: ScannedFile) -> ModuleInfo:
        """Lightweight regex analysis for JavaScript and TypeScript."""
        code = file_obj.content
        lines = code.splitlines()
        classes = re.findall(r"\bclass\s+([A-Za-z0-9_]+)", code)
        functions = re.findall(r"\bfunction\s+([A-Za-z0-9_]+)", code)
        arrow_funcs = re.findall(r"\b(?:const|let|var)\s+([A-Za-z0-9_]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>", code)
        functions.extend(arrow_funcs)

        is_entry = False
        filename = Path(file_obj.relative_path).name.lower()
        if filename in {"index.js", "index.ts", "main.js", "main.ts", "app.js", "app.ts", "server.js", "server.ts"}:
            is_entry = True

        imports: List[ImportRelation] = []
        # Match import ... from '...'
        es_imports = re.findall(r"""import\s+(?:[\w\s{},*]+)\s+from\s+['"]([^'"]+)['"]""", code)
        for target in es_imports:
            is_int = target.startswith("./") or target.startswith("../")
            imports.append(
                ImportRelation(
                    source_path=file_obj.relative_path,
                    target_module=target,
                    is_internal=is_int,
                    is_standard_lib=False,
                )
            )

        return ModuleInfo(
            path=file_obj.relative_path,
            module_name=Path(file_obj.relative_path).stem,
            language="JavaScript/TypeScript",
            lines_of_code=len(lines),
            classes=classes,
            functions=functions,
            imports=imports,
            is_entry_point=is_entry,
            entry_point_type="web_app_entry" if is_entry else None,
        )

    def _analyze_generic_file(self, file_obj: ScannedFile) -> ModuleInfo:
        """Generic module info for documentation, configs, or other source types."""
        lines = file_obj.content.splitlines()
        return ModuleInfo(
            path=file_obj.relative_path,
            module_name=Path(file_obj.relative_path).stem,
            language=file_obj.extension.lstrip(".").upper() or "Text",
            lines_of_code=len(lines),
        )
