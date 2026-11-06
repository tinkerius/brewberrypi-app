"""Cloud Sync: a one-way mirror of the recipes from the brewer's cloud storage (spec 14, ADR-0003)."""

from collections.abc import Callable
from pathlib import Path

from brewberrypi.clock import Clock
from brewberrypi.cloud_sync.google_drive import BUILT_IN_CLIENT_ID, BUILT_IN_CLIENT_SECRET, GoogleDrive
from brewberrypi.cloud_sync.google_drive import TITLE as GOOGLE_DRIVE
from brewberrypi.cloud_sync.service import CloudSync
from brewberrypi.config import CloudSyncConfig

__all__ = ["CloudSync", "create_cloud_sync"]


def create_cloud_sync(
    config: CloudSyncConfig,
    recipe_directory: Path,
    clock: Clock,
    on_recipes_changed: Callable[[], None] = lambda: None,
) -> CloudSync:
    """Cloud Sync with Google Drive; unavailable when disabled or without a client id (SYNC-001)."""
    client_id = config.google_drive_client_id or BUILT_IN_CLIENT_ID
    client_secret = config.google_drive_client_secret or BUILT_IN_CLIENT_SECRET
    provider = GoogleDrive(client_id, client_secret, clock) if config.enabled and client_id else None
    return CloudSync(
        provider,
        title=GOOGLE_DRIVE,
        recipe_directory=recipe_directory,
        state_directory=config.state_directory,
        interval_seconds=config.interval_seconds,
        clock=clock,
        on_recipes_changed=on_recipes_changed,
    )
