"""
Run lock to prevent concurrent bookmark-digest execution.

Prevents overlapping cron jobs and manual invocations from causing
race conditions on the queue database.
"""

import fcntl
import logging
import os
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


class RunLockError(Exception):
    """Raised when lock cannot be acquired."""
    pass


class RunLock:
    """
    File-based lock with stale PID detection.
    
    Usage:
        with RunLock(data_dir):
            # Do work
            pass
    
    Raises:
        RunLockError: If another process holds the lock
    """
    
    def __init__(self, data_dir: str):
        """
        Initialize lock.
        
        Args:
            data_dir: Directory for lock file (usually 'data/')
        """
        self.data_dir = Path(data_dir)
        self.lock_path = self.data_dir / ".run.lock"
        self.fd: Optional[int] = None
        self.pid = os.getpid()
        
    def __enter__(self):
        """Acquire lock on context entry."""
        # Ensure data directory exists
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Open lock file (create if doesn't exist)
        self.fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        
        try:
            # Try to acquire exclusive lock (non-blocking)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError) as e:
            # Lock is held by another process
            # Read the PID to report it
            existing_pid = self._read_pid()
            
            # Check if that PID is still alive
            if existing_pid and not self._is_pid_alive(existing_pid):
                logger.warning(
                    "Stale lock found (PID %d not running), clearing...",
                    existing_pid
                )
                # Close our FD and remove stale lock file
                os.close(self.fd)
                self.fd = None
                try:
                    os.remove(self.lock_path)
                except OSError:
                    pass
                
                # Retry acquisition
                self.fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o644)
                try:
                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (IOError, OSError):
                    os.close(self.fd)
                    self.fd = None
                    raise RunLockError(
                        "Another bookmark-digest run is in progress (stale lock retry failed)"
                    )
            else:
                # Lock is held by a living process
                os.close(self.fd)
                self.fd = None
                pid_msg = f"PID: {existing_pid}" if existing_pid else "unknown PID"
                raise RunLockError(
                    f"Another bookmark-digest run is in progress ({pid_msg})"
                )
        
        # Write our PID to the lock file
        os.ftruncate(self.fd, 0)
        os.lseek(self.fd, 0, os.SEEK_SET)
        os.write(self.fd, f"{self.pid}\n".encode())
        os.fsync(self.fd)
        
        logger.debug("Lock acquired (PID %d)", self.pid)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Release lock on context exit."""
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
                os.close(self.fd)
                logger.debug("Lock released (PID %d)", self.pid)
            except Exception as e:
                logger.error("Error releasing lock: %s", e)
            finally:
                self.fd = None
        
        # Clean up lock file
        try:
            if self.lock_path.exists():
                os.remove(self.lock_path)
        except OSError as e:
            logger.debug("Could not remove lock file: %s", e)
        
        return False  # Don't suppress exceptions
    
    def _read_pid(self) -> Optional[int]:
        """Read PID from lock file."""
        try:
            os.lseek(self.fd, 0, os.SEEK_SET)
            pid_bytes = os.read(self.fd, 32)
            if pid_bytes:
                return int(pid_bytes.decode().strip())
        except (ValueError, OSError):
            pass
        return None
    
    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        """Check if a PID is currently running."""
        try:
            # Send signal 0 (null signal) to check if process exists
            os.kill(pid, 0)
            return True
        except OSError:
            return False
