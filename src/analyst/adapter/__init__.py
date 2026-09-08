"""Adapter over the market-ingestion lake.

Everything that knows the ingester's storage layout lives in this package
and nowhere else. The rest of the codebase sees one interface:

    adapter = LakeAdapter.from_settings(settings)
    for ann in adapter.iter_new_announcements(since):
        ...
"""

from .lake import Announcement, LakeAdapter, LakeNotFound

__all__ = ["Announcement", "LakeAdapter", "LakeNotFound"]
