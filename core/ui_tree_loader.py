"""Batch Treeview inserts so large lists stay responsive (especially in frozen EXE)."""
from __future__ import annotations

from typing import Callable, Iterable, Optional, Sequence, Tuple, TypeVar

T = TypeVar("T")

# (iid, values, tags)
TreeRow = Tuple[str, tuple, tuple]


def save_tree_yview(tree):
    """Remember Treeview scroll position across repopulate."""
    try:
        return tree.yview()
    except Exception:
        return None


def restore_tree_yview(tree, yview) -> None:
    if not yview:
        return
    try:
        tree.yview_moveto(yview[0])
    except Exception:
        pass


def sync_tree_by_iid(
    tree,
    rows: Sequence[TreeRow],
    *,
    full_rebuild_threshold: float = 0.45,
) -> str:
    """
    Diff Treeview by iid: update/move/insert/delete instead of wipe+refill when
    the change set is small. Returns 'noop' | 'diff' | 'rebuild'.
    """
    desired = [(str(iid), tuple(values), tuple(tags or ())) for iid, values, tags in rows]
    try:
        existing = list(tree.get_children(""))
    except Exception:
        existing = []

    if not desired and not existing:
        return "noop"

    desired_ids = [r[0] for r in desired]
    desired_map = {r[0]: r for r in desired}
    existing_set = set(existing)
    desired_set = set(desired_ids)

    if existing == desired_ids:
        changed = False
        for iid, values, tags in desired:
            try:
                cur_vals = tuple(tree.item(iid, "values") or ())
                cur_tags = tuple(tree.item(iid, "tags") or ())
            except Exception:
                changed = True
                break
            if cur_vals != values or cur_tags != tags:
                changed = True
                break
        if not changed:
            return "noop"

    n = max(len(existing), len(desired), 1)
    removed = existing_set - desired_set
    added = desired_set - existing_set
    churn = len(removed) + len(added)
    # Large reshuffle → wipe once (caller should fall back to batched populate).
    if churn / float(n) >= full_rebuild_threshold and n >= 80:
        return "rebuild"

    yview = save_tree_yview(tree)
    for iid in list(removed):
        try:
            tree.delete(iid)
        except Exception:
            pass

    for idx, (iid, values, tags) in enumerate(desired):
        try:
            if tree.exists(iid):
                try:
                    cur_vals = tuple(tree.item(iid, "values") or ())
                    cur_tags = tuple(tree.item(iid, "tags") or ())
                except Exception:
                    cur_vals, cur_tags = (), ()
                if cur_vals != values or cur_tags != tags:
                    tree.item(iid, values=values, tags=tags)
                tree.move(iid, "", idx)
            else:
                tree.insert("", idx, iid=iid, values=values, tags=tags)
        except Exception:
            try:
                if tree.exists(iid):
                    tree.item(iid, values=values, tags=tags)
                else:
                    tree.insert("", "end", iid=iid, values=values, tags=tags)
            except Exception:
                pass

    restore_tree_yview(tree, yview)
    return "diff"


def populate_tree_batched(
    tree,
    rows: Iterable[T],
    insert_fn: Callable[[T], None],
    root,
    *,
    batch_size: int = 120,
    on_done: Callable[[], None] | None = None,
    on_start: Callable[[int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    """
    Insert rows into a Treeview in small batches via root.after_idle().
    Pass should_stop() to abort promptly when a dialog is cancelled.
    """
    data = list(rows)
    total = len(data)
    if on_start:
        try:
            on_start(total)
        except Exception:
            pass
    if total == 0:
        if on_done:
            try:
                on_done()
            except Exception:
                pass
        return

    def _stopped() -> bool:
        if should_stop is None:
            return False
        try:
            return bool(should_stop())
        except Exception:
            return True

    def _run_batch(start: int) -> None:
        if _stopped():
            return
        end = min(start + batch_size, total)
        for idx in range(start, end):
            if _stopped():
                return
            try:
                insert_fn(data[idx])
            except Exception:
                pass
        if end >= total:
            if not _stopped() and on_done:
                try:
                    on_done()
                except Exception:
                    pass
            return
        if _stopped():
            return
        if getattr(root, '_startup_prewarm', False):
            _run_batch(end)
            return
        try:
            root.after(1, lambda s=end: _run_batch(s))
        except Exception:
            if not _stopped() and on_done:
                try:
                    on_done()
                except Exception:
                    pass

    try:
        if getattr(root, '_startup_prewarm', False):
            _run_batch(0)
        else:
            root.after(1, lambda: _run_batch(0))
    except Exception:
        _run_batch(0)
