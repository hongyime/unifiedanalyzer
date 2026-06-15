"""Face search engine with similarity search and multi-face support."""

import numpy as np
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from sqlalchemy.orm import Session
from sqlalchemy import select

from src.face.storage.faiss_index import BatchedFAISSIndex
from src.face.storage.database import Face, Image
from src.face.config import Settings


@dataclass
class SearchResult:
    """A single search result."""
    face_id: str
    image_id: str
    file_path: str
    similarity: float
    quality_score: float
    thumbnail_path: Optional[str] = None
    bbox_x1: float = 0.0
    bbox_y1: float = 0.0
    bbox_x2: float = 0.0
    bbox_y2: float = 0.0


@dataclass
class SearchResponse:
    """Complete search response."""
    results: List[SearchResult]
    total_found: int
    query_embedding_dim: int
    search_time_ms: float


class SearchEngine:
    """
    Face search engine using FAISS for similarity search.
    
    Supports:
    - Single face search
    - Multi-face search (any or all together)
    - Filtering by similarity threshold
    """
    
    def __init__(self, faiss_index: BatchedFAISSIndex, config: Settings):
        """
        Initialize the search engine.
        
        Args:
            faiss_index: Batched FAISS index for similarity search
            config: Application settings
        """
        self.faiss_index = faiss_index
        self.config = config
        self.default_threshold = config.search_min_similarity
    
    def search(
        self,
        embedding: np.ndarray,
        k: int = 100,
        threshold: Optional[float] = None,
        db_session: Optional[Session] = None
    ) -> List[SearchResult]:
        """
        Search for similar faces.
        
        Args:
            embedding: Query embedding (512-d)
            k: Number of results to return
            threshold: Minimum similarity threshold (0-1)
            db_session: SQLAlchemy session for database lookup
            
        Returns:
            List of SearchResult objects
        """
        if threshold is None:
            threshold = self.default_threshold
        
        # Search FAISS index
        matches = self.faiss_index.search(embedding, k=k)
        
        if not matches or not db_session:
            return []
        
        # Filter by threshold and enrich with database info
        results = []
        face_ids = [face_id for face_id, score in matches if score >= threshold]
        
        if not face_ids:
            return []
        
        # Fetch face details from database
        stmt = select(Face).where(Face.embedding_id.in_(face_ids))
        faces = db_session.execute(stmt).scalars().all()
        
        # Create mapping of embedding_id to face record
        face_map = {face.embedding_id: face for face in faces}
        
        # Build results
        for face_id, score in matches:
            if score < threshold:
                continue
                
            face = face_map.get(face_id)
            if not face:
                continue
            
            # Get image info
            image = face.image if face.image else None
            file_path = image.file_path if image else "unknown"
            
            results.append(SearchResult(
                face_id=face.embedding_id,
                image_id=str(face.image_id),
                file_path=file_path,
                similarity=score,
                quality_score=face.quality_score,
                thumbnail_path=face.thumbnail_path,
                bbox_x1=face.bbox_x1,
                bbox_y1=face.bbox_y1,
                bbox_x2=face.bbox_x2,
                bbox_y2=face.bbox_y2
            ))
        
        return results
    
    def search_multi(
        self,
        embeddings: List[np.ndarray],
        mode: str = "any",
        k: int = 100,
        threshold: Optional[float] = None,
        db_session: Optional[Session] = None
    ) -> List[SearchResult]:
        """
        Search with multiple faces.
        
        Args:
            embeddings: List of query embeddings
            mode: "any" (union) or "all" (intersection)
            k: Number of results per face
            threshold: Minimum similarity threshold
            db_session: SQLAlchemy session
            
        Returns:
            List of SearchResult objects, ranked by combined score
        """
        if not embeddings:
            return []
        
        if threshold is None:
            threshold = self.default_threshold
        
        # Search for each embedding
        all_results: Dict[str, Dict[int, float]] = {}  # face_id -> {query_idx: score}
        
        for idx, emb in enumerate(embeddings):
            matches = self.faiss_index.search(emb, k=k * len(embeddings))
            
            for face_id, score in matches:
                if score >= threshold:
                    if face_id not in all_results:
                        all_results[face_id] = {}
                    all_results[face_id][idx] = score
        
        # Filter based on mode
        if mode == "all":
            # Only include faces that match ALL query embeddings
            filtered_ids = [
                face_id for face_id, scores in all_results.items()
                if len(scores) == len(embeddings)
            ]
        else:  # mode == "any"
            # Include faces that match ANY query embedding
            filtered_ids = list(all_results.keys())
        
        if not filtered_ids or not db_session:
            return []
        
        # Fetch face details
        stmt = select(Face).where(Face.embedding_id.in_(filtered_ids))
        faces = db_session.execute(stmt).scalars().all()
        face_map = {face.embedding_id: face for face in faces}
        
        # Build results with combined scores
        results = []
        for face_id in filtered_ids:
            face = face_map.get(face_id)
            if not face:
                continue
            
            scores = all_results[face_id]
            # Combined score: average of all matching scores
            combined_score = sum(scores.values()) / len(scores)
            
            image = face.image if face.image else None
            file_path = image.file_path if image else "unknown"
            
            results.append(SearchResult(
                face_id=face.embedding_id,
                image_id=str(face.image_id),
                file_path=file_path,
                similarity=combined_score,
                quality_score=face.quality_score,
                thumbnail_path=face.thumbnail_path,
                bbox_x1=face.bbox_x1,
                bbox_y1=face.bbox_y1,
                bbox_x2=face.bbox_x2,
                bbox_y2=face.bbox_y2
            ))
        
        # Sort by combined similarity (descending)
        results.sort(key=lambda r: r.similarity, reverse=True)
        
        return results[:k]
    
    def search_by_identity(
        self,
        identity_id: str,
        k: int = 100,
        threshold: Optional[float] = None,
        db_session: Optional[Session] = None
    ) -> List[SearchResult]:
        """
        Search for faces within a specific identity.
        
        This retrieves all faces belonging to an identity and uses
        their average embedding as the query.
        
        Args:
            identity_id: Identity ID to search within
            k: Number of results to return
            threshold: Minimum similarity threshold
            db_session: SQLAlchemy session
            
        Returns:
            List of SearchResult objects
        """
        if not db_session:
            return []
        
        # Get all faces for this identity
        from src.face.storage.database import FaceIdentityMap
        
        stmt = select(FaceIdentityMap).where(FaceIdentityMap.identity_id == identity_id)
        mappings = db_session.execute(stmt).scalars().all()
        
        if not mappings:
            return []
        
        face_ids = [m.face_id for m in mappings]
        
        # Fetch face embeddings
        stmt = select(Face).where(Face.id.in_(face_ids))
        faces = db_session.execute(stmt).scalars().all()
        
        if not faces:
            return []
        
        # Compute average embedding
        # Ensure we convert from halfvec (float16) if stored as such
        embeddings = []
        for face in faces:
            if face.embedding_vec is not None:
                emb = np.array(face.embedding_vec, dtype=np.float32)
                embeddings.append(emb)
        
        if not embeddings:
            return []
            
        avg_embedding = np.mean(embeddings, axis=0)
        # Normalize
        norm = np.linalg.norm(avg_embedding)
        if norm > 0:
            avg_embedding = avg_embedding / norm
        
        # Search using average embedding
        return self.search(avg_embedding, k=k, threshold=threshold, db_session=db_session)
