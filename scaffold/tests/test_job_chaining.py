import unittest
from datetime import datetime

from app.database import Base, SessionLocal, engine
from app.models import Job, _utcnow
from app.scheduler import release_triggered_jobs


class JobChainingTests(unittest.TestCase):
    def setUp(self):
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)

    def test_release_triggered_jobs_marks_waiting_children_as_ready(self):
        db = SessionLocal()
        try:
            parent = Job(
                description="Parent task",
                scheduled_at=datetime(2026, 6, 28, 10, 0, 0),
                time_bucket="2026062810",
                status="completed",
            )
            db.add(parent)
            db.commit()
            db.refresh(parent)

            child = Job(
                description="Child task",
                scheduled_at=datetime(2026, 6, 28, 10, 5, 0),
                time_bucket="2026062810",
                status="pending",
                trigger_after_job_id=parent.id,
            )
            db.add(child)
            db.commit()
            db.refresh(child)

            expected_release_time = _utcnow()
            release_triggered_jobs(parent, db)
            db.refresh(child)

            self.assertEqual(child.status, "pending")
            self.assertGreaterEqual(child.scheduled_at, expected_release_time)
            self.assertLessEqual(child.scheduled_at, _utcnow())
            self.assertEqual(child.trigger_after_job_id, parent.id)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
