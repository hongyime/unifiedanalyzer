"""Utility functions for hashing operations."""

import hashlib
from pathlib import Path


def compute_file_hash(file_path: Path) -> str:
    """
    Compute SHA256 hash of a file.
    
    Args:
        file_path: Path to the file
        
    Returns:
        Hexadecimal hash string
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def compute_content_hash(content: bytes) -> str:
    """
    Compute SHA256 hash of content bytes.
    
    Args:
        content: Bytes to hash
        
    Returns:
        Hexadecimal hash string
    """
    return hashlib.sha256(content).hexdigest()
