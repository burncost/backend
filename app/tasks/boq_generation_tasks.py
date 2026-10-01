from app.core.celery_app import celery_app
from app.core.database import mongodb, connect_to_mongo
from app.core import database as core_database
from app.services.boq_generator import BOQGenerator
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

### Generate BOQ items from documents
@celery_app.task(name="generate_boq_items")
def generate_boq_items_task(boq_id: str, document_ids: list):
    try:
        logger.info(f"Starting BOQ generation: {boq_id}")
        
        # Ensure MongoDB connection
        if not mongodb.db:
            import asyncio
            asyncio.run(connect_to_mongo())
        
        # Generate BOQ
        boq_generator = BOQGenerator(mongodb.db)
        
        import asyncio
        asyncio.run(boq_generator.generate_boq_items(boq_id, document_ids))
        
        logger.info(f"BOQ generated successfully: {boq_id}")
        
        return {"status": "success", "boq_id": boq_id}
        
    except Exception as e:
        logger.error(f"Error generating BOQ {boq_id}: {str(e)}")
        raise

### Recalculate BOQ totals and rates
@celery_app.task(name="recalculate_boq")
def recalculate_boq_task(boq_id: str):
    try:
        logger.info(f"Recalculating BOQ: {boq_id}")
        
        # Implementation would recalculate all rates and totals
        # based on updated material rates
        
        return {"status": "success", "boq_id": boq_id}
        
    except Exception as e:
        logger.error(f"Error recalculating BOQ {boq_id}: {str(e)}")
        raise


# ── Background bill verification (large uploads) ──────────────────────────────
#
# A full QS workbook can take minutes to parse and re-price — far longer than a
# request should be held open. `/boqs/verify/async` therefore spools the upload
# to a temp path and creates a job document; this task advances that document,
# and the client polls `GET /boqs/jobs/{job_id}` for the result.

_BOQ_JOB_COLLECTION = "boq_jobs"


class _SpooledUpload:
    """Minimal UploadFile stand-in over a temp path.

    `BOQGenerator.upload_and_verify` only needs `.read()` and `.filename`, and a
    plain file object cannot carry the attribute — so the named temp file (the
    thing that actually survives the request) is wrapped instead.
    """

    def __init__(self, path: str, filename: str):
        self._handle = open(path, "rb")
        self.filename = filename

    def read(self) -> bytes:
        return self._handle.read()

    def close(self) -> None:
        self._handle.close()


async def _set_job_status(db, job_id: str, status: str, **fields) -> None:
    from bson import ObjectId

    await db[_BOQ_JOB_COLLECTION].update_one(
        {"_id": ObjectId(job_id)},
        {"$set": {"status": status, "updatedAt": datetime.utcnow(), **fields}},
    )


async def run_verify_job(
    db, job_id: str, path: str, filename: str, city: str = "Abuja", user_id: str = ""
) -> dict:
    """Verify the uploaded file at `path` and record the outcome on the job.

    Shared by the Celery task (its own event loop) and the endpoint's inline
    fallback (the request's loop), so both paths report status identically and
    the temp upload is always removed once the handle is closed.
    """
    await _set_job_status(db, job_id, "running")
    upload = None
    try:
        upload = _SpooledUpload(path, filename)
        result = await BOQGenerator(db).upload_and_verify(
            file=upload, uploaded_by=user_id, city=city
        )
    except Exception as exc:  # noqa: BLE001 - the job must record its own failure
        logger.error(f"Background BOQ verification failed for job {job_id}: {exc}")
        await _set_job_status(db, job_id, "failed", error=str(exc))
        return {"status": "failed", "error": str(exc)}
    finally:
        if upload is not None:
            upload.close()
        try:
            os.remove(path)  # Windows refuses to unlink an open handle, hence close first
        except OSError as exc:  # pragma: no cover - temp-dir housekeeping
            logger.warning(f"Could not remove BOQ job upload {path}: {exc}")

    await _set_job_status(db, job_id, "succeeded", result=result)
    logger.info(f"Background BOQ verification finished: job {job_id}")
    return result


### Verify an uploaded bill off the request thread (queued by /boqs/verify/async)
@celery_app.task(name="verify_boq")
def verify_boq_task(
    job_id: str, path: str, filename: str, city: str = "Abuja", user_id: str = ""
):
    try:
        logger.info(f"Starting background BOQ verification: job {job_id}")

        # Ensure MongoDB connection. Read through the module (not the imported
        # name) because `connect_to_mongo` rebinds the global it owns.
        if core_database.mongodb is None:
            import asyncio
            asyncio.run(core_database.connect_to_mongo())
        db = core_database.mongodb
        if db is None:
            raise RuntimeError("MongoDB unavailable; cannot record the BOQ job")

        import asyncio
        asyncio.run(run_verify_job(db, job_id, path, filename, city, user_id))

        return {"status": "success", "job_id": job_id}

    except Exception as e:
        logger.error(f"Error verifying BOQ job {job_id}: {str(e)}")
        raise

