"""Runs once at startup: deletes finished batches (PUBLISHED/REJECTED, or
one where every video ended FAILED) older than the retention window, both
their DB rows and their downloaded/processed/final video files — so
agency.db and agent-data/batches/ don't grow forever. Active or
waiting-approval batches are never touched, no matter how old."""
import shutil

from . import config, db

RETENTION_SECONDS = 24 * 60 * 60  # 1 day
TERMINAL_STATUSES = ("PUBLISHED", "REJECTED")


def cleanup_old_batches(max_age_seconds=RETENTION_SECONDS, statuses=TERMINAL_STATUSES):
    removed = []
    for batch in db.list_batches_for_cleanup(max_age_seconds, statuses):
        shutil.rmtree(config.batch_dir(batch["id"]), ignore_errors=True)
        db.delete_batch(batch["id"])
        removed.append(batch["id"])
    return removed


if __name__ == "__main__":
    assert RETENTION_SECONDS == 86400
    assert set(TERMINAL_STATUSES) == {"PUBLISHED", "REJECTED"}
    print("cleanup.py self-check OK")
