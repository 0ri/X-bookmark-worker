#!/usr/bin/env python3
"""Shared bird CLI utilities for bookmark-digest."""

import logging
import os
import shutil
import subprocess
import time

logger = logging.getLogger(__name__)

# Default: try env var, then PATH, then bare name
_DEFAULT_BIRD_CLI = os.environ.get("BIRD_CLI") or shutil.which("bird") or "bird"


def run_bird(
    args: list[str],
    timeout: int = 30,
    retry: bool = True,
    bird_cli: str | None = None,
) -> str | None:
    """Run bird CLI with retry and exponential backoff.

    Args:
        args: Arguments to pass to bird CLI
        timeout: Timeout in seconds
        retry: Whether to retry on failure
        bird_cli: Path to bird CLI executable (default: auto-detected)

    Returns:
        stdout string on success, None on failure
    """
    cli = bird_cli or _DEFAULT_BIRD_CLI
    cmd = [cli] + args
    max_attempts = 3 if retry else 1

    for attempt in range(max_attempts):
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            if result.returncode == 0:
                return result.stdout
            logger.warning(
                "bird %s failed (attempt %d/%d): rc=%d stderr=%s",
                " ".join(args), attempt + 1, max_attempts,
                result.returncode, result.stderr.strip()[:200],
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "bird %s timed out after %ds (attempt %d/%d)",
                " ".join(args), timeout, attempt + 1, max_attempts
            )
        except FileNotFoundError:
            logger.error(
                "bird CLI not found at %s. Install with: npm install -g bird-cli",
                cli
            )
            return None
        except Exception as e:
            logger.error("Unexpected error running bird CLI: %s", e)
            return None

        # Exponential backoff before retry
        if attempt < max_attempts - 1:
            backoff = 2 ** attempt  # 1s, 2s
            logger.debug("Retrying in %ds...", backoff)
            time.sleep(backoff)

    logger.error("bird %s failed after %d attempts", " ".join(args), max_attempts)
    return None
