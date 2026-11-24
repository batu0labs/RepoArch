"""Privacy-focused repository scanner and secret sanitizer.

Strictly excludes sensitive files (.git, .env*, *.pem, *.key, node_modules,
virtualenvs, etc.) and repository .gitignore rules. Sanitizes potential secrets
before code snippets or metadata reach the local model.
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pathspec


# Strict privacy exclusion directories - unconditionally ignored
STRICT_EXCLUDED_DIRS: Set[str] = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    ".venv",
    "venv",
    "env",
    "ENV",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".cache",
    ".idea",
    ".vscode",
    "dist",
    "build",
    ".eggs",
    "*.egg-info",
}

# Strict privacy exclusion file patterns - unconditionally ignored
STRICT_EXCLUDED_FILE_PATTERNS: List[str] = [
    ".env*",
    "*.env",
    "*.pem",
    "*.key",
    "*.crt",
    "*.pfx",
    "*.p12",
    "*.jks",
    "*.id_rsa",
    "id_rsa*",
    "id_ed25519*",
    "credentials.json",
    "service_account*.json",
    "service-account*.json",
    "*.secret",
    "*.secrets",
]

# Binary and non-source extensions to ignore
BINARY_EXTENSIONS: Set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".tar", ".gz", ".7z", ".rar", ".bz2", ".xz",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".iso", ".dmg",
    ".pyc", ".pyo", ".pyd", ".whl", ".egg",
    ".db", ".sqlite", ".sqlite3", ".parquet", ".feather", ".pkl", ".pickle",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv",
}

# Secret sanitization patterns
SECRET_PATTERNS: List[Tuple[str, re.Pattern[str], str]] = [
    # Assignment of sensitive keys/tokens/passwords: key = "value"
    (
        "assignment",
        re.compile(
            r"""(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|secret[_-]?key|secret|password|passwd|private[_-]?key|client[_-]?secret)\b\s*([:=])\s*['"]([^'"]{4,})['"]""",
            re.MULTILINE,
        ),
        r'\1\2"[REDACTED_SECRET]"',
    ),
    # OpenAI API Keys
    (
        "openai_key",
        re.compile(r"\b(sk-[A-Za-z0-9_-]{20,})\b"),
        "[REDACTED_OPENAI_KEY]",
    ),
    # GitHub Tokens
    (
        "github_token",
        re.compile(r"\b((?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{30,})\b"),
        "[REDACTED_GITHUB_TOKEN]",
    ),
    # AWS Access Key IDs
    (
        "aws_key",
        re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
        "[REDACTED_AWS_KEY_ID]",
    ),
    # Slack Tokens
    (
        "slack_token",
        re.compile(r"\b(xox[baprs]-[0-9A-Za-z-]{10,})\b"),
        "[REDACTED_SLACK_TOKEN]",
    ),
    # JSON Web Tokens (JWT)
    (
        "jwt",
        re.compile(r"\b(eyJ[a-zA-Z0-9_-]{8,}\.eyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,})\b"),
        "[REDACTED_JWT]",
    ),
    # Database connection strings with credentials: postgres://user:pass@host
    (
        "db_conn",
        re.compile(r"""(?i)(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://([^:\s]+):([^@\s]+)@"""),
        r"\1://\2:[REDACTED_PASSWORD]@",
    ),
    # Private Key blocks
    (
        "private_key_block",
        re.compile(r"-----BEGIN (?:[A-Z0-9_-]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9_-]+ )?PRIVATE KEY-----"),
        "[REDACTED_PRIVATE_KEY_BLOCK]",
    ),
]


@dataclass
class ScannedFile:
    """Represents an indexed source file with sanitized contents."""

    path: Path
    relative_path: str
    extension: str
    size_bytes: int
    content: str
    secrets_masked_count: int = 0


@dataclass
class ScanStats:
    """Summary statistics of a repository scan."""

    total_files_visited: int = 0
    files_indexed: int = 0
    files_excluded: int = 0
    secrets_masked: int = 0
    excluded_reasons: Dict[str, int] = field(default_factory=lambda: {
        "strict_dir": 0,
        "strict_file_pattern": 0,
        "gitignore": 0,
        "binary_extension": 0,
        "read_error": 0,
        "size_limit": 0,
    })


def sanitize_secrets(text: str) -> Tuple[str, int]:
    """Applies lightweight regex filters to mask hardcoded keys and credentials.

    Args:
        text: Raw file content or code snippet.

    Returns:
        Tuple containing the sanitized string and the total number of masked occurrences.
    """
    total_masked = 0
    sanitized = text

    for _name, pattern, replacement in SECRET_PATTERNS:
        matches = len(pattern.findall(sanitized))
        if matches > 0:
            total_masked += matches
            sanitized = pattern.sub(replacement, sanitized)

    return sanitized, total_masked


