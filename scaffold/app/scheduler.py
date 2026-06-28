import queue
import threading
import time
from datetime import datetime

from croniter import croniter
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import Job, _utcnow

# In-memory queue (simulates SQS for prototype)
job_queue: queue.Queue[int] = queue.Queue()


def get_time_bucket(scheduled_at: datetime) -> str:
    return scheduled_at.strftime("%Y%m%d%H")
    


def get_next_run(current_time: datetime, cron_expression: str) -> datetime | None:
    """Return the next run time for a cron expression."""
    if not cron_expression:
        return None

    try:
        iterator = croniter(cron_expression, current_time)
        return iterator.get_next(datetime)
    except Exception:
        return None


def schedule_next_occurrence(job: Job, db: Session) -> None:
    """Create the next occurrence for a recurring job."""
    if not job.cron_expression:
        return

    next_run = get_next_run(job.scheduled_at, job.cron_expression)
    if next_run is None:
        return

    next_job = Job(
        description=job.description,
        scheduled_at=next_run,
        time_bucket=get_time_bucket(next_run),
        cron_expression=job.cron_expression,
        status="pending",
    )
    db.add(next_job)
    db.commit()


def release_triggered_jobs(parent_job: Job, db: Session) -> None:
    """Release child jobs that are waiting for this parent job to complete."""
    waiting_jobs = (
        db.query(Job)
        .filter(Job.trigger_after_job_id == parent_job.id, Job.status == "pending")
        .all()
    )
    if not waiting_jobs:
        return

    now = _utcnow()
    for job in waiting_jobs:
        job.scheduled_at = now
        job.time_bucket = get_time_bucket(now)
        job.status = "pending"

    db.commit()


def find_due_jobs(current_time: datetime, db: Session) -> list[Job]:
    time_bucket = get_time_bucket(current_time)
    return (
            db.query(Job).filter(
            Job.time_bucket == time_bucket,
            Job.scheduled_at <= current_time,
            Job.status == "pending"
        ).all()
    )


def watcher_loop(interval: int = 10):
    """Watcher scans DB for due jobs and pushes them to the queue."""
    while True:
        db = SessionLocal()
        try:
            now = _utcnow()
            due_jobs = find_due_jobs(now, db)
            for job in due_jobs:
                job.status = "queued"
                db.commit()
                job_queue.put(job.id)
        finally:
            db.close()
        time.sleep(interval)


def worker_loop():
    """Worker pulls jobs from queue and executes them."""
    while True:
        job_id = job_queue.get()
        db = SessionLocal()
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job is None or job.status == "cancelled":
                continue

            job.status = "running"
            db.commit()

            # Simulate execution — in production this would call LLM
            job.result = f"Executed: {job.description}"
            job.status = "completed"
            db.commit()

            release_triggered_jobs(job, db)

            if job.cron_expression:
                schedule_next_occurrence(job, db)
        except Exception as e:
            job.status = "failed"
            job.result = str(e)
            db.commit()
        finally:
            db.close()
            job_queue.task_done()


def start_scheduler():
    """Start watcher and worker threads."""
    watcher = threading.Thread(target=watcher_loop, daemon=True)
    worker = threading.Thread(target=worker_loop, daemon=True)
    watcher.start()
    worker.start()
