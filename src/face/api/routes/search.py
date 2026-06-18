"""Search API routes for face search operations."""

from fastapi import APIRouter, Request, UploadFile, File, HTTPException, Depends
from typing import List, Optional
import numpy as np
from PIL import Image
import io
import time
from sqlalchemy.orm import Session

from src.face.config import settings
from src.face.storage.database import get_db_session
from src.face.search.engine import SearchEngine, SearchResult, SearchResponse
from src.face.search.ranking import RankingStrategy

router = APIRouter()


def get_db():
    """Dependency to get database session — request-scoped, pool-safe."""
    yield from get_db_session(settings.database_url)


@router.post("/search", response_model=SearchResponse)
async def search_faces(
    request: Request,
    image: UploadFile = File(...),
    top_k: int = 100,
    min_similarity: Optional[float] = None,
    db: Session = Depends(get_db),
):
    """
    Search for similar faces by uploading an image.

    Uses the live in-memory FAISS index (app.state.faiss_index) so results
    include faces ingested since startup — not just what was on disk at boot.
    """
    start_time = time.time()

    # Shared singletons from app.state — same objects the ingest pipeline writes to
    faiss_index = request.app.state.faiss_index
    detector = request.app.state.detector
    search_engine = SearchEngine(faiss_index, settings)
    ranker = RankingStrategy()

    try:
        contents = await image.read()
        img = Image.open(io.BytesIO(contents))
        img_array = np.array(img.convert("RGB"))

        faces = detector.detect(img_array, extract_embeddings=True)
        if not faces:
            return SearchResponse(
                results=[],
                total_found=0,
                query_embedding_dim=512,
                search_time_ms=(time.time() - start_time) * 1000,
            )

        # Best face (highest quality score)
        best_face = max(faces, key=lambda f: f.quality_score)

        # Pull embedding from detector result — same vector space as indexed faces (buffalo_l)
        embedding = best_face.embedding
        if embedding is None:
            raise HTTPException(status_code=400, detail="Failed to extract embedding")

        raw_results = search_engine.search(
            embedding,
            k=top_k,
            threshold=min_similarity,
            db_session=db,
        )

        # Apply ranking (similarity + quality weighting)
        ranked = ranker.rank(raw_results)

        return SearchResponse(
            results=ranked,
            total_found=len(ranked),
            query_embedding_dim=512,
            search_time_ms=(time.time() - start_time) * 1000,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search/multi", response_model=SearchResponse)
async def search_multi_face(
    request: Request,
    image: UploadFile = File(...),
    mode: str = "any",
    top_k: int = 100,
    min_similarity: Optional[float] = None,
    db: Session = Depends(get_db),
):
    """
    Multi-face search — find images containing specific people.
    mode='any': union (image matches any face in query).
    mode='all': intersection (image must contain all faces in query).
    """
    start_time = time.time()

    faiss_index = request.app.state.faiss_index
    detector = request.app.state.detector
    search_engine = SearchEngine(faiss_index, settings)

    try:
        contents = await image.read()
        img = Image.open(io.BytesIO(contents))
        img_array = np.array(img.convert("RGB"))

        faces = detector.detect(img_array, extract_embeddings=True)
        if not faces:
            return SearchResponse(
                results=[],
                total_found=0,
                query_embedding_dim=512,
                search_time_ms=(time.time() - start_time) * 1000,
            )

        embeddings = [f.embedding for f in faces if f.embedding is not None]
        if not embeddings:
            raise HTTPException(status_code=400, detail="No usable faces detected")

        results = search_engine.search_multi(
            embeddings,
            mode=mode,
            k=top_k,
            threshold=min_similarity,
            db_session=db,
        )

        return SearchResponse(
            results=results,
            total_found=len(results),
            query_embedding_dim=512,
            search_time_ms=(time.time() - start_time) * 1000,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/search/identity/{identity_id}")
async def search_within_identity(
    identity_id: int,
    image: UploadFile = File(...),
    top_k: int = 50,
):
    """
    Search for faces within a specific identity cluster.

    NOT IMPLEMENTED. Returns HTTP 501 so clients correctly surface the gap.
    """
    raise HTTPException(
        status_code=501,
        detail="search_within_identity is not implemented yet",
    )
