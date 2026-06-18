"""Clustering quality metrics for identity validation."""

import logging
from dataclasses import dataclass

import numpy as np
from sklearn.metrics import (
    silhouette_score,
    calinski_harabasz_score,
    davies_bouldin_score,
)

logger = logging.getLogger(__name__)


@dataclass
class ClusteringMetricsResult:
    """Container for clustering quality metrics."""

    silhouette_score: float
    calinski_harabasz_index: float
    davies_bouldin_index: float
    n_samples: int
    n_clusters: int
    n_outliers: int
    avg_cluster_size: float
    min_cluster_size: int
    max_cluster_size: int


class ClusteringMetrics:
    """
    Compute and display clustering quality metrics.

    Provides multiple metrics to evaluate identity clustering quality:
    - Silhouette Score: Measures how similar objects are to their own cluster
    - Calinski-Harabasz Index: Ratio of between-cluster to within-cluster dispersion
    - Davies-Bouldin Index: Average similarity between each cluster and its most similar
    """

    @staticmethod
    def compute(
        embeddings: np.ndarray,
        labels: np.ndarray,
        quality_scores: np.ndarray | None = None,
    ) -> ClusteringMetricsResult:
        """
        Compute comprehensive clustering quality metrics.

        Args:
            embeddings: Array of shape (n_faces, 512) with face embeddings.
            labels: Cluster labels from HDBSCAN (-1 for outliers).
            quality_scores: Optional quality scores for weighted metrics.

        Returns:
            ClusteringMetricsResult with all computed metrics.
        """
        n_samples = len(embeddings)
        
        # Filter out outliers for metric computation
        non_outlier_mask = labels != -1
        filtered_embeddings = embeddings[non_outlier_mask]
        filtered_labels = labels[non_outlier_mask]
        
        n_outliers = np.sum(labels == -1)
        unique_labels = np.unique(filtered_labels)
        n_clusters = len(unique_labels)

        # Handle edge cases
        if n_clusters < 2 or len(filtered_embeddings) < 2:
            return ClusteringMetricsResult(
                silhouette_score=0.0,
                calinski_harabasz_index=0.0,
                davies_bouldin_index=float("inf"),
                n_samples=n_samples,
                n_clusters=n_clusters,
                n_outliers=int(n_outliers),
                avg_cluster_size=0.0,
                min_cluster_size=0,
                max_cluster_size=0,
            )

        # Compute standard metrics
        try:
            sil_score = silhouette_score(
                filtered_embeddings, 
                filtered_labels, 
                metric="cosine"
            )
        except Exception as e:
            logger.warning(f"Silhouette score computation failed: {e}")
            sil_score = 0.0

        try:
            ch_index = calinski_harabasz_score(filtered_embeddings, filtered_labels)
        except Exception as e:
            logger.warning(f"Calinski-Harabasz index computation failed: {e}")
            ch_index = 0.0

        try:
            db_index = davies_bouldin_score(filtered_embeddings, filtered_labels)
        except Exception as e:
            logger.warning(f"Davies-Bouldin index computation failed: {e}")
            db_index = float("inf")

        # Compute cluster size statistics
        cluster_sizes = [np.sum(filtered_labels == label) for label in unique_labels]
        avg_cluster_size = np.mean(cluster_sizes) if cluster_sizes else 0.0
        min_cluster_size = min(cluster_sizes) if cluster_sizes else 0
        max_cluster_size = max(cluster_sizes) if cluster_sizes else 0

        # Apply quality-weighted adjustments if quality scores provided
        if quality_scores is not None:
            sil_score, ch_index, db_index = ClusteringMetrics._apply_quality_weighting(
                sil_score,
                ch_index,
                db_index,
                quality_scores[non_outlier_mask],
                filtered_labels,
            )

        return ClusteringMetricsResult(
            silhouette_score=float(sil_score),
            calinski_harabasz_index=float(ch_index),
            davies_bouldin_index=float(db_index),
            n_samples=n_samples,
            n_clusters=n_clusters,
            n_outliers=int(n_outliers),
            avg_cluster_size=float(avg_cluster_size),
            min_cluster_size=min_cluster_size,
            max_cluster_size=max_cluster_size,
        )

    @staticmethod
    def _apply_quality_weighting(
        sil_score: float,
        ch_index: float,
        db_index: float,
        quality_scores: np.ndarray,
        labels: np.ndarray,
    ) -> tuple[float, float, float]:
        """
        Adjust metrics based on quality scores.

        Higher quality faces should contribute more to the metrics.

        Args:
            sil_score: Original silhouette score.
            ch_index: Original Calinski-Harabasz index.
            db_index: Original Davies-Bouldin index.
            quality_scores: Quality scores for non-outlier faces.
            labels: Cluster labels for non-outlier faces.

        Returns:
            Tuple of (adjusted_silhouette, adjusted_ch, adjusted_db).
        """
        # Normalize quality scores
        quality_normalized = quality_scores / (quality_scores.max() + 1e-8)
        
        # Compute average quality per cluster
        unique_labels = np.unique(labels)
        cluster_qualities = {}
        for label in unique_labels:
            mask = labels == label
            cluster_qualities[label] = np.mean(quality_normalized[mask])

        # Weight adjustment factor (higher quality = less penalty)
        avg_quality = np.mean(quality_normalized)
        quality_factor = 0.5 + (avg_quality * 0.5)  # Range: 0.5 to 1.0

        # Adjust metrics
        adjusted_sil = sil_score * quality_factor
        adjusted_ch = ch_index * quality_factor
        adjusted_db = db_index / quality_factor if quality_factor > 0 else db_index

        return adjusted_sil, adjusted_ch, adjusted_db

    @staticmethod
    def format_report(metrics: ClusteringMetricsResult) -> str:
        """
        Format metrics as a human-readable report.

        Args:
            metrics: ClusteringMetricsResult to format.

        Returns:
            Formatted string report.
        """
        lines = [
            "=" * 50,
            "CLUSTERING QUALITY REPORT",
            "=" * 50,
            f"Samples: {metrics.n_samples} total, {metrics.n_outliers} outliers",
            f"Clusters: {metrics.n_clusters}",
            "",
            "Quality Metrics:",
            f"  Silhouette Score:       {metrics.silhouette_score:.4f} (range: -1 to 1, higher is better)",
            f"  Calinski-Harabasz:      {metrics.calinski_harabasz_index:.2f} (higher is better)",
            f"  Davies-Bouldin:         {metrics.davies_bouldin_index:.4f} (lower is better)",
            "",
            "Cluster Size Statistics:",
            f"  Average: {metrics.avg_cluster_size:.1f}",
            f"  Min:     {metrics.min_cluster_size}",
            f"  Max:     {metrics.max_cluster_size}",
            "=" * 50,
        ]

        # Add quality assessment
        lines.append("")
        lines.append("Quality Assessment:")
        
        if metrics.silhouette_score > 0.5:
            lines.append("  ✓ Strong cluster separation")
        elif metrics.silhouette_score > 0.25:
            lines.append("  △ Moderate cluster separation")
        else:
            lines.append("  ✗ Weak cluster separation")

        if metrics.davies_bouldin_index < 1.0:
            lines.append("  ✓ Low cluster similarity")
        elif metrics.davies_bouldin_index < 2.0:
            lines.append("  △ Moderate cluster similarity")
        else:
            lines.append("  ✗ High cluster similarity")

        lines.append("=" * 50)

        return "\n".join(lines)

    @staticmethod
    def to_dict(metrics: ClusteringMetricsResult) -> dict:
        """
        Convert metrics to dictionary for API response.

        Args:
            metrics: ClusteringMetricsResult to convert.

        Returns:
            Dictionary with all metrics.
        """
        return {
            "silhouette_score": round(metrics.silhouette_score, 4),
            "calinski_harabasz_index": round(metrics.calinski_harabasz_index, 2),
            "davies_bouldin_index": round(metrics.davies_bouldin_index, 4),
            "n_samples": metrics.n_samples,
            "n_clusters": metrics.n_clusters,
            "n_outliers": metrics.n_outliers,
            "avg_cluster_size": round(metrics.avg_cluster_size, 1),
            "min_cluster_size": metrics.min_cluster_size,
            "max_cluster_size": metrics.max_cluster_size,
            "quality_assessment": ClusteringMetrics._get_quality_assessment(metrics),
        }

    @staticmethod
    def _get_quality_assessment(metrics: ClusteringMetricsResult) -> str:
        """Get overall quality assessment string."""
        if metrics.silhouette_score > 0.5 and metrics.davies_bouldin_index < 1.0:
            return "excellent"
        elif metrics.silhouette_score > 0.25 and metrics.davies_bouldin_index < 2.0:
            return "good"
        elif metrics.silhouette_score > 0.0:
            return "fair"
        else:
            return "poor"
