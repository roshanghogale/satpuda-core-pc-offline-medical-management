"""Mark legacy sync modules deprecated once Sync V3 is default.

These modules remain for rollback (`SYNC_V3=0`) and unmigrated edge paths.
Do not delete until production soak confirms V3 for all entities (see
REBUILD_MIGRATION_PLAN Phase 6).
"""

DEPRECATED_MODULES = (
    "core.sync_watermarks",
    "core.revision_sync_flags",  # V3 is always revision-based
    "core.page_refresh",  # prefer DataChangeBus
)

# Watermark poller: only when SYNC_V3=0 and USE_REVISION_SYNC=false.
# sync_coordinator.after_* hooks: still used; V3 outbox is additive on failure.
