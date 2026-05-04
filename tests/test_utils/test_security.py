"""Tests for security utilities."""

import tempfile
from pathlib import Path


from code_agent.utils.security import SecurityChecker


class TestSecurityChecker:
    """Tests for SecurityChecker."""

    def test_safe_path(self):
        """Test checking a safe path."""
        is_safe, reason = SecurityChecker.is_path_safe("/tmp/test.txt")
        assert is_safe is True
        assert reason == ""

    def test_path_outside_base_dir(self):
        """Test path outside base directory."""
        with tempfile.TemporaryDirectory() as base:
            is_safe, reason = SecurityChecker.is_path_safe(
                "/etc/passwd", base_dir=base
            )
            assert is_safe is False
            assert "超出允许目录" in reason

    def test_sensitive_path(self):
        """Test accessing sensitive path."""
        is_safe, reason = SecurityChecker.is_path_safe("/etc/passwd")
        assert is_safe is False
        assert "敏感路径" in reason

    def test_safe_command(self):
        """Test checking a safe command."""
        is_safe, reason = SecurityChecker.is_command_safe("ls -la")
        assert is_safe is True
        assert reason == ""

    def test_blocked_command(self):
        """Test checking a blocked command."""
        is_safe, reason = SecurityChecker.is_command_safe("sudo apt update")
        assert is_safe is False
        assert "危险模式" in reason

    def test_rm_rf_command(self):
        """Test checking rm -rf command."""
        is_safe, reason = SecurityChecker.is_command_safe("rm -rf /")
        assert is_safe is False
        assert "危险模式" in reason

    def test_file_size_within_limit(self):
        """Test file size within limit."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"small content")
            path = f.name

        is_safe, reason = SecurityChecker.check_file_size(path, max_size=1024)
        assert is_safe is True

        Path(path).unlink()

    def test_file_size_exceeds_limit(self):
        """Test file size exceeds limit."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"x" * 2000)
            path = f.name

        is_safe, reason = SecurityChecker.check_file_size(path, max_size=1024)
        assert is_safe is False
        assert "超过上限" in reason

        Path(path).unlink()
