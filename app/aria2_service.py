"""
Aria2c service for Turbo downloads
Manages Aria2c RPC connections and download operations
"""
import subprocess
import json
import os
import logging
import time
import threading
from typing import List, Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

class Aria2Service:
    """Service for managing Aria2c downloads"""
    
    def __init__(self, download_dir: str = None, rpc_port: int = 6800, rpc_secret: str = ""):
        # Use absolute path in workspace or user's Downloads folder
        if download_dir is None:
            # Try workspace downloads first, then user Downloads
            workspace_downloads = Path(os.getcwd()) / "downloads"
            user_downloads = Path.home() / "Downloads" / "aria2_downloads"
            if workspace_downloads.exists() or os.access(os.getcwd(), os.W_OK):
                download_dir = str(workspace_downloads)
            else:
                download_dir = str(user_downloads)
        
        self.download_dir = Path(download_dir).resolve()  # Use absolute path
        try:
            self.download_dir.mkdir(parents=True, exist_ok=True)
            # Test write permissions
            test_file = self.download_dir / ".test_write"
            test_file.touch()
            test_file.unlink()
            logger.info(f"Aria2c download directory: {self.download_dir}")
        except (PermissionError, OSError) as e:
            logger.error(f"Cannot create/download to {self.download_dir}: {e}")
            # Fallback to user Downloads
            self.download_dir = Path.home() / "Downloads" / "aria2_downloads"
            self.download_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Using fallback download directory: {self.download_dir}")
        
        self.rpc_port = rpc_port
        self.rpc_secret = rpc_secret or os.environ.get("ARIA2_RPC_SECRET", "")  # Allow env var
        self.rpc_url = f"http://localhost:{rpc_port}/jsonrpc"
        self.aria2_process = None
        self.active_downloads = {}  # gid -> video_id mapping
        
        # Aria2c configuration (can be updated from dashboard)
        self.max_connections_per_server = 32  # Default: 32 connections per server
        self.split_count = 32  # Default: 32 splits
        self.max_concurrent_downloads = 20  # Default: 20 concurrent downloads
        self.min_split_size = "1M"  # Default: 1MB minimum split size
        
    def start_aria2c(self):
        """Start Aria2c daemon if not running"""
        try:
            # Check if aria2c is already running
            result = subprocess.run(
                ["aria2c", "--version"],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                logger.info("Aria2c is available")
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.warning("Aria2c not found in PATH. Please install aria2c.")
            return False
        
        # Try to start aria2c daemon
        try:
            cmd = [
                "aria2c",
                "--enable-rpc",
                f"--rpc-listen-port={self.rpc_port}",
                f"--dir={self.download_dir}",
                f"--max-connection-per-server={self.max_connections_per_server}",
                f"--split={self.split_count}",
                f"--min-split-size={self.min_split_size}",
                f"--max-concurrent-downloads={self.max_concurrent_downloads}",
                "--continue=true",
                "--auto-file-renaming=false",
            ]
            
            if self.rpc_secret:
                cmd.append(f"--rpc-secret={self.rpc_secret}")
            
            self.aria2_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            time.sleep(1)  # Wait for aria2c to start
            logger.info(f"Aria2c daemon started on port {self.rpc_port} with {self.max_concurrent_downloads} concurrent downloads, {self.max_connections_per_server} connections per server")
            return True
        except Exception as e:
            logger.error(f"Failed to start Aria2c: {e}")
            return False
    
    def _rpc_call(self, method: str, params: List = None) -> Optional[Dict]:
        """Make RPC call to Aria2c"""
        import requests
        
        if params is None:
            params = []
        
        # Build params array - token must be first if secret is set
        rpc_params = []
        if self.rpc_secret:
            rpc_params.append(f"token:{self.rpc_secret}")
        rpc_params.extend(params)
        
        payload = {
            "jsonrpc": "2.0",
            "id": str(int(time.time() * 1000)),  # Aria2c prefers string IDs
            "method": method,
            "params": rpc_params
        }
        
        try:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            response = requests.post(
                self.rpc_url,
                json=payload,
                headers=headers,
                timeout=10
            )
            
            # Log response for debugging
            if response.status_code != 200:
                logger.error(f"Aria2c RPC HTTP {response.status_code}: {response.text}")
                return None
            
            result = response.json()
            
            if "error" in result:
                error_info = result.get("error", {})
                logger.error(f"Aria2c RPC error: {error_info.get('code')} - {error_info.get('message')}")
                return None
            
            return result.get("result")
        except requests.exceptions.ConnectionError:
            logger.error(f"Aria2c RPC connection failed - is Aria2c running on port {self.rpc_port}?")
            return None
        except Exception as e:
            logger.error(f"Aria2c RPC call failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
    
    def add_download(self, url: str, video_id: int, filename: Optional[str] = None) -> Optional[str]:
        """
        Add download to Aria2c
        Returns GID (Global ID) if successful
        """
        if not self._rpc_call("aria2.getVersion"):
            if not self.start_aria2c():
                return None
        
        options = {
            "dir": str(self.download_dir),
            "max-connection-per-server": str(self.max_connections_per_server),
            "split": str(self.split_count),
            "min-split-size": self.min_split_size,
        }
        
        if filename:
            options["out"] = filename
        
        # Aria2c.addUri expects: [uris[], options{}, position?]
        # uris must be an array, even for single URL
        params = [[url], options]
        result = self._rpc_call("aria2.addUri", params)
        
        if result:
            gid = result
            self.active_downloads[gid] = video_id
            logger.info(f"Added download for video {video_id}, GID: {gid}")
            return gid
        
        return None
    
    def get_status(self, gid: str) -> Optional[Dict]:
        """Get download status by GID"""
        params = [gid]
        result = self._rpc_call("aria2.tellStatus", params)
        if result:
            # Validate downloaded file size
            result = self._validate_download(result)
        return result
    
    def get_all_status(self) -> List[Dict]:
        """Get status of all active downloads"""
        params = []
        result = self._rpc_call("aria2.tellActive", params)
        if result:
            # Validate all downloads
            result = [self._validate_download(dl) for dl in result]
        return result if result else []
    
    def get_stopped_downloads(self, limit: int = 50) -> List[Dict]:
        """Get stopped downloads (completed or failed)"""
        params = [0, limit]  # offset, num
        result = self._rpc_call("aria2.tellStopped", params)
        if result:
            # Validate all stopped downloads
            result = [self._validate_download(dl) for dl in result]
        return result if result else []
    
    def _validate_download(self, download: Dict) -> Dict:
        """Validate download - check if file size is reasonable (not just HTML error page)"""
        try:
            status = download.get("status", "")
            files = download.get("files", [])
            
            if status == "complete" and files:
                # Check file size
                for file_info in files:
                    completed_length = int(file_info.get("completedLength", 0))
                    length = int(file_info.get("length", 0))
                    actual_size = completed_length if completed_length > 0 else length
                    
                    # If file is too small (< 1MB), it's probably an error page or redirect
                    # Real videos should be at least 1MB, usually much more
                    if actual_size > 0 and actual_size < 1024 * 1024:  # Less than 1MB
                        logger.warning(f"Download {download.get('gid')} completed but file is too small ({actual_size} bytes) - likely error page")
                        # Mark as error
                        download["status"] = "error"
                        download["errorCode"] = "FILE_TOO_SMALL"
                        download["errorMessage"] = f"Downloaded file is too small ({self._format_bytes(actual_size)}) - likely an error page or redirect"
                        
                        # Try to remove the invalid file
                        try:
                            path = file_info.get("path", "")
                            if path and os.path.exists(path):
                                os.remove(path)
                                logger.info(f"Removed invalid download file: {path}")
                        except Exception as e:
                            logger.error(f"Failed to remove invalid file: {e}")
            
            return download
        except Exception as e:
            logger.error(f"Error validating download: {e}")
            return download
    
    def _format_bytes(self, bytes: int) -> str:
        """Format bytes to human readable format"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes < 1024.0:
                return f"{bytes:.1f} {unit}"
            bytes /= 1024.0
        return f"{bytes:.1f} TB"
    
    def pause_download(self, gid: str) -> bool:
        """Pause download"""
        params = [gid]
        result = self._rpc_call("aria2.pause", params)
        return result == gid
    
    def resume_download(self, gid: str) -> bool:
        """Resume download"""
        params = [gid]
        result = self._rpc_call("aria2.unpause", params)
        return result == gid
    
    def remove_download(self, gid: str) -> bool:
        """Remove download"""
        params = [gid]
        result = self._rpc_call("aria2.remove", params)
        if result == gid:
            self.active_downloads.pop(gid, None)
            return True
        return False
    
    def get_global_stat(self) -> Dict:
        """Get global statistics"""
        params = []
        result = self._rpc_call("aria2.getGlobalStat", params)
        return result if result else {}
    
    def update_config(self, max_connections: int = None, split_count: int = None, 
                     max_concurrent: int = None, min_split_size: str = None):
        """Update Aria2c configuration"""
        if max_connections is not None:
            self.max_connections_per_server = max_connections
        if split_count is not None:
            self.split_count = split_count
        if max_concurrent is not None:
            self.max_concurrent_downloads = max_concurrent
        if min_split_size is not None:
            self.min_split_size = min_split_size
        logger.info(f"Aria2c config updated: connections={self.max_connections_per_server}, splits={self.split_count}, concurrent={self.max_concurrent_downloads}")
    
    def get_config(self) -> Dict:
        """Get current Aria2c configuration"""
        return {
            "max_connections_per_server": self.max_connections_per_server,
            "split_count": self.split_count,
            "max_concurrent_downloads": self.max_concurrent_downloads,
            "min_split_size": self.min_split_size
        }


# Global instance
aria2_service = Aria2Service()

