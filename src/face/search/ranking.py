"""Ranking strategies for search results."""

from typing import List, Optional
from dataclasses import dataclass
from datetime import datetime, timezone
import numpy as np

from .engine import SearchResult


@dataclass
class RankedResult(SearchResult):
    """Search result with ranking score."""
    ranking_score: float = 0.0
    recency_factor: float = 1.0
    quality_factor: float = 1.0


class RankingStrategy:
    """
    Ranking strategy combining multiple factors.
    
    Factors:
    - Similarity score from FAISS
    - Quality score of the face detection
    - Recency of the image
    """
    
    def __init__(
        self,
        similarity_weight: float = 0.5,
        quality_weight: float = 0.3,
        recency_weight: float = 0.2,
        recency_decay_days: float = 365.0
    ):
        """
        Initialize ranking strategy.
        
        Args:
            similarity_weight: Weight for similarity score (0-1)
            quality_weight: Weight for quality score (0-1)
            recency_weight: Weight for recency factor (0-1)
            recency_decay_days: Days for recency to decay to 0.5
        """
        self.similarity_weight = similarity_weight
        self.quality_weight = quality_weight
        self.recency_weight = recency_weight
        self.recency_decay_days = recency_decay_days
        
        # Normalize weights
        total = similarity_weight + quality_weight + recency_weight
        if total > 0:
            self.similarity_weight /= total
            self.quality_weight /= total
            self.recency_weight /= total
    
    def rank(
        self,
        results: List[SearchResult],
        reference_date: Optional[datetime] = None
    ) -> List[RankedResult]:
        """
        Rank search results using combined scoring.
        
        Args:
            results: List of SearchResult objects
            reference_date: Reference date for recency calculation
            
        Returns:
            List of RankedResult objects sorted by ranking score
        """
        if reference_date is None:
            reference_date = datetime.now(timezone.utc)
        
        ranked_results = []
        
        for result in results:
            # For simplicity, if we don't have a date, assume neutral recency
            recency_factor = 0.5
            
            # Normalize quality score (assume 0-1 range)
            quality_factor = min(1.0, max(0.0, result.quality_score))
            
            # Combined ranking score
            ranking_score = (
                self.similarity_weight * result.similarity +
                self.quality_weight * quality_factor +
                self.recency_weight * recency_factor
            )
            
            ranked_results.append(RankedResult(
                face_id=result.face_id,
                image_id=result.image_id,
                file_path=result.file_path,
                similarity=result.similarity,
                quality_score=result.quality_score,
                thumbnail_path=result.thumbnail_path,
                bbox_x1=result.bbox_x1,
                bbox_y1=result.bbox_y1,
                bbox_x2=result.bbox_x2,
                bbox_y2=result.bbox_y2,
                ranking_score=ranking_score,
                recency_factor=recency_factor,
                quality_factor=quality_factor
            ))
        
        # Sort by ranking score (descending)
        ranked_results.sort(key=lambda r: r.ranking_score, reverse=True)
        
        return ranked_results
    
    def rank_by_similarity_only(
        self,
        results: List[SearchResult]
    ) -> List[RankedResult]:
        """
        Rank results by similarity score only.
        
        Args:
            results: List of SearchResult objects
            
        Returns:
            List of RankedResult objects sorted by similarity
        """
        ranked_results = []
        
        for result in results:
            ranked_results.append(RankedResult(
                face_id=result.face_id,
                image_id=result.image_id,
                file_path=result.file_path,
                similarity=result.similarity,
                quality_score=result.quality_score,
                thumbnail_path=result.thumbnail_path,
                bbox_x1=result.bbox_x1,
                bbox_y1=result.bbox_y1,
                bbox_x2=result.bbox_x2,
                bbox_y2=result.bbox_y2,
                ranking_score=result.similarity,
                recency_factor=1.0,
                quality_factor=min(1.0, max(0.0, result.quality_score))
            ))
        
        ranked_results.sort(key=lambda r: r.similarity, reverse=True)
        
        return ranked_results