class PrivacyGuard:
    """Enforces strict file exclusion and repository .gitignore rules."""

    def __init__(self, root_path: Path):
        self.root_path = root_path.resolve()
        self.gitignore_spec = self._load_gitignore_spec()

    def _load_gitignore_spec(self) -> Optional[pathspec.PathSpec]:
        """Loads and compiles .gitignore from the root directory if present."""
        gitignore_path = self.root_path / ".gitignore"
        if not gitignore_path.is_file():
            return None

        try:
            with open(gitignore_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
            try:
                return pathspec.PathSpec.from_lines("gitignore", lines)
            except Exception:
                return pathspec.PathSpec.from_lines("gitwildmatch", lines)
        except Exception:
            return None

    def check_exclusion(self, path: Path) -> Tuple[bool, Optional[str]]:
        """Determines if a given path must be excluded.

        Args:
            path: Target file or directory path.

        Returns:
            Tuple of (is_excluded: bool, reason: Optional[str]).
        """
        resolved = path.resolve()
        try:
            rel_path = resolved.relative_to(self.root_path)
            rel_str = str(rel_path).replace("\\", "/")
        except ValueError:
            rel_str = resolved.name

        # 1. Check directory components for strict exclusions
        parts = rel_str.split("/")
        for part in parts[:-1]:
            for strict_dir in STRICT_EXCLUDED_DIRS:
                if fnmatch.fnmatch(part, strict_dir):
                    return True, "strict_dir"

        # If it's a directory being checked directly
        if resolved.is_dir():
            dir_name = resolved.name
            for strict_dir in STRICT_EXCLUDED_DIRS:
                if fnmatch.fnmatch(dir_name, strict_dir):
                    return True, "strict_dir"
            if self.gitignore_spec and (self.gitignore_spec.match_file(rel_str + "/") or self.gitignore_spec.match_file(rel_str)):
                return True, "gitignore"
            return False, None

        # 2. Check filename against strict patterns (.env*, *.pem, *.key, etc.)
        filename = resolved.name
        for pattern in STRICT_EXCLUDED_FILE_PATTERNS:
            if fnmatch.fnmatch(filename, pattern):
                return True, "strict_file_pattern"

        # 3. Check for binary extensions
        ext = resolved.suffix.lower()
        if ext in BINARY_EXTENSIONS:
            return True, "binary_extension"

        # 4. Check repository .gitignore
        if self.gitignore_spec:
            if self.gitignore_spec.match_file(rel_str):
                return True, "gitignore"

        return False, None


def scan_repository(
    target_path: Path,
    max_file_size_bytes: int = 500_000,
) -> Tuple[List[ScannedFile], ScanStats]:
    """Scans a repository tree with strict privacy guardrails and secret masking.

    Args:
        target_path: Path to the target repository.
        max_file_size_bytes: Skip files larger than this threshold (default 500KB).

    Returns:
        Tuple of (list of indexed ScannedFile objects, ScanStats summary).
    """
    root = target_path.resolve()
    guard = PrivacyGuard(root)
    stats = ScanStats()
    scanned_files: List[ScannedFile] = []

    if not root.exists():
        return scanned_files, stats

    for dirpath, dirnames, filenames in os.walk(root):
        current_dir = Path(dirpath)

        # Prune excluded directories in-place to avoid descending into them
        pruned_dirs = []
        for d in dirnames:
            dir_full = current_dir / d
            is_excluded, reason = guard.check_exclusion(dir_full)
            if is_excluded:
                stats.files_excluded += 1
                if reason:
                    stats.excluded_reasons[reason] = stats.excluded_reasons.get(reason, 0) + 1
            else:
                pruned_dirs.append(d)
        dirnames[:] = pruned_dirs

        for f in filenames:
            stats.total_files_visited += 1
            file_full = current_dir / f

            is_excluded, reason = guard.check_exclusion(file_full)
            if is_excluded:
                stats.files_excluded += 1
                if reason:
                    stats.excluded_reasons[reason] = stats.excluded_reasons.get(reason, 0) + 1
                continue

            # Check file size
            try:
                size = file_full.stat().st_size
                if size > max_file_size_bytes:
                    stats.files_excluded += 1
                    stats.excluded_reasons["size_limit"] = stats.excluded_reasons.get("size_limit", 0) + 1
                    continue
            except OSError:
                stats.files_excluded += 1
                stats.excluded_reasons["read_error"] = stats.excluded_reasons.get("read_error", 0) + 1
                continue

            # Read content with privacy sanitization
            try:
                with open(file_full, "r", encoding="utf-8", errors="replace") as file_obj:
                    raw_content = file_obj.read()
            except Exception:
                stats.files_excluded += 1
                stats.excluded_reasons["read_error"] = stats.excluded_reasons.get("read_error", 0) + 1
                continue

            sanitized_content, masked_count = sanitize_secrets(raw_content)
            stats.secrets_masked += masked_count

            rel_path = str(file_full.relative_to(root)).replace("\\", "/")
            scanned_files.append(
                ScannedFile(
                    path=file_full,
                    relative_path=rel_path,
                    extension=file_full.suffix.lower(),
                    size_bytes=size,
                    content=sanitized_content,
                    secrets_masked_count=masked_count,
                )
            )
            stats.files_indexed += 1

    return scanned_files, stats
