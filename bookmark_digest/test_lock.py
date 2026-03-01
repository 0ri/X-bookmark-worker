"""
Tests for the run lock module.
"""

import os
import signal
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from .lock import RunLock, RunLockError


class TestRunLock:
    """Tests for RunLock context manager."""
    
    def test_lock_acquires_and_releases(self):
        """Test that lock can be acquired and released."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Should acquire without error
            with RunLock(tmpdir):
                lock_path = Path(tmpdir) / ".run.lock"
                assert lock_path.exists(), "Lock file should exist while locked"
            
            # Lock file should be cleaned up after exit
            assert not lock_path.exists(), "Lock file should be removed after release"
    
    def test_concurrent_lock_raises_error(self):
        """Test that second lock attempt raises RunLockError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with RunLock(tmpdir):
                # Try to acquire again while first lock is held
                with pytest.raises(RunLockError) as exc_info:
                    with RunLock(tmpdir):
                        pass
                
                # Error message should mention that another run is in progress
                assert "Another bookmark-digest run is in progress" in str(exc_info.value)
    
    def test_lock_file_cleaned_up_after_context_exit(self):
        """Test that lock file is removed even if exception occurs in context."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / ".run.lock"
            
            # Lock file should not exist initially
            assert not lock_path.exists()
            
            # Acquire and release normally
            with RunLock(tmpdir):
                assert lock_path.exists()
            
            assert not lock_path.exists()
            
            # Acquire and raise exception
            try:
                with RunLock(tmpdir):
                    assert lock_path.exists()
                    raise ValueError("Test exception")
            except ValueError:
                pass
            
            # Lock file should still be cleaned up
            assert not lock_path.exists()
    
    def test_stale_lock_auto_cleared(self):
        """Test that lock from dead process is automatically cleared."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / ".run.lock"
            
            # Create a fake stale lock with non-existent PID
            # Use a PID that's very unlikely to exist
            fake_pid = 999999
            
            # Manually create lock file with fake PID
            lock_path.write_text(f"{fake_pid}\n")
            
            # Mock _is_pid_alive to return False for our fake PID
            with patch('bookmark_digest.lock.RunLock._is_pid_alive', return_value=False):
                # Should successfully acquire after clearing stale lock
                with RunLock(tmpdir):
                    # Lock should be held by current process now
                    assert lock_path.exists()
                    content = lock_path.read_text()
                    current_pid = os.getpid()
                    assert str(current_pid) in content
    
    def test_lock_writes_current_pid(self):
        """Test that lock file contains current process PID."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lock_path = Path(tmpdir) / ".run.lock"
            current_pid = os.getpid()
            
            with RunLock(tmpdir):
                assert lock_path.exists()
                content = lock_path.read_text()
                assert str(current_pid) in content
    
    def test_lock_error_includes_pid(self):
        """Test that RunLockError includes PID of lock holder."""
        with tempfile.TemporaryDirectory() as tmpdir:
            current_pid = os.getpid()
            
            with RunLock(tmpdir):
                # Try to acquire again
                with pytest.raises(RunLockError) as exc_info:
                    with RunLock(tmpdir):
                        pass
                
                # Error should include PID
                error_msg = str(exc_info.value)
                assert "PID:" in error_msg or str(current_pid) in error_msg
    
    def test_lock_creates_data_dir_if_missing(self):
        """Test that lock creates data directory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Use a non-existent subdirectory
            data_dir = Path(tmpdir) / "data"
            assert not data_dir.exists()
            
            with RunLock(str(data_dir)):
                # Directory should be created
                assert data_dir.exists()
                assert data_dir.is_dir()
