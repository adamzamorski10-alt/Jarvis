"""
Lease and claim-token utilities for the Scheduler subsystem.

This module owns:
- cryptographically strong claim token generation
- deterministic occurrence identity generation
- default lease configuration
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

DEFAULT_LEASE_DURATION = timedelta(minutes=5)
MAX_LEASE_EXTENSION = timedelta(minutes=5)


def generate_claim_token() -> str:
    """Return a cryptographically strong unpredictable claim token."""
    return secrets.token_hex(32)


def generate_occurrence_id(job_id: str, scheduled_run_at: datetime) -> str:
    """Return a deterministic occurrence identity.

    The same (job_id, scheduled_run_at) pair always produces the same ID,
    which lets the scheduler recognize the same occurrence across restarts
    and lease recovery without inventing hidden state.
    """
    if scheduled_run_at.tzinfo is None:
        scheduled_run_at = scheduled_run_at.replace(tzinfo=timezone.utc)
    message = f"{job_id}\x00{scheduled_run_at.isoformat()}"
    digest = hashlib.sha256(message.encode()).hexdigest()[:12]
    return f"occurrence_{job_id}_{digest}"


def now_utc() -> datetime:
    """Return the current UTC time."""
    return datetime.now(timezone.utc)
