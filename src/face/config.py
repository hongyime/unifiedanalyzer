"""Configuration module for Face Tracker application."""

from pydantic import Field, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional
import json


class DriveSource(BaseSettings):
    """Configuration for a drive source."""
    path: str
    type: str = "local"  # local, usb, onedrive
    priority: int = 1
    exclude: bool = False
    on_mount: bool = False


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # Storage Paths — accept HOST_FACE_STORAGE (.env) or FACE_STORAGE_ROOT (.env.example) for back-compat
    face_storage_root: str = Field(
        default="Y:/facetracker/faces",
        validation_alias=AliasChoices("FACE_STORAGE_ROOT", "HOST_FACE_STORAGE"),
    )
    postgres_data_path: str = "./postgres_data"
    app_root: str = "C:/facetracker"
    
    # Drive Sources
    drive_sources: List[DriveSource] = []
    exclude_paths: List[str] = []
    # Directory-name blacklist (case-insensitive). Unlike `exclude_paths`
    # which is a prefix match against the full file path, these match the
    # last path segment regardless of where it appears in the tree. Use
    # for known-junk directories that scatter throughout drives:
    # OS metadata (.thumbnails, __MACOSX, @eaDir), browser/app caches,
    # framework artifacts (node_modules, .git already covered separately
    # via path containment but explicit here is faster).
    exclude_dir_names: List[str] = [
        ".thumbnails",
        ".thumbs",
        "__MACOSX",
        "@eaDir",          # Synology NAS metadata
        ".Trash-1000",
        ".Trash",
        ".trashes",
        ".DS_Store",
        "Thumbs.db",
        ".cache",
        ".tmp",
        "Temporary Internet Files",
    ]

    # Smallest file we'll consider for face detection. Below this is
    # almost always icons, sprites, favicons, sidecars, .DS_Store,
    # Plex metadata thumbnails, etc. Plex covers are ~30-80 KB so 4 KB
    # is well below any real cover and well above all known junk.
    # 0 disables the filter.
    min_file_size_bytes: int = 4096
    
    # Media Formats
    supported_images: str = ".jpg,.jpeg,.png,.webp,.gif,.bmp,.heic,.heif,.tiff,.tif"
    supported_raw: str = ".cr2,.cr3,.nef,.arw,.orf,.rw2,.dng,.raf"
    supported_videos: str = ".mp4,.mov,.m4v,.avi,.mkv,.wmv,.webm,.flv,.3gp"
    
    # Face Detection
    min_face_area_percent: float = 5.0
    min_laplacian_variance: float = 100.0
    min_detection_confidence: float = 0.5
    crop_margin_percent: float = 15.0
    thumbnail_size: int = 64
    thumbnail_quality: int = 80
    
    # Video Processing
    video_tracking_fps: int = 3
    video_embedding_fps: int = 1
    deepsort_max_age: int = 30
    deepsort_n_init: int = 1
    
    # FAISS Indexing
    faiss_staging_size: int = 10000
    faiss_merge_timeout: int = 300
    # Index type. Two values supported:
    #   "HNSW64"  - legacy graph index (good search, O(N) persistence cost,
    #               viable to ~30k vectors)
    #   "IVFFlat" - clustered inverted-file index (O(1) add, smaller writes,
    #               viable to millions). Switch via config + run the
    #               scripts/faiss_migrate_ivf.py migration once.
    faiss_index_type: str = "IVFFlat"  # production uses IVFFlat; HNSW64 retained as fallback option
    # IVFFlat tuning. Only used when faiss_index_type == "IVFFlat".
    #   nlist : number of Voronoi cells. Rule of thumb K = 4*sqrt(N).
    #           512 is appropriate for ~16k-64k vectors. Bump to 4096 once
    #           total faces crosses ~250k. nlist requires nlist*8 training
    #           points minimum (FAISS hard floor) — picking too high here
    #           will block the migration script.
    #   nprobe: cells to scan at query time. K/16 is a typical recall
    #           sweet spot (~95-98% recall on face embeddings).
    faiss_ivf_nlist: int = 512
    faiss_ivf_nprobe: int = 32

    # FAISS outbox reaper (Phase 2 — outbox pattern)
    faiss_reaper_poll_ms: int = 500            # how often the reaper wakes
    faiss_reaper_batch_size: int = 2048        # rows claimed per cycle (was 256; bumped to amortize whole-index rewrite cost)
    faiss_reaper_stuck_timeout_s: int = 120    # reclaim 'merging' rows older than this
    faiss_reaper_max_attempts: int = 5         # park as 'failed' after this many attempts
    
    # OneDrive
    onedrive_enabled: bool = True
    onedrive_download_timeout: int = 300
    onedrive_revert_verify: bool = True
    onedrive_multi_detect: bool = True
    onedrive_max_retries: int = 3
    
    # Indexing
    watch_mode: bool = True
    watch_poll_interval: int = 30  # minutes between full drive scans; manager.py multiplies by 60
    index_queue_size: int = 50
    index_workers: int = 1  # ONNX intra-op pool saturates CPU at workers>1 on 4-core hardware; benchmark before increasing
    
    # Clustering
    cluster_min_size: int = 5
    cluster_quality_weighting: bool = True
    auto_merge_threshold: float = 0.75
    user_verify_threshold: float = 0.60
    max_undo_stack: int = 10
    
    # Search
    search_top_k: int = 100
    search_min_similarity: float = 0.6
    # search_cache_enabled / redis_host / redis_port removed — Redis was never
    # wired into any code path. Remove redis container from docker-compose

    # Identity clustering
    # Cosine similarity threshold for Chinese Whispers and incremental assignment.
    # 0.6 = strict (fewer false merges). Lower to ~0.5 to merge more aggressively.
    identity_cluster_threshold: float = 0.6
    # if caching is not being implemented.
   
    # API authentication
    # Static bearer token for /api/v1 routes. Empty string disables auth.
    api_token: str = ""

    # Database
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "facetracker"
    postgres_user: str = "postgres"
    postgres_password: str = "changeme"
    
    # Dashboard
    dashboard_port: int = 5151
    
    # Logging
    log_level: str = "INFO"
    verbose_status: bool = True
    status_update_interval: int = 5
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # tolerate forward-compat keys / drift between .env and Settings
        populate_by_name=True,
    )
    
    @property
    def image_extensions(self) -> List[str]:
        """Get list of supported image extensions."""
        return [ext.strip() for ext in self.supported_images.split(",")]
    
    @property
    def raw_extensions(self) -> List[str]:
        """Get list of supported raw extensions."""
        return [ext.strip() for ext in self.supported_raw.split(",")]
    
    @property
    def video_extensions(self) -> List[str]:
        """Get list of supported video extensions."""
        return [ext.strip() for ext in self.supported_videos.split(",")]
    
    @property
    def all_supported_extensions(self) -> List[str]:
        """Get all supported file extensions."""
        return (
            self.image_extensions + 
            self.raw_extensions + 
            self.video_extensions
        )
    
    @property
    def database_url(self) -> str:
        """Get PostgreSQL database URL."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )
    
    @property
    def thumbnail_cache_path(self) -> str:
        """Get thumbnail cache path."""
        return f"{self.face_storage_root}/media/thumbnails"
    
    @property
    def faiss_live_path(self) -> str:
        """Get FAISS live index path."""
        return f"{self.face_storage_root}/embeddings/live/face_index.faiss"
    
    @property
    def faiss_staging_dir(self) -> str:
        """Get FAISS staging directory."""
        return f"{self.face_storage_root}/embeddings/staging"


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """Get the global settings instance."""
    return settings
