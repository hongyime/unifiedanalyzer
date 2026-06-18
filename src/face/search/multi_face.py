"""Multi-face search utilities."""

from typing import List, Dict, Set, Optional
from dataclasses import dataclass
import numpy as np

from .engine import SearchResult


@dataclass
class MultiFaceQuery:
    """Represents a multi-face search query."""
    embeddings: List[np.ndarray]
    mode: str  # "any" or "all"
    face_bboxes: List[tuple] = None
    face_quality_scores: List[float] = None
    
    def __post_init__(self):
        if self.face_bboxes is None:
            self.face_bboxes = []
        if self.face_quality_scores is None:
            self.face_quality_scores = []


@dataclass
class MultiFaceResult:
    """Result for a multi-face search."""
    mode: str
    faces_detected: int
    results: List[SearchResult]
    per_face_results: Dict[str, List[SearchResult]]
    common_faces: Optional[List[SearchResult]] = None


class MultiFaceSearcher:
    """
    Helper class for multi-face search operations.
    
    Provides utilities for:
    - Combining results from multiple face queries
    - Intersection/union modes
    - Result aggregation
    """
    
    def __init__(self, search_engine):
        """
        Initialize multi-face searcher.
        
        Args:
            search_engine: SearchEngine instance
        """
        self.search_engine = search_engine
    
    def search(
        self,
        query: MultiFaceQuery,
        k: int = 100,
        threshold: Optional[float] = None,
        db_session=None
    ) -> MultiFaceResult:
        """
        Perform multi-face search.
        
        Args:
            query: Multi-face query object
            k: Number of results per face
            threshold: Similarity threshold
            db_session: Database session
            
        Returns:
            MultiFaceResult object
        """
        # Search for each face individually
        per_face_results = {}
        all_face_ids: Set[str] = set()
        
        for i, embedding in enumerate(query.embeddings):
            results = self.search_engine.search(
                embedding,
                k=k,
                threshold=threshold,
                db_session=db_session
            )
            
            face_key = f"face_{i}"
            per_face_results[face_key] = results
            
            # Collect all face IDs
            for result in results:
                all_face_ids.add(result.face_id)
        
        if query.mode == "all":
            # Find intersection: faces that appear in ALL result sets
            result_sets = [
                {r.face_id for r in results}
                for results in per_face_results.values()
            ]
            
            if result_sets:
                common_ids = set.intersection(*result_sets)
                
                # Get the actual results for common faces
                common_results = []
                for result in per_face_results.get("face_0", []):
                    if result.face_id in common_ids:
                        common_results.append(result)
                
                return MultiFaceResult(
                    mode=query.mode,
                    faces_detected=len(query.embeddings),
                    results=common_results,
                    per_face_results=per_face_results,
                    common_faces=common_results
                )
            else:
                return MultiFaceResult(
                    mode=query.mode,
                    faces_detected=len(query.embeddings),
                    results=[],
                    per_face_results=per_face_results,
                    common_faces=[]
                )
        
        else:  # mode == "any"
            # Union: all faces that match ANY query face
            # Aggregate results by face_id, keeping best score
            aggregated: Dict[str, SearchResult] = {}
            
            for face_results in per_face_results.values():
                for result in face_results:
                    if result.face_id not in aggregated:
                        aggregated[result.face_id] = result
                    else:
                        # Keep the higher similarity score
                        if result.similarity > aggregated[result.face_id].similarity:
                            aggregated[result.face_id] = result
            
            # Sort by similarity
            union_results = sorted(
                aggregated.values(),
                key=lambda r: r.similarity,
                reverse=True
            )[:k]
            
            return MultiFaceResult(
                mode=query.mode,
                faces_detected=len(query.embeddings),
                results=union_results,
                per_face_results=per_face_results
            )
    
    def create_query_from_detections(
        self,
        embeddings: List[np.ndarray],
        bboxes: Optional[List[tuple]] = None,
        quality_scores: Optional[List[float]] = None,
        mode: str = "any"
    ) -> MultiFaceQuery:
        """
        Create a multi-face query from detection results.
        
        Args:
            embeddings: List of face embeddings
            bboxes: List of bounding boxes (optional)
            quality_scores: List of quality scores (optional)
            mode: "any" or "all"
            
        Returns:
            MultiFaceQuery object
        """
        return MultiFaceQuery(
            embeddings=embeddings,
            mode=mode,
            face_bboxes=bboxes or [],
            face_quality_scores=quality_scores or []
        )
