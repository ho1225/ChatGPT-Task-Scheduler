import unittest
from datetime import datetime

from app.database import Base, SessionLocal, engine
from app.models import Job
from app.scheduler import get_next_run, schedule_next_occurrence


class RecurringJobTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

    def test_get_next_run_returns_next_occurrence(self):
        current_time = datetime(2026, 6, 28, 10, 0, 0)
        self.assertEqual(get_next_run(current_time, "*/5 * * * *"), datetime(2026, 6, 28, 10, 5, 0))

    def test_schedule_next_occurrence_creates_follow_up_job(self):
        db = SessionLocal()
        try:
            job = Job(
                description="Send daily report",
                scheduled_at=datetime(2026, 6, 28, 10, 0, 0),
                time_bucket="2026062810",
                status="completed",
                cron_expression="0 9 * * *",
            )
            db.add(job)
            db.commit()
            db.refresh(job)

            schedule_next_occurrence(job, db)

            follow_up_jobs = db.query(Job).filter(Job.id != job.id).all()
            self.assertEqual(len(follow_up_jobs), 1)
            self.assertEqual(follow_up_jobs[0].description, "Send daily report")
            self.assertEqual(follow_up_jobs[0].scheduled_at, datetime(2026, 6, 29, 9, 0, 0))
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
