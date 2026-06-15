"""Quality-aware face clustering using HDBSCAN with quality-weighted distances."""

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import hdbscan
from sklearn.metrics import pairwise_distances

logger = logging.getLogger(__name__)


@dataclass
class ClusteringResult:
    """Result of clustering operation."""

    labels: np.ndarray
    probabilities: Optional[np.ndarray]
    n_clusters: int
    n_outliers: int
    cluster_sizes: dict[int, int]


class QualityAwareClusterer:
    """
    Quality-aware face clustering using HDBSCAN.

    Low-quality faces are treated as outliers and given lower weight
    in distance calculations.
    """

    def __init__(
        self,
        min_cluster_size: int = 5,
        min_samples: int = 3,
        cluster_selection_epsilon: float = 0.6,
        metric: str = "cosine",
    ):
        """
        Initialize the clusterer.

        Args:
            min_cluster_size: Minimum number of faces per cluster.
            min_samples: Minimum samples for core points.
            cluster_selection_epsilon: Maximum distance for cluster membership.
            metric: Distance metric (cosine, euclidean).
        """
        self.min_cluster_size = min_cluster_size
        self.min_samples = min_samples
        self.cluster_selection_epsilon = cluster_selection_epsilon
        self.metric = metric

        self._clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster_size,
            min_samples=min_samples,
            cluster_selection_epsilon=cluster_selection_epsilon,
            metric=metric,
            allow_single_cluster=False,
            prediction_data=True,
        )

    def cluster(
        self,
        embeddings: np.ndarray,
        quality_scores: Optional[np.ndarray] = None,
    ) -> ClusteringResult:
        """
        Cluster face embeddings with optional quality weighting.

        Args:
            embeddings: Array of shape (n_faces, 512) with face embeddings.
            quality_scores: Optional array of shape (n_faces,) with quality scores.

        Returns:
            ClusteringResult with labels and statistics.
        """
        if len(embeddings) == 0:
            return ClusteringResult(
                labels=np.array([]),
                probabilities=None,
                n_clusters=0,
                n_outliers=0,
                cluster_sizes={},
            )

        if len(embeddings) < self.min_cluster_size:
            # Not enough faces for clustering
            return ClusteringResult(
                labels=np.full(len(embeddings), -1),
                probabilities=None,
                n_clusters=0,
                n_outliers=len(embeddings),
                cluster_sizes={},
            )

        # Apply quality weighting if provided
        if quality_scores is not None:
            embeddings = self._apply_quality_weighting(embeddings, quality_scores)

        # Perform clustering
        try:
            self._clusterer.fit(embeddings)
            labels = self._clusterer.labels_
            probabilities = self._clusterer.probabilities_
        except Exception as e:
            logger.error(f"Clustering failed: {e}")
            # Return all as outliers on failure
            return ClusteringResult(
                labels=np.full(len(embeddings), -1),
                probabilities=None,
                n_clusters=0,
                n_outliers=len(embeddings),
                cluster_sizes={},
            )

        # Calculate statistics
        unique_labels = set(labels)
        outlier_count = np.sum(labels == -1)
        cluster_sizes = {}
        for label in unique_labels:
            if label != -1:
                cluster_sizes[label] = int(np.sum(labels == label))

        return ClusteringResult(
            labels=labels,
            probabilities=probabilities,
            n_clusters=len(cluster_sizes),
            n_outliers=outlier_count,
            cluster_sizes=cluster_sizes,
        )

    def _apply_quality_weighting(
        self, embeddings: np.ndarray, quality_scores: np.ndarray
    ) -> np.ndarray:
        """
        Apply quality-based weighting to embeddings.

        Low-quality faces get perturbed slightly to make them more likely
        to be classified as outliers. Uses a fixed seed for reproducibility.

        Args:
            embeddings: Face embeddings.
            quality_scores: Quality scores in range [0, 1].

        Returns:
            Weighted embeddings.
        """
        # Normalize quality scores
        quality_normalized = quality_scores / (quality_scores.max() + 1e-8)

        # Create noise inversely proportional to quality
        # Low quality = more noise = more likely to be outlier
        noise_scale = 0.1 * (1 - quality_normalized)
        
        # Use a fixed seed for deterministic weighting
        rng = np.random.default_rng(seed=42)
        noise = rng.normal(0, noise_scale[:, np.newaxis], embeddings.shape)

        # Add noise to embeddings
        weighted_embeddings = embeddings + noise

        return weighted_embeddings

    def predict(self, new_embeddings: np.ndarray) -> np.ndarray:
        """
        Predict cluster assignments for new embeddings.

        Args:
            new_embeddings: Array of shape (n_new, 512) with new embeddings.

        Returns:
            Cluster labels for new embeddings (-1 for outliers).
        """
        if not hasattr(self._clusterer, "_prediction_data"):
            raise RuntimeError("Clusterer has no prediction data. Call fit() first.")

        return self._clusterer.approximate_predict(new_embeddings)[0]

    def get_cluster_centers(self, embeddings: np.ndarray, labels: np.ndarray) -> dict[int, np.ndarray]:
        """
        Compute centroid for each cluster.

        Args:
            embeddings: Original embeddings.
            labels: Cluster labels.

        Returns:
            Dictionary mapping cluster ID to centroid embedding.
        """
        centers = {}
        unique_labels = set(labels)

        for label in unique_labels:
            if label == -1:
                continue
            mask = labels == label
            cluster_embeddings = embeddings[mask]
            if len(cluster_embeddings) > 0:
                centers[label] = np.mean(cluster_embeddings, axis=0)

        return centers
