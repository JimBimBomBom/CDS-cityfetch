"""
registry.py
-----------
OCI Registry operations using ORAS CLI.

Handles push/pull of data artifacts to/from GitHub Container Registry (GHCR)
and other OCI-compliant registries.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ORAS download URLs by platform
ORAS_DOWNLOAD_URLS = {
    "Linux": "https://github.com/oras-project/oras/releases/download/v1.1.0/oras_1.1.0_linux_amd64.tar.gz",
    "Darwin": "https://github.com/oras-project/oras/releases/download/v1.1.0/oras_1.1.0_darwin_amd64.tar.gz",
    "Windows": "https://github.com/oras-project/oras/releases/download/v1.1.0/oras_1.1.0_windows_amd64.zip",
}


@dataclass
class ArtifactMetadata:
    """Metadata for a data artifact stored in registry."""
    registry: str
    tag: str
    fetched_at: str
    record_count: int
    languages: list[str]
    format: str
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "ArtifactMetadata":
        return cls(**data)
    
    def save(self, path: Path) -> None:
        """Save metadata to JSON file."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
    
    @classmethod
    def load(cls, path: Path) -> "ArtifactMetadata":
        """Load metadata from JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))


class ORASClient:
    """Client for OCI registry operations using ORAS CLI."""
    
    def __init__(self, token: Optional[str] = None):
        self.token = token or os.environ.get("GITHUB_TOKEN")
        self.oras_path: Optional[Path] = None
        self._temp_dir: Optional[tempfile.TemporaryDirectory] = None
    
    def _ensure_oras(self) -> Path:
        """Ensure ORAS CLI is available, downloading if necessary."""
        # Check if ORAS is in PATH
        try:
            subprocess.run(["oras", "version"], capture_output=True, check=True)
            logger.debug("Using system ORAS installation")
            return Path("oras")
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        
        # Check if we already downloaded ORAS
        if self.oras_path and self.oras_path.exists():
            return self.oras_path
        
        # Download ORAS to temp directory
        logger.info("ORAS not found in PATH, downloading...")
        self._temp_dir = tempfile.TemporaryDirectory(prefix="cityfetch_oras_")
        temp_path = Path(self._temp_dir.name)
        
        system = platform.system()
        if system not in ORAS_DOWNLOAD_URLS:
            raise RuntimeError(f"Unsupported platform: {system}")
        
        download_url = ORAS_DOWNLOAD_URLS[system]
        archive_name = download_url.split("/")[-1]
        archive_path = temp_path / archive_name
        
        # Download archive
        import urllib.request
        logger.info(f"Downloading ORAS from {download_url}...")
        urllib.request.urlretrieve(download_url, archive_path)
        
        # Extract
        if archive_name.endswith(".tar.gz"):
            import tarfile
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extract("oras", path=temp_path)
        elif archive_name.endswith(".zip"):
            import zipfile
            with zipfile.ZipFile(archive_path, "r") as zip_ref:
                zip_ref.extract("oras.exe", path=temp_path)
        
        # Make executable (Unix)
        oras_bin = temp_path / ("oras.exe" if system == "Windows" else "oras")
        if system != "Windows":
            os.chmod(oras_bin, 0o755)
        
        self.oras_path = oras_bin
        logger.info(f"ORAS downloaded to {oras_bin}")
        return oras_bin
    
    def _run_oras(self, args: list[str], **kwargs) -> subprocess.CompletedProcess:
        """Run ORAS command with authentication."""
        oras_bin = self._ensure_oras()
        
        env = os.environ.copy()
        if self.token:
            env["ORAS_PASSWORD"] = self.token
        
        cmd = [str(oras_bin)] + args
        logger.debug(f"Running: {' '.join(cmd)}")
        
        return subprocess.run(cmd, capture_output=True, text=True, env=env, **kwargs)
    
    def pull(self, registry: str, tag: str, output_dir: Path) -> tuple[Path, ArtifactMetadata]:
        """
        Pull artifact from registry and extract data file.
        
        Args:
            registry: Registry URL (e.g., "ghcr.io/filip/cds-cityfetch")
            tag: Artifact tag (e.g., "en-latest")
            output_dir: Directory to extract data file
            
        Returns:
            Tuple of (data_file_path, metadata)
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Create temp directory for OCI layout
        with tempfile.TemporaryDirectory(prefix="cityfetch_pull_") as temp_dir:
            temp_path = Path(temp_dir)
            
            # Pull artifact to temp dir (maintains OCI structure)
            full_ref = f"{registry}:{tag}"
            logger.info(f"Pulling {full_ref}...")
            
            result = self._run_oras([
                "pull",
                "-o", str(temp_path),
                "--keep-old-files",
                full_ref
            ])
            
            if result.returncode != 0:
                raise RuntimeError(f"Failed to pull artifact: {result.stderr}")
            
            # Find the data file in the OCI structure
            # ORAS extracts blobs to temp_path, we need to find the actual SQL/JSON/CSV file
            data_files = list(temp_path.rglob("*"))
            data_file = None
            metadata_file = None
            
            for f in data_files:
                if f.is_file() and f.name.startswith("cities."):
                    if f.name.endswith(".meta"):
                        metadata_file = f
                    else:
                        data_file = f
            
            if not data_file:
                raise RuntimeError("No data file found in pulled artifact")
            
            # Determine format from extension
            format_ext = data_file.suffix.lstrip(".")
            
            # Read or create metadata
            if metadata_file:
                metadata = ArtifactMetadata.load(metadata_file)
            else:
                # Create minimal metadata from available info
                metadata = ArtifactMetadata(
                    registry=registry,
                    tag=tag,
                    fetched_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    record_count=0,  # Will be determined later
                    languages=[tag.split("-")[0]] if "-" in tag else [],
                    format=format_ext
                )
            
            # Copy data file to output directory
            output_file = output_dir / data_file.name
            import shutil
            shutil.copy2(data_file, output_file)
            
            # Copy metadata file if it exists
            if metadata_file:
                metadata_output = output_dir / f"{data_file.name}.meta"
                shutil.copy2(metadata_file, metadata_output)
            else:
                # Save generated metadata
                metadata_output = output_dir / f"{data_file.name}.meta"
                metadata.save(metadata_output)
            
            logger.info(f"Downloaded {data_file.name} to {output_dir}")
            return output_file, metadata
    
    def push(
        self,
        data_file: Path,
        registry: str,
        tag: str,
        metadata: ArtifactMetadata
    ) -> str:
        """
        Push data file to registry as OCI artifact.
        
        Args:
            data_file: Path to data file (SQL, JSON, or CSV)
            registry: Registry URL
            tag: Artifact tag
            metadata: Artifact metadata
            
        Returns:
            Full reference to pushed artifact
        """
        data_file = Path(data_file)
        if not data_file.exists():
            raise FileNotFoundError(f"Data file not found: {data_file}")
        
        # Create temp directory with OCI layout
        with tempfile.TemporaryDirectory(prefix="cityfetch_push_") as temp_dir:
            temp_path = Path(temp_dir)
            
            # Copy data file to temp dir
            temp_data = temp_path / data_file.name
            import shutil
            shutil.copy2(data_file, temp_data)
            
            # Save metadata file
            metadata_path = temp_path / f"{data_file.name}.meta"
            metadata.save(metadata_path)
            
            # Determine media type from file extension
            ext = data_file.suffix.lstrip(".")
            media_type = f"application/vnd.cityfetch.data.{ext}"
            
            # Build annotations
            annotations = {
                "org.opencontainers.image.title": data_file.name,
                "cityfetch.record_count": str(metadata.record_count),
                "cityfetch.languages": ",".join(metadata.languages),
                "cityfetch.format": metadata.format,
                "cityfetch.fetched_at": metadata.fetched_at,
            }
            
            # Build ORAS push command
            full_ref = f"{registry}:{tag}"
            logger.info(f"Pushing to {full_ref}...")
            
            # Create annotation file for ORAS
            annot_file = temp_path / "annotations.json"
            with open(annot_file, "w") as f:
                json.dump({"$config": annotations}, f)
            
            result = self._run_oras([
                "push",
                full_ref,
                f"{temp_data}:{media_type}",
                f"{metadata_path}:application/vnd.cityfetch.metadata+json",
                "--annotation-file", str(annot_file)
            ])
            
            if result.returncode != 0:
                raise RuntimeError(f"Failed to push artifact: {result.stderr}")
            
            logger.info(f"Successfully pushed to {full_ref}")
            return full_ref
    
    def list_tags(self, registry: str) -> list[str]:
        """List available tags in registry repository."""
        result = self._run_oras([
            "repo", "tags",
            registry
        ])
        
        if result.returncode != 0:
            raise RuntimeError(f"Failed to list tags: {result.stderr}")
        
        # Parse output (one tag per line)
        tags = [line.strip() for line in result.stdout.split("\n") if line.strip()]
        return tags
    
    def __del__(self):
        """Cleanup temp directory on destruction."""
        if self._temp_dir:
            self._temp_dir.cleanup()
