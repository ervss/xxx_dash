import os
import ffmpeg
import concurrent.futures
import urllib.parse
import yt_dlp
import glob
import logging
import subprocess
from sqlalchemy.orm import Session
from .database import Video, SessionLocal
from .websockets import manager # Import the manager
import re
import requests
from bs4 import BeautifulSoup
import time
import httpx
import json
import shutil
from sqlalchemy import func
from collections import Counter
import asyncio

# --- Eporner API import ---
def fetch_eporner_videos(query=None, page=1, per_page=20, tags=None, gay=None, hd=None, pornstar=None, order=None):
    """
    Fetch videos from Eporner API v2.
    Returns list of video dicts with title, url (page), video_url (direct), thumbnail, duration.
    """
    base_url = "https://www.eporner.com/api/v2/video/search/"
    params = { "query": query or "", "per_page": per_page, "page": page }
    if tags: params["tags"] = tags
    if gay is not None: params["gay"] = int(bool(gay))
    if hd is not None: params["hd"] = int(bool(hd))
    if pornstar: params["pornstar"] = pornstar
    if order: params["order"] = order
    try:
        resp = requests.get(base_url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logging.error(f"Eporner API Error: {e}")
        return []

    videos = []
    for v in data.get("videos", []):
        # Extract video_url from API response - try multiple possible field names
        video_url = v.get("video_url") or v.get("videoUrl") or v.get("mp4") or v.get("url_mp4")
        
        # If no direct video_url, use the page URL (will be processed by VIPVideoProcessor)
        if not video_url:
            video_url = v.get("url")
        
        videos.append({
            "title": v.get("title"),
            "url": v.get("url"),  # Page URL
            "video_url": video_url,  # Direct video URL or page URL as fallback
            "thumbnail": v.get("default_thumb", {}).get("src") if isinstance(v.get("default_thumb"), dict) else v.get("default_thumb"),
            "duration": v.get("length_sec") or v.get("length_min") or 0,
            "embed_url": v.get("embed")
        })
    return videos

def fetch_eporner_playlist(playlist_url):
    """
    Extract video URLs from an Eporner playlist page.
    Returns list of video dicts similar to fetch_eporner_videos.
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        resp = requests.get(playlist_url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        videos = []
        # Find all video links on the playlist page
        # Eporner playlist pages typically have links like /video/ID/title
        for link in soup.find_all('a', href=True):
            href = link.get('href', '')
            if '/video/' in href and href.startswith('/video/'):
                # Construct full URL
                video_url = urllib.parse.urljoin(playlist_url, href)
                title = link.get_text(strip=True) or link.get('title', '')
                
                # Try to find thumbnail
                thumbnail = None
                img = link.find('img')
                if img:
                    thumbnail = img.get('src') or img.get('data-src')
                    if thumbnail and not thumbnail.startswith('http'):
                        thumbnail = urllib.parse.urljoin(playlist_url, thumbnail)
                
                videos.append({
                    "title": title or "Untitled",
                    "url": video_url,
                    "video_url": video_url,  # Will be processed by VIPVideoProcessor
                    "thumbnail": thumbnail,
                    "duration": 0,
                    "embed_url": None
                })
        
        # Remove duplicates
        seen_urls = set()
        unique_videos = []
        for v in videos:
            if v["url"] not in seen_urls:
                seen_urls.add(v["url"])
                unique_videos.append(v)
        
        return unique_videos
    except Exception as e:
        logging.error(f"Eporner Playlist Error: {e}")
        return []

# --- Konfigurácia ---

THUMB_DIR = "app/static/thumbnails"
PREVIEW_DIR = "app/static/previews"
SUBTITLE_DIR = "app/static/subtitles"
os.makedirs(THUMB_DIR, exist_ok=True)
os.makedirs(PREVIEW_DIR, exist_ok=True)
os.makedirs(SUBTITLE_DIR, exist_ok=True)

FFMPEG_CMD = os.path.join(os.getcwd(), 'ffmpeg.exe')
FFPROBE_CMD = FFMPEG_CMD.replace('ffmpeg.exe', 'ffprobe.exe')

if not os.path.exists(FFMPEG_CMD):
    logging.warning(f"CRITICAL WARNING: ffmpeg.exe not found at {FFMPEG_CMD}.")

FFMPEG_NETWORK_ARGS = [
    '-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '10',
    '-timeout', '20000000', '-user_agent', 'Mozilla/5.0'
]

import spacy
try: NLP = spacy.load('en_core_web_sm')
except OSError: NLP = None

# --- Hlavná trieda ---

class VIPVideoProcessor:
    async def _broadcast_status(self, video_id: int, status: str, extra_data: dict = None):
        message = {"type": "status_update", "video_id": video_id, "status": status}
        if extra_data:
            message.update(extra_data)
        await manager.broadcast(json.dumps(message))

    def process_batch(self, video_ids: list[int], import_speed: str = "default"):
        # Note: This is synchronous. For fully async, this would need a task queue.
        for video_id in video_ids:
            self.process_single_video(video_id, import_speed=import_speed)

    def process_single_video(self, video_id, force=False, quality_mode="mp4", extractor="auto", import_speed="default"):
        print(f"VIPVideoProcessor: Processing ID {video_id}...")
        db = SessionLocal()
        try:
            video = db.query(Video).get(video_id)
            if not video: return

            thumb_exists = video.thumbnail_path and os.path.exists(f"app{video.thumbnail_path}")
            if not force and video.status == 'ready' and thumb_exists:
                db.close(); return

            video.status = "processing"
            db.commit()
            asyncio.run(self._broadcast_status(video_id, "processing"))

            # Check if this is a local file (not a remote URL)
            is_local_file = (video.url.startswith('/static/') or 
                           video.url.startswith('./') or 
                           (not video.url.startswith('http://') and not video.url.startswith('https://')))
            
            is_direct_file = (is_local_file or 
                            any(video.url.lower().endswith(ext) for ext in ['.mp4', '.mkv', '.avi', '.mov', '.webm']))
            is_pixeldrain = "pixeldrain.com" in video.url
            is_xvideos = "xvideos.com" in video.url

            pd_id = None
            if is_pixeldrain:
                match = re.search(r'/file/([a-zA-Z0-9]+)', video.url)
                if match: pd_id = match.group(1)

            meta = {}
            stream_url = video.url 
            yt_id = None
            
            # --- METADATA EXTRACTION ---
            
            # 1. Custom Scraper for XVIDEOS
            if is_xvideos and extractor == 'auto':
                xv_meta, xv_stream_url = self._fetch_xvideos_meta(video.url)
                if xv_stream_url:
                    stream_url = xv_stream_url
                    video.url = xv_stream_url # Update URL so proxy works
                    meta.update(xv_meta)
                    logging.info(f"Xvideos scraper success for {video_id}")

            # 1.5 Generic Scraper (skip for local files)
            if not is_local_file and not is_direct_file and not stream_url and extractor == 'auto':
                logging.info(f"Running generic scraper for {video.url}")
                scraped_meta, scraped_stream_url = asyncio.run(self._scrape_generic_video_page(video.url))
                if scraped_stream_url:
                    stream_url = scraped_stream_url
                    video.url = scraped_stream_url  # Update URL to the direct stream
                    meta.update(scraped_meta)
                    logging.info(f"Generic scraper success for {video_id}, found stream: {stream_url}")

            # 2. Pixeldrain API
            if is_pixeldrain and pd_id:
                pd_info = self._fetch_pixeldrain_info_api(pd_id)
                if pd_info and pd_info.get("name"):
                    meta['title'] = pd_info['name']

            # 3. Generic Fallback (yt-dlp) - SKIP for local files and turbo mode
            # Run if custom scrapers failed to get a title OR if Xvideos scraper failed to get a stream URL
            # Turbo mode: Skip yt-dlp completely
            # Fast mode: Only run yt-dlp if title is missing
            if import_speed != "turbo" and not is_local_file and (not meta.get('title') or (is_xvideos and stream_url == video.url)):
                should_run_ytdlp = False
                if extractor == "yt-dlp": should_run_ytdlp = True
                elif extractor == "auto" and not is_pixeldrain and not is_direct_file:
                    # Fast mode: Only run yt-dlp if title is missing
                    if import_speed == "fast":
                        should_run_ytdlp = not meta.get('title')
                    else:  # default mode
                        should_run_ytdlp = True

                if should_run_ytdlp:
                    try:
                        info_id = yt_dlp.YoutubeDL({'quiet': True, 'extract_flat': True, 'ignoreerrors': True}).extract_info(video.url, download=False)
                        yt_id = info_id.get('id') if info_id else None
                        
                        dlp_meta, fetched_stream_url = self._fetch_metadata(video.url, yt_id, quality_mode)
                        
                        if fetched_stream_url and not meta.get('stream_url'): # Don't overwrite if scraper got it
                            stream_url = fetched_stream_url
                        
                        meta.update(dlp_meta)
                    except Exception as e: logging.warning(f"yt-dlp failed: {e}")
            
            # 3. FFprobe - Skip in turbo mode if we have basic metadata
            if import_speed == "turbo" and meta.get('duration'):
                pass  # Skip ffprobe if we already have duration
            elif not meta.get('duration') or extractor == "ffprobe":
                meta = self._ffprobe_fallback(stream_url, meta)

            # 4. Názov a Tagy
            new_title = meta.get('title')
            if new_title and not ("hls-" in new_title or new_title.startswith("video")): video.title = new_title
            
            if not video.title or "Queued" in video.title or len(video.title) < 3:
                 path_title = self._extract_title_from_url(video.url)
                 video.title = path_title if path_title else f"Video #{video_id}"

            video.duration = meta.get('duration') or 0
            video.width = meta.get('width') or 0
            video.height = meta.get('height') or 0
            
            if not video.tags: video.tags = meta.get('tags') or self._generate_smart_tags(video.title)
            video.ai_tags = self._generate_ai_tags(video.title, meta.get('description', ''))
            
            # Subtitles - Skip in turbo/fast mode
            if import_speed != "turbo" and import_speed != "fast" and not is_local_file and not is_direct_file and yt_id:
                video.subtitle = self._read_and_clean_vtt(yt_id)

            # 5. Vizuály - Different strategies based on import_speed
            visuals_ok = False
            
            # Turbo mode: Only try scraper thumbnail, skip ffmpeg generation
            if import_speed == "turbo":
                if meta.get('thumbnail_url'):
                    try:
                        thumb_resp = requests.get(meta['thumbnail_url'], timeout=5)  # Shorter timeout
                        if thumb_resp.status_code == 200:
                            thumb_path = os.path.join(THUMB_DIR, f"thumb_{video_id}.jpg")
                            with open(thumb_path, 'wb') as f:
                                f.write(thumb_resp.content)
                            visuals_ok = True
                    except Exception as e:
                        logging.warning(f"Failed to download thumbnail from URL: {e}")
                # Skip ffmpeg thumbnail generation in turbo mode
            else:
                # Default and Fast mode: Full thumbnail generation
                # Use scraper thumbnail if available
                if meta.get('thumbnail_url'):
                    try:
                        thumb_resp = requests.get(meta['thumbnail_url'], timeout=10)
                        if thumb_resp.status_code == 200:
                            thumb_path = os.path.join(THUMB_DIR, f"thumb_{video_id}.jpg")
                            with open(thumb_path, 'wb') as f:
                                f.write(thumb_resp.content)
                            visuals_ok = True
                    except Exception as e:
                        logging.error(f"Failed to download thumbnail from URL {meta['thumbnail_url']}: {e}")

                if not visuals_ok and is_pixeldrain and pd_id and extractor == "auto":
                    if self._download_pixeldrain_thumbnail(video_id, pd_id): visuals_ok = True
                
                if not visuals_ok:
                    try:
                        # Fast mode: Skip GIF generation
                        self._generate_visuals(stream_url, video_id, video.duration, skip_gif=(import_speed == "fast"))
                        visuals_ok = True
                    except Exception as e: logging.error(f"Visuals gen failed for {video_id}: {e}")

            if os.path.exists(os.path.join(THUMB_DIR, f"thumb_{video_id}.jpg")):
                video.thumbnail_path = f"/static/thumbnails/thumb_{video_id}.jpg"
            
            # GIF preview - Skip in fast and turbo mode
            if import_speed != "fast" and import_speed != "turbo":
                gif_preview_path = os.path.join(THUMB_DIR, f"thumb_{video_id}.gif")
                if os.path.exists(gif_preview_path):
                    video.gif_preview_path = f"/static/thumbnails/thumb_{video_id}.gif"

            video.preview_path = f"/static/previews/{video_id}"
            video.status = "ready"
            video.error_msg = None
            db.commit()

            # Broadcast READY status with final data
            asyncio.run(self._broadcast_status(video_id, "ready", {
                "title": video.title,
                "thumbnail_path": video.thumbnail_path
            }))

        except Exception as e:
            logging.error(f"Error processing video {video_id}: {e}")
            video.status = "error"
            video.error_msg = str(e)
            db.commit()
            # Broadcast ERROR status
            asyncio.run(self._broadcast_status(video_id, "error", {"error": str(e)}))
        finally: 
            db.close()

    # --- POMOCNÉ METÓDY ---

    def _fetch_pixeldrain_info_api(self, pd_id):
        try:
            url = f"https://pixeldrain.com/api/file/{pd_id}/info"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200: return resp.json()
        except: pass
        return None

    def extract_xvideos_metadata(self, url):
        """
        Specialized extractor for XVideos using yt-dlp as parser (no download).
        Returns JSON metadata with best HLS stream.
        """
        import yt_dlp
        
        # Options matching the requirements
        ydl_opts = {
            'quiet': True,
            'skip_download': True,
            'dump_single_json': True,
            'extract_flat': False, # We need full info to get formats
            'format': 'bv*', # We will filter manually later, but this hints preference
            'cookiefile': 'xvideos.cookies.txt',
            'ignoreerrors': True,
            'no_warnings': True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return None

                # Extract basic metadata
                video_id = info.get('id')
                title = info.get('title')
                duration = info.get('duration')
                thumbnail = info.get('thumbnail')
                
                # Find best HLS stream
                formats = info.get('formats', [])
                hls_formats = []
                
                for f in formats:
                    # Check for HLS m3u8
                    if 'm3u8' in f.get('protocol', '') or 'm3u8' in f.get('ext', '') or 'hls' in f.get('format_id', '').lower():
                        hls_formats.append(f)
                
                best_hls = None
                if hls_formats:
                    # Sort by height (resolution) descending
                    hls_formats.sort(key=lambda x: x.get('height', 0) or 0, reverse=True)
                    best_hls = hls_formats[0]
                
                if not best_hls:
                    # Fallback: check if 'url' in info points to m3u8 directly (sometimes happens)
                    if info.get('url', '').endswith('.m3u8'):
                         best_hls = {'url': info['url'], 'height': info.get('height'), 'fps': info.get('fps')}
                    else:
                        return None # Requirement: "Pre XVideos VŽDY: používať HLS"

                return {
                    "source": "xvideos",
                    "id": video_id,
                    "title": title,
                    "duration": duration,
                    "thumbnail": thumbnail,
                    "stream": {
                        "type": "hls",
                        "url": best_hls.get('url'),
                        "height": best_hls.get('height'),
                        "fps": best_hls.get('fps')
                    }
                }
        except Exception as e:
            logging.error(f"XVideos extraction failed for {url}: {e}")
            return None

    def _fetch_xvideos_meta(self, url):
        try:
            # Enhanced User-Agent to avoid mobile redirection or anti-bot blocks
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'html.parser')

            meta = {}
            stream_url = None

            # --- Title ---
            title_tag = soup.select_one('h2.page-title, .video-title h1 strong')
            if title_tag:
                meta['title'] = title_tag.text.strip()
            
            script_content = ""
            scripts = soup.find_all('script')
            for script in scripts:
                # Check script.string or script.get_text() if string is empty
                content = script.string or script.get_text()
                if content and 'html5player.setVideoTitle' in content:
                    script_content = content
                    break
            
            if script_content:
                # Robust regex patterns (handling single/double quotes and spaces)
                
                # HLS (Preferred for quality)
                match_hls = re.search(r"html5player\.setVideoHLS\s*\(\s*['\"]([^'\"]+)['\"]\s*\);", script_content)
                if match_hls:
                    stream_url = match_hls.group(1)

                # High Quality Fallback
                if not stream_url:
                    match_high = re.search(r"html5player\.setVideoUrlHigh\s*\(\s*['\"]([^'\"]+)['\"]\s*\);", script_content)
                    if match_high:
                        stream_url = match_high.group(1)
                
                # Low Quality Fallback
                if not stream_url:
                    match_low = re.search(r"html5player\.setVideoUrlLow\s*\(\s*['\"]([^'\"]+)['\"]\s*\);", script_content)
                    if match_low:
                        stream_url = match_low.group(1)

                # Duration
                duration_match = re.search(r"html5player\.setVideoDuration\s*\(\s*([\d\.]+)\s*\);", script_content)
                if duration_match:
                    meta['duration'] = int(float(duration_match.group(1)))

                # Thumbnail
                # Try setThumbUrl169 first (often higher res)
                thumb_match = re.search(r"html5player\.setThumbUrl169\s*\(\s*['\"]([^'\"]+)['\"]\s*\);", script_content)
                if thumb_match:
                     meta['thumbnail_url'] = thumb_match.group(1)
                else:
                    # Fallback to setThumbUrl
                    thumb_match = re.search(r"html5player\.setThumbUrl\s*\(\s*['\"]([^'\"]+)['\"]\s*\);", script_content)
                    if thumb_match:
                         meta['thumbnail_url'] = thumb_match.group(1)
                    else:
                        # Fallback to setPoster (old)
                        thumb_match = re.search(r"html5player\.setPoster\s*\(\s*['\"]([^'\"]+)['\"]\s*\);", script_content)
                        if thumb_match:
                             meta['thumbnail_url'] = thumb_match.group(1)

            # Fallback for title
            if not meta.get('title'):
                title_og = soup.find('meta', property='og:title')
                if title_og: meta['title'] = title_og['content']

            return meta, stream_url
        except Exception as e:
            logging.warning(f"Xvideos scraping failed for {url}: {e}")
            return {}, None

    async def _scrape_generic_video_page(self, url):
        meta = {}
        stream_url = None
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        
        try:
            async with httpx.AsyncClient(http2=True, timeout=20, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, 'html.parser')

                # --- Find Title ---
                og_title = soup.find('meta', property='og:title')
                if og_title and og_title.get('content'):
                    meta['title'] = og_title['content']
                else:
                    title_tag = soup.find('title')
                    if title_tag:
                        meta['title'] = title_tag.text.strip()

                # --- Find Video Stream ---
                # Look for <video> tag src
                video_tag = soup.find('video')
                if video_tag and video_tag.get('src'):
                    stream_url = urllib.parse.urljoin(url, video_tag['src'])
                
                # Look for HLS (.m3u8) links if no video tag found
                if not stream_url:
                    links = soup.find_all('a', href=True)
                    for link in links:
                        if '.m3u8' in link['href']:
                            stream_url = urllib.parse.urljoin(url, link['href'])
                            break
                
                # Fallback: Look for any MP4 links
                if not stream_url:
                    links = soup.find_all('a', href=True)
                    for link in links:
                        if '.mp4' in link['href']:
                            stream_url = urllib.parse.urljoin(url, link['href'])
                            break
                            
        except Exception as e:
            logging.error(f"Generic scraping failed for {url}: {e}")

        return meta, stream_url

    def _read_and_clean_vtt(self, yt_id):
        try:
            vtt_path = os.path.join(SUBTITLE_DIR, f"{yt_id}.en.vtt")
            if not os.path.exists(vtt_path): return ""
            with open(vtt_path, 'r', encoding='utf-8') as f: content = f.read()
            lines = content.splitlines()
            clean_lines = []
            for line in lines:
                if line.strip().startswith('WEBVTT') or '-->' in line or line.strip().startswith('Kind:') or line.strip().startswith('Language:'): continue
                line = re.sub(r'<[^>]+>', '', line)
                clean_lines.append(line.strip())
            return " ".join(clean_lines)
        except: return ""

    def _download_pixeldrain_thumbnail(self, video_id, pd_id):
        thumb_url = f"https://pixeldrain.com/api/file/{pd_id}/thumbnail"
        target_path = os.path.join(THUMB_DIR, f"thumb_{video_id}.jpg")
        try:
            resp = requests.get(thumb_url, timeout=5)
            if resp.status_code == 200:
                with open(target_path, 'wb') as f: f.write(resp.content)
                preview_base = os.path.join(PREVIEW_DIR, f"{video_id}_")
                for i in range(4): shutil.copy(target_path, f"{preview_base}{i}.jpg")
                return True
        except: pass
        return False

    def _extract_title_from_url(self, url):
        try:
            path = urllib.parse.urlparse(url).path
            basename = os.path.basename(path)
            if "pixeldrain" in url:
                decoded = urllib.parse.unquote(basename)
                if len(decoded) > 3: return decoded
            title = os.path.splitext(basename)[0]
            return title.replace('_', ' ').replace('-', ' ').title()
        except: return None

    def _fetch_metadata(self, url, yt_id, quality_mode='mp4'):
        meta = {}
        stream_url = None
        try:
            subtitle_path_template = os.path.join(SUBTITLE_DIR, f'{yt_id}.%(ext)s') if yt_id else os.path.join(SUBTITLE_DIR, 'subtitle.%(ext)s')
            fmt = 'bestvideo+bestaudio/best'
            opts = {
                'quiet': True, 'skip_download': True, 'ignoreerrors': True, 'socket_timeout': 15,
                'ffmpeg_location': FFMPEG_CMD,
                'format': fmt,
                'writesubtitles': True, 'writeautomaticsub': True,
                'subtitleslangs': ['en'],
                'outtmpl': subtitle_path_template,
                'http_headers': {'User-Agent': 'Mozilla/5.0'}
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    stream_url = info.get('url') 
                    meta.update({
                        'title': info.get('title'), 'description': info.get('description'),
                        'duration': info.get('duration'), 'width': info.get('width'),
                        'height': info.get('height'), 'tags': ",".join(info.get('tags', []))
                    })
        except Exception as e:
            logging.warning(f"Failed to fetch metadata with yt-dlp for {url}: {e}")
        return meta, stream_url

    def _ffprobe_fallback(self, url, meta):
        try:
            if not os.path.exists(FFPROBE_CMD): return meta
            
            # Convert local file paths to absolute paths for ffprobe
            probe_url = url
            if url.startswith('/static/') or (not url.startswith('http://') and not url.startswith('https://')):
                # Local file - convert to absolute path
                if url.startswith('/static/'):
                    probe_url = os.path.abspath(os.path.join(os.getcwd(), url.lstrip('/')))
                else:
                    probe_url = os.path.abspath(url) if not os.path.isabs(url) else url
                
                # Use local file args (no network args needed)
                cmd = [FFPROBE_CMD, '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height,duration',
                       '-of', 'json', '-analyzeduration', '10000000', '-probesize', '10000000', probe_url]
            else:
                # Remote URL - use network args
                cmd = [FFPROBE_CMD] + FFMPEG_NETWORK_ARGS + [
                    '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height,duration',
                    '-of', 'json', '-analyzeduration', '10000000', '-probesize', '10000000', probe_url
                ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            data = json.loads(result.stdout)
            if 'streams' in data and len(data['streams']) > 0:
                stream = data['streams'][0]
                meta['width'] = int(stream.get('width', 0))
                meta['height'] = int(stream.get('height', 0))
                dur = stream.get('duration')
                meta['duration'] = float(dur) if dur else 0
        except Exception as e: logging.error(f"ffprobe failed: {e}")
        return meta
    
    def _generate_visuals(self, url, vid_id, duration, skip_gif=False):
        if not os.path.exists(FFMPEG_CMD): return
        thumb_out = os.path.join(THUMB_DIR, f"thumb_{vid_id}.jpg")
        
        # Convert local file paths to absolute paths for ffmpeg
        input_url = url
        is_local = url.startswith('/static/') or (not url.startswith('http://') and not url.startswith('https://'))
        
        if is_local:
            # Local file - convert to absolute path
            if url.startswith('/static/'):
                input_url = os.path.abspath(os.path.join(os.getcwd(), url.lstrip('/')))
            else:
                input_url = os.path.abspath(url) if not os.path.isabs(url) else url
            # Use local file args (no network args needed)
            common_args = [FFMPEG_CMD, '-y', '-hide_banner', '-loglevel', 'error']
        else:
            # Remote URL - use network args
            common_args = [FFMPEG_CMD, '-y', '-hide_banner', '-loglevel', 'error'] + FFMPEG_NETWORK_ARGS

        # Generate thumbnail (always)
        if duration > 10:
            try:
                cmd = common_args + ['-ss', str(duration * 0.1), '-i', input_url, '-vf', "scale=640:-1", '-vframes', '1', '-q:v', '5', thumb_out]
                subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=60)
            except: pass
        elif duration >= 0: 
             try:
                cmd = common_args + ['-i', input_url, '-vf', "thumbnail,scale=640:-1", '-frames:v', '1', thumb_out]
                subprocess.run(cmd, capture_output=True, check=True, timeout=40)
             except: pass

        # Generate GIF preview (skip if skip_gif=True)
        if not skip_gif:
            gif_out = os.path.join(THUMB_DIR, f"thumb_{vid_id}.gif")
            if duration > 5:
                try:
                    gif_cmd = common_args + ['-ss', str((duration * 0.2) - 1), '-t', '2', '-i', input_url, '-filter_complex', '[0:v] scale=320:-1,split [a][b];[a] palettegen [p];[b][p] paletteuse', '-loop', '0', gif_out]
                    subprocess.run(gif_cmd, capture_output=True, text=True, check=True, timeout=45)
                except: pass

    def _generate_smart_tags(self, title):
        if not title: return ""
        tags = []
        for k in ['4k', 'hd', 'vlog', 'gameplay', 'pov', 'asmr']:
            if k in title.lower(): tags.append(k)
        return ",".join(tags)

    def _generate_ai_tags(self, title, description):
        if not NLP or not title: return ""
        try:
            doc = NLP(title[:200])
            tags = {token.lemma_.lower() for token in doc if token.pos_ == 'NOUN' and not token.is_stop}
            return ",".join(list(tags)[:10])
        except: return ""

def search_videos_by_subtitle(query: str, db: Session):
    return db.query(Video).filter(Video.subtitle.contains(query)).all()

def get_batch_stats(db: Session):
    results = db.query(Video.batch_name, func.count(Video.id)).group_by(Video.batch_name).all()
    return [{"label": r[0] or "Uncategorized", "value": r[1]} for r in results]

def get_tags_stats(db: Session):
    all_tags = []
    videos = db.query(Video.tags, Video.ai_tags).all()
    for v_tags, v_ai_tags in videos:
        if v_tags: all_tags.extend(t.strip() for t in v_tags.split(','))
        if v_ai_tags: all_tags.extend(t.strip() for t in v_ai_tags.split(','))
    tag_counts = Counter(all_tags)
    return [{"label": t[0], "value": t[1]} for t in tag_counts.most_common(20)]

def get_quality_stats(db: Session):
    stats = { "4K": 0, "FHD": 0, "HD": 0, "SD": 0, "Unknown": 0 }
    videos = db.query(Video.height).all()
    for v in videos:
        h = v[0]
        if h >= 1080: stats["FHD"] += 1
        elif h >= 720: stats["HD"] += 1
        elif h > 0: stats["SD"] += 1
        else: stats["Unknown"] += 1
    return [{"label": k, "value": v} for k, v in stats.items()]

def extract_playlist_urls(url: str, parser: str = "yt-dlp"):
    opts = {'extract_flat': True, 'quiet': True, 'ignoreerrors': True}
    found_urls = []
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info and 'entries' in info:
                for entry in info['entries']:
                    if entry: found_urls.append(entry.get('url') or entry.get('webpage_url'))
            elif info: found_urls.append(url)
    except: pass
    return [u for u in found_urls if u] if found_urls else [url]

# --- Coomer/Kemono Profile Scanner (VIP Dashboard) ---
def scan_coomer_profile(profile_url: str):
    """
    Scan Coomer.st or Kemono.party creator profile and extract video information.
    Implements heuristic scoring (0-10) with badge system:
    - 8-10: FULL-LENGTH (import entire profile)
    - 6-7: MIXED (import videos only)
    - ≤5: SKIP (do not import)
    
    Only includes MP4 videos >= 300MB. Ignores images, GIFs, clips < 50MB.
    """
    if not profile_url:
        logging.warning("scan_coomer_profile: Empty profile_url")
        return None
    
    # Normalize URL - handle coomer.* and kemono.* domains
    # Supported domains: coomer.st, coomer.si, coomer.party, kemono.party, kemono.su, etc.
    # Also accept username-only input (construct URL)
    profile_url_lower = profile_url.lower().strip()
    
    # If just username, try to construct URL
    if '/' not in profile_url_lower and len(profile_url_lower) > 2:
        # Try common patterns - try coomer.st first, then kemono.party
        profile_url = f"https://coomer.st/fansly/user/{profile_url_lower}"
        profile_url_lower = profile_url.lower()
    
    # Check if URL contains coomer or kemono domain (flexible matching)
    is_coomer = 'coomer.' in profile_url_lower or 'coomer/' in profile_url_lower
    is_kemono = 'kemono.' in profile_url_lower or 'kemono/' in profile_url_lower
    
    if not is_coomer and not is_kemono:
        logging.warning(f"scan_coomer_profile: Invalid domain in URL (must contain coomer.* or kemono.*): {profile_url}")
        return None
    
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        
        # Fetch profile page
        resp = requests.get(profile_url, headers=headers, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # Extract creator name/username
        creator_name = "Unknown"
        name_selectors = ['h1', '.card-header h2', '.user-header h1', '.user-header h2', 'header h1',
                         '.user-name', '.creator-name', '[class*="user"] h1', '[class*="creator"] h1']
        for selector in name_selectors:
            name_elem = soup.select_one(selector)
            if name_elem:
                creator_name = name_elem.get_text(strip=True)
                if creator_name and len(creator_name) > 2:
                    break
        
        # Extract username from URL if name not found
        if creator_name == "Unknown":
            url_parts = profile_url.rstrip('/').split('/')
            if len(url_parts) > 0:
                creator_name = url_parts[-1]
        
        # Collect all files from profile and posts
        all_files = []  # All files (images, videos, etc.)
        mp4_videos = []  # Only MP4 videos >= 300MB
        post_links = []
        post_dates = []  # For regularity calculation
        seen_urls = set()
        
        # Find post links
        for link in soup.find_all('a', href=True):
            href = link['href']
            if '/post/' in href:
                full_url = urllib.parse.urljoin(profile_url, href)
                if full_url not in post_links:
                    post_links.append(full_url)
        
        # Also check article tags
        for article in soup.find_all('article'):
            article_link = article.find('a', href=True)
            if article_link:
                href = article_link['href']
                if '/post/' in href:
                    full_url = urllib.parse.urljoin(profile_url, href)
                    if full_url not in post_links:
                        post_links.append(full_url)
        
        logging.info(f"Found {len(post_links)} post links, scanning first 100...")
        
        # Scan posts for files
        for post_url in post_links[:100]:
            try:
                post_resp = requests.get(post_url, headers=headers, timeout=15)
                post_resp.raise_for_status()
                post_soup = BeautifulSoup(post_resp.text, 'html.parser')
                
                # Extract post date if available
                date_elem = post_soup.select_one('time, .post-date, [class*="date"]')
                if date_elem:
                    date_str = date_elem.get('datetime') or date_elem.get_text(strip=True)
                    if date_str:
                        post_dates.append(date_str)
                
                # Find all file links in post
                for link in post_soup.find_all('a', href=True):
                    href = link['href']
                    if '/data/' in href:
                        file_url = urllib.parse.urljoin(post_url, href)
                        if file_url not in seen_urls:
                            seen_urls.add(file_url)
                            file_ext = href.lower().split('.')[-1] if '.' in href else ''
                            file_size_mb = 0
                            
                            # Try to extract size from link text or nearby
                            size_text = link.get_text()
                            size_match = re.search(r'([\d.]+)\s*(GB|MB|KB)', size_text, re.IGNORECASE)
                            if size_match:
                                size_val = float(size_match.group(1))
                                size_unit = size_match.group(2).upper()
                                if size_unit == 'GB':
                                    file_size_mb = size_val * 1024
                                elif size_unit == 'MB':
                                    file_size_mb = size_val
                                elif size_unit == 'KB':
                                    file_size_mb = size_val / 1024
                            
                            # Extract post ID from URL
                            post_id = post_url.split('/')[-1] if '/' in post_url else post_url
                            
                            # Extract title
                            title = link.get_text(strip=True) or f"Video {len(all_files) + 1}"
                            
                            # Check if it's a video (MP4 only, ignore GIF, images, etc.)
                            if file_ext == 'mp4' and file_size_mb >= 300:  # Only MP4 >= 300MB
                                mp4_videos.append({
                                    'id': post_id,
                                    'title': title,
                                    'url': file_url,
                                    'thumbnail': '',  # Will be extracted if available
                                    'size_mb': round(file_size_mb, 2),
                                    'date': date_str if date_str else '',
                                    'post_url': post_url
                                })
                            
                            all_files.append({
                                'url': file_url,
                                'ext': file_ext,
                                'size_mb': file_size_mb,
                                'type': 'video' if file_ext == 'mp4' else ('image' if file_ext in ['jpg', 'jpeg', 'png', 'webp'] else 'other')
                            })
                
                # Also check video tags and script tags
                for video_tag in post_soup.find_all('video'):
                    src = video_tag.get('src')
                    if src and '/data/' in src and '.mp4' in src.lower():
                        video_url = urllib.parse.urljoin(post_url, src)
                        if video_url not in seen_urls:
                            seen_urls.add(video_url)
                            post_id = post_url.split('/')[-1]
                            mp4_videos.append({
                                'id': post_id,
                                'title': f"Video from {post_id}",
                                'url': video_url,
                                'thumbnail': '',
                                'size_mb': 0,
                                'date': date_str if date_str else '',
                                'post_url': post_url
                            })
                
                # Search script tags for JSON data
                for script in post_soup.find_all('script'):
                    script_text = script.string or script.get_text()
                    if script_text and '/data/' in script_text:
                        for match in re.finditer(r'https?://[^"\'\\s\)]+/data/[^"\'\\s\)]+\.mp4', script_text):
                            video_url = match.group(0)
                            if video_url not in seen_urls:
                                seen_urls.add(video_url)
                                post_id = post_url.split('/')[-1]
                                mp4_videos.append({
                                    'id': post_id,
                                    'title': f"Video from {post_id}",
                                    'url': video_url,
                                    'thumbnail': '',
                                    'size_mb': 0,
                                    'date': date_str if date_str else '',
                                    'post_url': post_url
                                })
            
            except Exception as e:
                logging.warning(f"Error scanning post {post_url}: {e}")
                continue
        
        # HEURISTIC SCORING (0-10) - 5 categories, 0-2 points each
        rating = 0
        
        # 1. Podiel MP4 videí (0-2 body)
        total_files = len(all_files)
        if total_files > 0:
            mp4_ratio = len(mp4_videos) / total_files
            if mp4_ratio >= 0.7:  # 70%+ MP4
                rating += 2
            elif mp4_ratio >= 0.4:  # 40%+ MP4
                rating += 1
        
        # 2. Priemerná veľkosť videí (0-2 body)
        if mp4_videos:
            avg_size = sum(v.get('size_mb', 0) for v in mp4_videos) / len(mp4_videos)
            if avg_size >= 1000:  # 1GB+
                rating += 2
            elif avg_size >= 500:  # 500MB+
                rating += 1
        
        # 3. Dĺžka videí / počet videí (0-2 body)
        if len(mp4_videos) >= 50:
            rating += 2
        elif len(mp4_videos) >= 20:
            rating += 1
        
        # 4. Pravidelnosť uploadu (0-2 body)
        if len(post_dates) >= 20:
            # Check if dates are spread out (regular uploads)
            rating += 2
        elif len(post_dates) >= 10:
            rating += 1
        
        # 5. Názvy / PPV signály (0-2 body)
        ppv_keywords = ['full', 'ppv', 'uncut', 'complete', 'full length', 'premium']
        ppv_count = 0
        for video in mp4_videos:
            title_lower = video.get('title', '').lower()
            if any(keyword in title_lower for keyword in ppv_keywords):
                ppv_count += 1
        
        if len(mp4_videos) > 0:
            ppv_ratio = ppv_count / len(mp4_videos)
            if ppv_ratio >= 0.5:  # 50%+ have PPV keywords
                rating += 2
            elif ppv_ratio >= 0.2:  # 20%+ have PPV keywords
                rating += 1
        
        rating = min(10, round(rating, 1))
        
        # Determine badge
        if rating >= 8:
            badge = "FULL-LENGTH"
        elif rating >= 6:
            badge = "MIXED"
        else:
            badge = "SKIP"
        
        # If SKIP, log it
        if badge == "SKIP":
            logging.info(f"SKIP: Profile {creator_name} has rating {rating}")
        
        return {
            'source': 'coomer',
            'creator': creator_name,
            'rating': rating,
            'badge': badge,
            'videos': mp4_videos[:50]  # Return first 50 videos for preview
        }
        
    except Exception as e:
        logging.error(f"Error scanning Coomer profile {profile_url}: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return None