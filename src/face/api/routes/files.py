"""File status API routes."""

import os
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from src.face.config import settings
from src.face.storage.database import get_database, get_db_session, Image

router = APIRouter(prefix="/files")

def get_db():
    """Dependency to get database session — request-scoped, pool-safe."""
    yield from get_db_session(settings.database_url)


def _validate_file_path(file_path: str) -> str:
    """Normalize and validate a file_path query parameter.

    Currently file_path is only used as a DB equality filter (no filesystem
    open), but this guard ensures a future endpoint can't introduce path
    traversal. Rejects null bytes and relative-path escape sequences.
    """
    if "\x00" in file_path:
        raise HTTPException(status_code=400, detail="Invalid file path: null bytes")
    normalized = os.path.normpath(file_path)
    if ".." in normalized.split(os.sep):
        raise HTTPException(status_code=400, detail="Invalid file path: directory traversal")
    return file_path


@router.get("/status")
async def get_file_status(file_path: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Get processing status of a specific file."""
    file_path = _validate_file_path(file_path)
    image = db.query(Image).filter(Image.file_path == file_path).first()
    
    if not image:
        return {
            "file_path": file_path,
            "is_processed": False,
            "faces_detected": 0,
            "status": "not_found",
            "error": None,
        }
        
    return {
        "file_path": image.file_path,
        "is_processed": image.status == "completed",
        "faces_detected": image.face_count,
        "status": image.status,
        "error": image.error_message,
        "created_at": image.created_at.isoformat()
    }

@router.get("/search")
async def search_files(pattern: str, limit: int = 50) -> Dict[str, Any]:
    """Search for files by pattern.

    NOT IMPLEMENTED — returns HTTP 501 instead of a misleading empty result.
    """
    raise HTTPException(status_code=501, detail="files.search is not implemented yet")


@router.post("/{file_id}/reprocess")
async def reprocess_file(file_id: int) -> Dict[str, Any]:
    """Queue a file for reprocessing.

    NOT IMPLEMENTED — returns HTTP 501 instead of pretending to enqueue.
    """
    raise HTTPException(status_code=501, detail="files.reprocess is not implemented yet")


@router.delete("/{file_id}")
async def delete_file(file_id: int) -> Dict[str, Any]:
    """Delete a file from the index (not the source file).

    NOT IMPLEMENTED — returns HTTP 501 instead of pretending to delete.
    """
    raise HTTPException(status_code=501, detail="files.delete is not implemented yet")
