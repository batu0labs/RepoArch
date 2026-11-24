"""Unit tests for scanner.py: verifying privacy guard exclusions and secret masking."""

import tempfile
from pathlib import Path

from repo_architect.scanner import (
    PrivacyGuard,
    sanitize_secrets,
    scan_repository,
)


def test_strict_exclusion_directories():
    """Verifies that .git, node_modules, .venv, and __pycache__ are strictly excluded."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        guard = PrivacyGuard(root)

        # Create directories
        git_dir = root / ".git"
        node_dir = root / "node_modules"
        venv_dir = root / ".venv"
        cache_dir = root / "__pycache__"
        src_dir = root / "src"

        for d in [git_dir, node_dir, venv_dir, cache_dir, src_dir]:
            d.mkdir(parents=True, exist_ok=True)

        assert guard.check_exclusion(git_dir)[0] is True
        assert guard.check_exclusion(node_dir)[0] is True
        assert guard.check_exclusion(venv_dir)[0] is True
        assert guard.check_exclusion(cache_dir)[0] is True
        assert guard.check_exclusion(src_dir)[0] is False

        # Files inside strictly excluded folders must also be excluded
        git_file = git_dir / "config"
        git_file.write_text("dummy")
        assert guard.check_exclusion(git_file)[0] is True

        node_file = node_dir / "package.json"
        node_file.write_text("{}")
        assert guard.check_exclusion(node_file)[0] is True


def test_strict_exclusion_file_patterns():
    """Verifies that .env*, *.pem, *.key, and credentials files are strictly excluded."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        guard = PrivacyGuard(root)

        sensitive_files = [
            root / ".env",
            root / ".env.local",
            root / ".env.production",
            root / "prod.env",
            root / "server.pem",
            root / "id_rsa.key",
            root / "client.crt",
            root / "id_rsa",
            root / "id_ed25519",
            root / "service_account.json",
            root / "database.secret",
        ]

        for sf in sensitive_files:
            sf.write_text("secret")
            is_excluded, reason = guard.check_exclusion(sf)
            assert is_excluded is True, f"File {sf.name} was not excluded!"
            assert reason == "strict_file_pattern"

        # Safe files should pass
        safe_file = root / "main.py"
        safe_file.write_text("print('hello')")
        is_excluded, _ = guard.check_exclusion(safe_file)
        assert is_excluded is False


def test_gitignore_rule_parsing():
    """Verifies that rules in target repository's .gitignore are respected."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        gitignore = root / ".gitignore"
        gitignore.write_text("temp_output/\n*.log\ncustom_build/\n")

        guard = PrivacyGuard(root)

        log_file = root / "app.log"
        log_file.write_text("log data")
        assert guard.check_exclusion(log_file)[0] is True

        temp_dir = root / "temp_output"
        temp_dir.mkdir()
        temp_file = temp_dir / "data.txt"
        temp_file.write_text("temporary")
        assert guard.check_exclusion(temp_file)[0] is True

        regular_file = root / "app.py"
        regular_file.write_text("print(1)")
        assert guard.check_exclusion(regular_file)[0] is False


def test_secret_sanitization_patterns():
    """Verifies regex masking of API keys, tokens, passwords, and connection strings."""
    sample_text = """
    # Hardcoded configs
    api_key = "sk-1234567890abcdef1234567890"
    password = "MySuperSecretPassword123"
    auth_token: "xyz9876543210token"
    
    # Cloud tokens
    github_pat = "ghp_1234567890abcdef1234567890abcdef1234"
    aws_access = "AKIAIOSFODNN7EXAMPLE"
    slack_token = "xoxb-1234567890-abcdefghijkl"
    
    # DB URI
    database_url = "postgres://admin:topsecretpass@db.internal:5432/production"
    
    # Private Key
    key = '''-----BEGIN RSA PRIVATE KEY-----
    MIIEowIBAAKCAQEA0Y...
    -----END RSA PRIVATE KEY-----'''
    """

    sanitized, count = sanitize_secrets(sample_text)

    # Assertions
    assert "sk-1234567890" not in sanitized
    assert "MySuperSecretPassword123" not in sanitized
    assert "ghp_1234567890" not in sanitized
    assert "AKIAIOSFODNN7EXAMPLE" not in sanitized
    assert "topsecretpass" not in sanitized
    assert "-----BEGIN RSA PRIVATE KEY-----" not in sanitized
    assert "[REDACTED_SECRET]" in sanitized or "[REDACTED_OPENAI_KEY]" in sanitized
    assert "[REDACTED_PASSWORD]" in sanitized
    assert "[REDACTED_PRIVATE_KEY_BLOCK]" in sanitized
    assert count >= 6


def test_scan_repository_end_to_end():
    """Verifies end-to-end repository traversal, exclusions, and statistics."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)

        # Valid source
        (root / "src").mkdir()
        main_py = root / "src" / "main.py"
        main_py.write_text("import os\napi_key = 'sk-proj-testkey12345678901234567890'\nprint('ready')")

        # Excluded items
        (root / ".git").mkdir()
        (root / ".git" / "HEAD").write_text("ref: refs/heads/main")

        (root / ".env").write_text("SECRET_KEY=123456")
        (root / "id_rsa.key").write_text("private key")

        # Binary
        (root / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        files, stats = scan_repository(root)

        assert stats.files_indexed == 1
        assert files[0].relative_path.replace("\\", "/") == "src/main.py"
        assert stats.secrets_masked >= 1
        assert "[REDACTED" in files[0].content
        assert stats.files_excluded >= 3
