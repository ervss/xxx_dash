from fastapi import FastAPI, Depends, UploadFile, File, BackgroundTasks, HTTPException, Request, Response, Body, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import distinct, desc, asc, or_
from typing import List, Optional
from pydantic import BaseModel
import datetime
import os
import aiohttp
import json
import base64
import urllib.parse
import requests
import shutil
import subprocess
import yt_dlp
import asyncio
import logging

from .database import get_db, init_db, Video, SmartPlaylist, SessionLocal
# FIX: Odstránené nefunkčné importy (PornOne, JD)
from contextlib import asynccontextmanager
from .services import VIPVideoProcessor, search_videos_by_subtitle, get_batch_stats, get_tags_stats, get_quality_stats, extract_playlist_urls, fetch_eporner_videos, fetch_eporner_playlist, scan_coomer_profile
from .websockets import manager
from .aria2_service import aria2_service

http_session = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_session
    timeout = aiohttp.ClientTimeout(total=None, connect=60, sock_read=300)
    http_session = aiohttp.ClientSession(timeout=timeout)
    print("AIOHTTP ClientSession created.")
    yield
    if http_session:
        await http_session.close()
        print("AIOHTTP ClientSession closed.")

app = FastAPI(title="Quantum VIP Dashboard", lifespan=lifespan)
init_db()

# --- Password & Session ---
# Load password and secret key from environment variables for better security
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "admin")
SECRET_KEY = os.environ.get("SECRET_KEY", "a_very_secret_key_change_me")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# --- Models ---
class VideoExport(BaseModel):
    id: int
    title: str
    url: str
    duration: float
    width: int
    height: int
    tags: str
    ai_tags: str
    created_at: datetime.datetime
    class Config:
        orm_mode = True

class ImportRequest(BaseModel):
    urls: List[str]
    batch_name: Optional[str] = None
    parser: Optional[str] = None
    import_speed: Optional[str] = "default"  # default, fast, turbo

class XVideosImportRequest(BaseModel):
    url: str

class BatchActionRequest(BaseModel):
    video_ids: List[int]
    action: str

class BatchDeleteRequest(BaseModel):
    batch_name: str

class VideoUpdate(BaseModel):
    is_favorite: Optional[bool] = None
    is_watched: Optional[bool] = None
    resume_time: Optional[float] = None
    tags: Optional[str] = None

class EpornerSearchRequest(BaseModel):
    query: Optional[str] = None
    playlist_url: Optional[str] = None
    count: int = 50
    min_quality: int = 1080
    batch_name: Optional[str] = None
    import_speed: Optional[str] = "default"  # default, fast, turbo

class CoomerScanRequest(BaseModel):
    profile_url: str

class CoomerSaveRequest(BaseModel):
    urls: List[str]
    batch_name: Optional[str] = None
    import_speed: Optional[str] = "default"  # default, fast, turbo

class TurboDownloadRequest(BaseModel):
    video_ids: List[int]

# --- Routes ---

@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

class LoginRequest(BaseModel):
    password: str

@app.post("/login")
async def login_submit(request: Request, login_request: LoginRequest):
    if login_request.password == DASHBOARD_PASSWORD:
        request.session["authenticated"] = True
        return Response(status_code=200)
    raise HTTPException(status_code=401, detail="Invalid password")

@app.get("/logout")
async def logout(request: Request):
    request.session.pop("authenticated", None)
    return RedirectResponse(url="/login")

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/favicon.ico")
def favicon(): return Response(status_code=204)

@app.get("/stats")
def read_stats(): return FileResponse("app/static/stats.html")

@app.get("/api/videos")
def get_videos(page: int = 1, limit: int = 10, search: str = "", batch: str = "All", favorites_only: bool = False, quality: str = "All", duration_min: int = 0, duration_max: int = 99999, sort: str = "date_desc", dateMin: Optional[str] = None, dateMax: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Video)
    if search: query = query.filter(or_(Video.title.contains(search), Video.tags.contains(search), Video.ai_tags.contains(search), Video.batch_name.contains(search)))
    if batch and batch != "All": query = query.filter(Video.batch_name == batch)
    if favorites_only: query = query.filter(Video.is_favorite == True)
    query = query.filter(Video.duration >= duration_min)
    if duration_max < 3600: query = query.filter(Video.duration <= duration_max)
    if quality != "All":
        if quality == "4K": query = query.filter(Video.height >= 2160)
        elif quality == "1440p": query = query.filter(Video.height >= 1440, Video.height < 2160)
        elif quality in ["1080p", "FHD"]: query = query.filter(Video.height >= 1080, Video.height < 1440)
        elif quality in ["720p", "HD"]: query = query.filter(Video.height >= 720, Video.height < 1080)
        elif quality == "SD": query = query.filter(Video.height < 720)
    
    if dateMin:
        try: query = query.filter(Video.created_at >= datetime.datetime.fromisoformat(dateMin))
        except ValueError: pass
    if dateMax:
        try: query = query.filter(Video.created_at < datetime.datetime.fromisoformat(dateMax) + datetime.timedelta(days=1))
        except ValueError: pass

    if sort == "date_desc": query = query.order_by(desc(Video.id))
    elif sort == "title_asc": query = query.order_by(asc(Video.title))
    elif sort == "longest": query = query.order_by(desc(Video.duration))
    elif sort == "shortest": query = query.order_by(asc(Video.duration))
    
    videos = query.offset((page - 1) * limit).limit(limit).all()
    
    # Convert to dicts and add gif_preview_path
    results = []
    for v in videos:
        video_dict = v.__dict__
        video_dict.pop('_sa_instance_state', None) # Remove SQLAlchemy state
        results.append(video_dict)
        
    return results

@app.get("/api/export")
def export_videos(search: str = "", batch: str = "All", favorites_only: bool = False, quality: str = "All", duration_min: int = 0, duration_max: int = 99999, sort: str = "date_desc", dateMin: Optional[str] = None, dateMax: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Video)
    if search: query = query.filter(or_(Video.title.contains(search), Video.tags.contains(search), Video.ai_tags.contains(search), Video.batch_name.contains(search)))
    if batch and batch != "All": query = query.filter(Video.batch_name == batch)
    if favorites_only: query = query.filter(Video.is_favorite == True)
    query = query.filter(Video.duration >= duration_min)
    if duration_max < 3600: query = query.filter(Video.duration <= duration_max)
    if quality != "All":
        if quality == "4K": query = query.filter(Video.height >= 2160)
        elif quality == "1440p": query = query.filter(Video.height >= 1440, Video.height < 2160)
        elif quality in ["1080p", "FHD"]: query = query.filter(Video.height >= 1080, Video.height < 1440)
        elif quality in ["720p", "HD"]: query = query.filter(Video.height >= 720, Video.height < 1080)
        elif quality == "SD": query = query.filter(Video.height < 720)
    
    if dateMin:
        try: query = query.filter(Video.created_at >= datetime.datetime.fromisoformat(dateMin))
        except ValueError: pass
    if dateMax:
        try: query = query.filter(Video.created_at < datetime.datetime.fromisoformat(dateMax) + datetime.timedelta(days=1))
        except ValueError: pass

    if sort == "date_desc": query = query.order_by(desc(Video.id))
    elif sort == "title_asc": query = query.order_by(asc(Video.title))
    elif sort == "longest": query = query.order_by(desc(Video.duration))
    elif sort == "shortest": query = query.order_by(asc(Video.duration))
    
    videos = query.all()
    content = [VideoExport.from_orm(v).dict() for v in videos]
    return JSONResponse(content=content, headers={'Content-Disposition': f'attachment; filename="export.json"'})

@app.get("/api/search/subtitles")
def search_subs(query: str, db: Session = Depends(get_db)):
    return search_videos_by_subtitle(query, db)
    
@app.get("/api/batches")
def get_batches(db: Session = Depends(get_db)):
    batches = db.query(distinct(Video.batch_name)).all()
    return [b[0] for b in batches if b[0]]

@app.get("/api/tags")
def get_all_tags(db: Session = Depends(get_db)):
    all_tags = set()
    videos = db.query(Video.tags, Video.ai_tags).filter(or_(Video.tags != None, Video.ai_tags != None)).all()
    for video_tags, video_ai_tags in videos:
        if video_tags: all_tags.update(tag.strip() for tag in video_tags.split(',') if tag.strip())
        if video_ai_tags: all_tags.update(tag.strip() for tag in video_ai_tags.split(',') if tag.strip())
    return sorted(list(all_tags))

class SmartPlaylistRule(BaseModel):
    field: str
    operator: str
    value: str

class SmartPlaylistCreate(BaseModel):
    name: str
    rules: List[SmartPlaylistRule]

class SmartPlaylistUpdate(SmartPlaylistCreate):
    pass

class SmartPlaylistOut(SmartPlaylistCreate):
    id: int
    created_at: datetime.datetime

    class Config:
        orm_mode = True

# --- Smart Playlist Endpoints ---

@app.get("/api/smart-playlists", response_model=List[SmartPlaylistOut])
def get_smart_playlists(db: Session = Depends(get_db)):
    return db.query(SmartPlaylist).all()

@app.post("/api/smart-playlists", response_model=SmartPlaylistOut)
def create_smart_playlist(playlist: SmartPlaylistCreate, db: Session = Depends(get_db)):
    db_playlist = SmartPlaylist(name=playlist.name, rules=[r.dict() for r in playlist.rules])
    db.add(db_playlist)
    db.commit()
    db.refresh(db_playlist)
    return db_playlist

@app.get("/api/smart-playlists/{playlist_id}", response_model=SmartPlaylistOut)
def get_smart_playlist(playlist_id: int, db: Session = Depends(get_db)):
    playlist = db.query(SmartPlaylist).get(playlist_id)
    if not playlist:
        raise HTTPException(404, "Playlist not found")
    return playlist

@app.put("/api/smart-playlists/{playlist_id}", response_model=SmartPlaylistOut)
def update_smart_playlist(playlist_id: int, playlist: SmartPlaylistUpdate, db: Session = Depends(get_db)):
    db_playlist = db.query(SmartPlaylist).get(playlist_id)
    if not db_playlist:
        raise HTTPException(404, "Playlist not found")
    db_playlist.name = playlist.name
    db_playlist.rules = [r.dict() for r in playlist.rules]
    db.commit()
    return db_playlist

@app.delete("/api/smart-playlists/{playlist_id}")
def delete_smart_playlist(playlist_id: int, db: Session = Depends(get_db)):
    playlist = db.query(SmartPlaylist).get(playlist_id)
    if not playlist:
        raise HTTPException(404, "Playlist not found")
    db.delete(playlist)
    db.commit()
    return {"status": "ok"}

@app.get("/api/smart-playlists/{playlist_id}/videos")
def get_smart_playlist_videos(playlist_id: int, db: Session = Depends(get_db)):
    playlist = db.query(SmartPlaylist).get(playlist_id)
    if not playlist:
        raise HTTPException(404, "Playlist not found")
    
    query = db.query(Video)
    for rule in playlist.rules:
        field = getattr(Video, rule['field'], None)
        if field is None: continue

        op = rule['operator']
        val = rule['value']

        if op == 'contains':
            query = query.filter(field.contains(val))
        elif op == 'not_contains':
            query = query.filter(~field.contains(val))
        elif op == 'equals':
            query = query.filter(field == val)
        elif op == 'not_equals':
            query = query.filter(field != val)
        elif op == 'greater_than':
            query = query.filter(field > val)
        elif op == 'less_than':
            query = query.filter(field < val)
    
    videos = query.order_by(desc(Video.id)).limit(100).all()
    results = []
    for v in videos:
        video_dict = v.__dict__
        video_dict.pop('_sa_instance_state', None)
        results.append(video_dict)
    return results

# --- Stats Endpoints ---
@app.get("/api/stats/batches")
def api_get_batch_stats(db: Session = Depends(get_db)): return get_batch_stats(db)

@app.get("/api/stats/tags")
def api_get_tags_stats(db: Session = Depends(get_db)): return get_tags_stats(db)

@app.get("/api/stats/quality")
def api_get_quality_stats(db: Session = Depends(get_db)): return get_quality_stats(db)

@app.post("/api/batch-action")
def batch_action(req: BatchActionRequest, db: Session = Depends(get_db)):
    query = db.query(Video).filter(Video.id.in_(req.video_ids))
    if req.action == 'delete': query.delete(synchronize_session=False)
    elif req.action == 'favorite': query.update({Video.is_favorite: True}, synchronize_session=False)
    elif req.action == 'unfavorite': query.update({Video.is_favorite: False}, synchronize_session=False)
    elif req.action == 'mark_watched': query.update({Video.is_watched: True}, synchronize_session=False)
    db.commit()
    return {"status": "ok"}

@app.post("/api/batch/delete-all")
def delete_entire_batch(req: BatchDeleteRequest, db: Session = Depends(get_db)):
    if not req.batch_name or req.batch_name == "All": raise HTTPException(400)
    db.query(Video).filter(Video.batch_name == req.batch_name).delete(synchronize_session=False)
    db.commit()
    return {"status": "deleted", "batch": req.batch_name}

@app.put("/api/videos/{video_id}")
def update_video(video_id: int, update: VideoUpdate, db: Session = Depends(get_db)):
    v = db.query(Video).get(video_id)
    if not v: raise HTTPException(404)
    if update.is_favorite is not None: v.is_favorite = update.is_favorite
    if update.is_watched is not None: v.is_watched = update.is_watched
    if update.resume_time is not None: v.resume_time = update.resume_time
    if update.tags is not None: v.tags = update.tags
    db.commit()
    return v

@app.post("/api/videos/{video_id}/regenerate")
def regenerate_thumbnail(video_id: int, bg_tasks: BackgroundTasks, mode: str = "mp4", extractor: str = "auto", db: Session = Depends(get_db)):
    v = db.query(Video).get(video_id)
    if not v: raise HTTPException(404)
    v.status = "pending"
    v.error_msg = None
    db.commit()
    processor = VIPVideoProcessor()
    bg_tasks.add_task(processor.process_single_video, video_id, force=True, quality_mode=mode, extractor=extractor)
    return {"status": "queued", "id": video_id}

def run_aria_download(video_id: int):
    db = SessionLocal()
    v = db.query(Video).get(video_id)
    if not v:
        db.close()
        return

    try:
        output_dir = os.path.join("app", "static", "local_videos")
        os.makedirs(output_dir, exist_ok=True)
        
        safe_filename = f"video_{video_id}_{v.title[:50]}.mp4"
        safe_filename = "".join([c for c in safe_filename if c.isalnum() or c in (' ','-','_')]).strip().replace(' ', '_')
        
        aria2c_path = "aria2c.exe"
        if not os.path.isfile(aria2c_path):
             aria2c_path = os.path.join("app", "aria2c.exe")
             if not os.path.isfile(aria2c_path):
                raise FileNotFoundError("aria2c.exe not found in root or app directory.")

        command = [
            aria2c_path,
            "--continue=true",
            "--max-connection-per-server=16",
            "--split=16",
            "--min-split-size=1M",
            "--dir", output_dir,
            "--out", safe_filename,
            v.url
        ]

        print(f"Starting Turbo Download for video {video_id}: {' '.join(command)}")

        process = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='ignore')

        if process.returncode == 0:
            v.status = "ready"
            v.url = f"/static/local_videos/{safe_filename}"
        else:
            v.status = "error"
            v.error_msg = f"Aria2c failed: {process.stderr[:250]}"
        
        db.commit()

    except Exception as e:
        print(f"Error in run_aria_download for video {video_id}: {e}")
        v.status = "error"
        v.error_msg = str(e)
        db.commit()
    finally:
        db.close()

@app.post("/api/videos/{video_id}/download_local")
async def download_local_video(video_id: int, bg_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    v = db.query(Video).get(video_id)
    if not v:
        raise HTTPException(404, "Video not found")
    
    if not v.url.startswith("http"):
        return {"status": "already_local", "video_id": video_id}

    v.status = 'downloading'
    db.commit()
    
    bg_tasks.add_task(run_aria_download, video_id)
    
    return {"status": "download_queued", "video_id": video_id}

# --- Background Import Logic (FIX ZASEKÁVANIA) ---

def background_import_process(urls: List[str], batch_name: str, parser: str, import_speed: str = "default"):
    """
    Táto funkcia beží na pozadí. Rozoberá URL, pridáva do DB a spúšťa spracovanie.
    """
    db = SessionLocal()
    new_ids = []
    
    # 1. Expandovanie playlistov (blokujúca operácia, preto je tu)
    final_urls = []
    for u in urls:
        u = u.strip()
        if not u: continue
        
        # Skip local files - they should not be downloaded or processed as playlists
        # Local files start with /static/ or are relative paths (not http/https)
        is_local_file = (u.startswith('/static/') or 
                        u.startswith('./') or 
                        (not u.startswith('http://') and not u.startswith('https://') and 
                         any(u.lower().endswith(ext) for ext in ['.mp4', '.mkv', '.avi', '.mov', '.webm'])))
        
        if is_local_file:
            # Local file - add directly without expansion
            final_urls.append(u)
            continue
        
        # Pixeldrain nepotrebuje expandovať
        if "pixeldrain.com" in u and "/api/file/" in u:
            final_urls.append(u)
        else:
            final_urls.extend(extract_playlist_urls(u, parser=parser))

    # 2. Vloženie do DB
    final_urls = list(dict.fromkeys(final_urls)) # Unikátne URL
    
    for url in final_urls:
        url = url.strip()
        if not url: continue
        
        # Pixeldrain Title Logic (rýchle, z URL)
        title = "Queued..."
        if "pixeldrain.com" in url and "/api/file/" in url:
             try: 
                 parts = url.split("/")
                 if len(parts) > 5: # .../api/file/ID/Meno
                     title = urllib.parse.unquote(parts[-1])
             except: pass

        # Ukladáme do DB
        v = Video(title=title, url=url, source_url=url, batch_name=batch_name, status="pending")
        db.add(v)
        db.flush()
        new_ids.append(v.id)

    db.commit()
    db.close()

    # 3. Spustenie spracovania
    if new_ids:
        processor = VIPVideoProcessor()
        processor.process_batch(new_ids, import_speed=import_speed)

@app.post("/api/import/text")
async def import_text(bg_tasks: BackgroundTasks, data: ImportRequest):
    """
    API vráti odpoveď OKAMŽITE. Celý import beží na pozadí.
    """
    batch = data.batch_name or f"Import {datetime.datetime.now().strftime('%d.%m %H:%M')}"
    import_speed = data.import_speed or "default"
    # Spustíme prácu na pozadí
    bg_tasks.add_task(background_import_process, data.urls, batch, data.parser or "yt-dlp", import_speed)
    return {"count": len(data.urls), "batch": batch, "message": "Import started in background"}

@app.post("/api/import/file")
async def import_file(bg_tasks: BackgroundTasks, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Upload local video file to project.
    Security: Validates filename, file size, and restricts to local_videos directory.
    """
    if not file.filename:
        return JSONResponse(status_code=400, content={"error": "No filename provided"})
    
    filename = file.filename
    ext = filename.lower().rsplit('.', 1)[-1] if '.' in filename else ""

    if ext in ["mp4", "mkv", "avi", "mov", "webm"]:
        # Security: Sanitize filename - remove path traversal attempts
        safe_filename = os.path.basename(filename)  # Remove any directory components
        safe_filename = "".join(c for c in safe_filename if c.isalnum() or c in (' ', '-', '_', '.'))  # Remove dangerous chars
        safe_filename = safe_filename.strip()
        
        if not safe_filename or len(safe_filename) > 255:
            return JSONResponse(status_code=400, content={"error": "Invalid filename"})
        
        # Ensure extension is still valid after sanitization
        if not safe_filename.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm')):
            return JSONResponse(status_code=400, content={"error": "Invalid file extension"})
        
        # Security: Restrict to local_videos directory only
        save_dir = os.path.abspath(os.path.join(os.getcwd(), "app/static/local_videos"))
        os.makedirs(save_dir, exist_ok=True)
        
        # Security: Ensure save_path is within save_dir (prevent path traversal)
        save_path = os.path.join(save_dir, safe_filename)
        save_path = os.path.abspath(save_path)  # Resolve to absolute path
        
        if not save_path.startswith(save_dir):
            return JSONResponse(status_code=403, content={"error": "Access denied - path traversal detected"})
        
        # Security: Check file size limit (e.g., 10GB max)
        MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024  # 10GB
        file_size = 0
        
        try:
            with open(save_path, "wb") as f:
                while True:
                    chunk = await file.read(1024 * 1024 * 10)  # 10MB chunks
                    if not chunk:
                        break
                    file_size += len(chunk)
                    if file_size > MAX_FILE_SIZE:
                        # Remove partial file
                        if os.path.exists(save_path):
                            os.remove(save_path)
                        return JSONResponse(status_code=413, content={"error": f"File too large. Maximum size: {MAX_FILE_SIZE / (1024**3):.1f}GB"})
                    f.write(chunk)
            
            # Security: Verify file was actually written and has reasonable size
            if not os.path.exists(save_path) or os.path.getsize(save_path) < 1024:  # At least 1KB
                if os.path.exists(save_path):
                    os.remove(save_path)
                return JSONResponse(status_code=400, content={"error": "Uploaded file is too small or invalid"})
            
            # Create video entry
            v = Video(
                title=safe_filename,
                url=f"/static/local_videos/{safe_filename}",
                batch_name=f"Local_{datetime.datetime.now().strftime('%d.%m %H:%M')}",
                status="pending"
            )
            db.add(v)
            db.commit()
            
            processor = VIPVideoProcessor()
            bg_tasks.add_task(processor.process_single_video, v.id, force=True)
            return {"count": 1, "message": "Video uploaded", "video_id": v.id, "filename": safe_filename}
        
        except Exception as e:
            logging.error(f"Error uploading file {filename}: {e}")
            # Clean up partial file
            if os.path.exists(save_path):
                try:
                    os.remove(save_path)
                except:
                    pass
            return JSONResponse(status_code=500, content={"error": f"Upload failed: {str(e)}"})

    # CSV import
    if ext == "csv":
        import csv
        import io
        content = await file.read()
        try:
            text = content.decode('utf-8')
        except:
            text = content.decode('latin-1', errors='ignore')
        reader = csv.DictReader(io.StringIO(text))
        count = 0
        new_ids = []
        for row in reader:
            # Očakávame stĺpce: title, url, prípadne ďalšie (prispôsobiť podľa .csv)
            title = row.get('title') or row.get('name') or row.get('Title') or row.get('Name') or 'Untitled'
            url = row.get('url') or row.get('Url') or row.get('URL')
            if not url:
                continue
            video = Video(
                title=title,
                url=url,
                source_url=url,
                batch_name=f"CSV_{filename}",
                status="pending",
                created_at=datetime.datetime.utcnow()
            )
            db.add(video)
            db.flush()
            new_ids.append(video.id)
            count += 1
        db.commit()
        processor = VIPVideoProcessor()
        bg_tasks.add_task(processor.process_batch, new_ids)
        return {"count": count, "batch": f"CSV_{filename}", "message": f"Imported {count} videos from CSV"}

    # Text/JSON import - delegujeme na background task
    content = await file.read()
    try: text = content.decode('utf-8')
    except: text = content.decode('latin-1', errors='ignore')
    
    urls = text.splitlines()
    
    if filename.endswith('.json'):
        try:
            j = json.loads(text)
            
            # --- OPRAVA: Extrakcia len 'video_url' z JSON objektov ---
            if isinstance(j, list) and all(isinstance(item, dict) and 'video_url' in item for item in j):
                # Extrahuje 'video_url' zo všetkých objektov v zozname
                urls = [item['video_url'] for item in j]
            else:
                # Fallback pre JSON s čistými URL adresami
                urls = [str(x) for x in j] if isinstance(j, list) else text.splitlines()

            # Odstránenie neplatných (napr. None) a prázdnych URL a kontrola protokolu
            urls = [u for u in urls if u and u.startswith('http')]
            # --- KONIEC OPRAVY ---

        except Exception as e:
            print(f"Failed to parse JSON content: {e}")
            urls = [] # Ak parsovanie zlyhá, neimportujeme nič

    batch = f"Import_{filename}"
    bg_tasks.add_task(background_import_process, urls, batch, "yt-dlp")
    return {"count": len(urls), "batch": batch, "message": "File import started in background"}

@app.post("/api/import/xvideos")
async def import_xvideos(data: XVideosImportRequest, db: Session = Depends(get_db)):
    """
    Import single XVideos URL, extract metadata, and save to DB.
    Returns JSON metadata for immediate display.
    """
    processor = VIPVideoProcessor()
    meta = processor.extract_xvideos_metadata(data.url)
    
    if not meta:
        return JSONResponse(status_code=400, content={"error": "EXTRACTION_FAILED"})
    
    # Check if exists
    existing = db.query(Video).filter(Video.source_url == data.url).first()
    if existing:
        # Update existing
        existing.url = meta['stream']['url']
        existing.title = meta['title']
        existing.duration = meta['duration']
        existing.thumbnail_path = meta['thumbnail']
        existing.height = meta['stream']['height']
        existing.status = "ready"
        db.commit()
        db.refresh(existing)
        video_id = existing.id
    else:
        # Create new
        video = Video(
            title=meta['title'],
            url=meta['stream']['url'],
            source_url=data.url,
            duration=meta['duration'],
            thumbnail_path=meta['thumbnail'],
            height=meta['stream']['height'],
            width=0, # Not provided in simplified meta
            status="ready",
            batch_name=f"Import XVideos {datetime.datetime.now().strftime('%d.%m')}",
            created_at=datetime.datetime.utcnow()
        )
        db.add(video)
        db.commit()
        db.refresh(video)
        video_id = video.id

    # Add DB ID to response if needed, but the prompt specified a specific shape.
    # The prompt asked for: source, id, title, duration, thumbnail, stream object.
    # The extracted meta has this shape.
    # We might want to pass the DB ID as 'id' or keep the source ID?
    # The prompt example: "id": "okchumv725e" (looks like xvideos ID).
    # But for the frontend to work with the player and internal logic, it usually needs the DB ID.
    # However, the frontend "importXVideos" logic will likely map this response to the internal video object.
    # The internal video object needs 'id' (DB ID) for things like favorites/delete etc.
    # But the prompt explicitly defined the response shape.
    # I will stick to the requested response shape, but if the frontend needs to manipulate the video later,
    # it might be tricky if I don't return the DB ID.
    # Wait, the prompt says "BACKEND RESPONSE (JSON SHAPE)... id: okchumv725e". This is the XVideos ID.
    # But the dashboard displays videos from DB.
    # If I implement "Import", I am adding to DB.
    # The frontend will probably reload or add to the list.
    # If the frontend adds to the list using this JSON, it will have the XVideos ID, not DB ID.
    # If the user clicks "Favorite", it sends the ID. If it sends "okchumv725e", the backend won't find it (expects int).
    # This suggests a conflict.
    # Option A: The frontend reloads the list after import (batch load).
    # Option B: The response should include the DB ID, maybe as a separate field or replacing 'id'.
    # The prompt says "BACKEND RESPONSE (JSON SHAPE) ... id: ...".
    # I will modify the response to include `db_id` or just rely on the fact that `id` in the prompt might be flexible or I should just return what is asked.
    # But for a functional dashboard, I'll return the requested shape. The user said "backend spracúva... priebežne renderuje UI".
    # If the user wants full functionality (like delete/fav) immediately on these items, they need DB ID.
    # I will add `db_id` to the response just in case, it doesn't hurt.
    
    meta['db_id'] = video_id
    return meta

@app.post("/api/import/eporner_search")
async def import_eporner_search(bg_tasks: BackgroundTasks, data: EpornerSearchRequest = Body(...), db: Session = Depends(get_db)):
    """
    Import videos from Eporner either by search query or playlist URL.
    """
    batch = data.batch_name or f"Eporner {datetime.datetime.now().strftime('%d.%m %H:%M')}"
    import_speed = data.import_speed or "default"
    
    videos = []
    
    # Check if playlist URL is provided
    if data.playlist_url and data.playlist_url.strip():
        playlist_url = data.playlist_url.strip()
        if not playlist_url.startswith('http'):
            playlist_url = f"https://www.eporner.com{playlist_url}" if playlist_url.startswith('/') else f"https://www.eporner.com/{playlist_url}"
        
        if 'eporner.com' not in playlist_url.lower():
            return JSONResponse(status_code=400, content={"error": "Invalid Eporner playlist URL"})
        
        videos = fetch_eporner_playlist(playlist_url)
        if not videos:
            return JSONResponse(status_code=400, content={"error": "No videos found in playlist or failed to parse playlist"})
    
    # Otherwise use search query
    elif data.query and data.query.strip():
        videos = fetch_eporner_videos(
            query=data.query.strip(), 
            per_page=data.count, 
            hd=1 if data.min_quality >= 720 else 0, 
            order="newest"
        )
        if not videos:
            return JSONResponse(status_code=400, content={"error": "No videos found for search query"})
    else:
        return JSONResponse(status_code=400, content={"error": "Either query or playlist_url must be provided"})
    
    # Note: Quality filtering is handled by VIPVideoProcessor during processing
    # We can't filter by quality from API response alone
    
    new_ids = []
    for v in videos:
        # Use video_url if available, otherwise use page URL (will be processed)
        video_url = v.get("video_url") or v.get("url")
        if not video_url:
            continue  # Skip invalid entries
        
        video = Video(
            title=(v.get("title") or "Queued...")[:500],  # Limit title length
            url=video_url,
            source_url=v.get("url", video_url),  # Eporner page URL for reference
            batch_name=batch,
            status="pending",
            thumbnail_path=v.get("thumbnail"),
            created_at=datetime.datetime.utcnow()
        )
        db.add(video)
        db.flush()
        new_ids.append(video.id)
    
    if not new_ids:
        return JSONResponse(status_code=400, content={"error": "No valid videos to import"})
    
    db.commit()
    processor = VIPVideoProcessor()
    bg_tasks.add_task(processor.process_batch, new_ids, import_speed=import_speed)
    return {"count": len(new_ids), "batch": batch, "message": f"Added {len(new_ids)} Eporner videos"}

@app.post("/api/import/coomer/scan")
async def scan_coomer(data: CoomerScanRequest = Body(...)):
    """
    Scan Coomer/Kemono profile and return profile data with heuristic scoring.
    """
    try:
        profile_data = scan_coomer_profile(data.profile_url)
        if not profile_data:
            logging.error(f"scan_coomer_profile returned None for URL: {data.profile_url}")
            return JSONResponse(status_code=400, content={"error": "Failed to scan profile. Check if URL is valid and accessible."})
        return profile_data
    except Exception as e:
        logging.error(f"Error in scan_coomer endpoint: {e}")
        import traceback
        logging.error(traceback.format_exc())
        return JSONResponse(status_code=500, content={"error": f"Internal error: {str(e)}"})

@app.post("/api/import/coomer/save")
async def save_coomer_videos(bg_tasks: BackgroundTasks, data: CoomerSaveRequest = Body(...), db: Session = Depends(get_db)):
    """
    Save selected videos from Coomer profile to database.
    Uses background import process (no download, just orchestration).
    """
    if not data.urls:
        return JSONResponse(status_code=400, content={"error": "No videos selected"})
    
    batch = data.batch_name or f"Coomer {datetime.datetime.now().strftime('%d.%m %H:%M')}"
    import_speed = data.import_speed or "default"
    
    # Use background import process (same as other imports)
    bg_tasks.add_task(background_import_process, data.urls, batch, "yt-dlp", import_speed)
    
    return {"count": len(data.urls), "batch": batch, "message": "Coomer import started in background"}

# --- PROXY ---

@app.api_route("/stream_proxy/{video_id}.mp4", methods=["GET", "HEAD"])
async def proxy_video(video_id: int, request: Request, db: Session = Depends(get_db)):
    v = db.query(Video).get(video_id)
    if not v: raise HTTPException(404)
    
    if not v.url.startswith("http"):
        # Security fix: Restrict local file access to app/static/local_videos
        safe_base = os.path.abspath(os.path.join(os.getcwd(), "app/static"))
        requested_path = os.path.abspath(os.path.join(os.getcwd(), v.url.lstrip('/')))
        
        if not requested_path.startswith(safe_base):
             raise HTTPException(403, detail="Access denied")

        if os.path.exists(requested_path): return FileResponse(requested_path, media_type="video/mp4", headers={"Accept-Ranges": "bytes"})
        raise HTTPException(404, detail="File not found")

    # --- JIT Link Refreshing ---
    link_ok = False
    try:
        # Quick check to see if the link is still valid
        async with http_session.head(v.url, timeout=5, allow_redirects=True) as head_resp:
            if head_resp.status < 400:
                link_ok = True
    except asyncio.TimeoutError:
        link_ok = False # Assume link is dead on timeout
    except Exception:
        link_ok = False

    if not link_ok and v.source_url:
        print(f"Link for video {video_id} appears to be dead. Attempting to refresh...")
        try:
            def refresh_link():
                opts = {'quiet': True, 'skip_download': True, 'format': 'best'}
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(v.source_url, download=False)
            
            info = await asyncio.to_thread(refresh_link)
            
            if info and info.get('url'):
                v.url = info['url']
                db.commit()
                print(f"Successfully refreshed URL for video {video_id}")
            else:
                print(f"yt-dlp failed to get a new URL for {v.source_url}")
        except Exception as e:
            print(f"Error refreshing link for video {video_id}: {e}")

    # More robust headers to mimic a real browser
    req_headers = {
        'User-Agent': request.headers.get('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'),
        'Referer': v.source_url or v.url, # Use source_url as referer
        'Accept': '*/*',
        'Accept-Encoding': 'gzip, deflate, br',
        'Accept-Language': 'en-US,en;q=0.9',
        'Origin': request.headers.get('Origin', f"{request.url.scheme}://{request.url.netloc}")
    }
    if request.headers.get("range"):
        req_headers["Range"] = request.headers.get("range")

    try:
        upstream_response = await http_session.get(v.url, headers=req_headers, allow_redirects=True)
        
        if upstream_response.status >= 400:
            await upstream_response.release()
            raise HTTPException(status_code=upstream_response.status, detail="Upstream server error")
        
        # Simplified streaming logic
        async def stream_content():
            try:
                async for chunk in upstream_response.content.iter_chunked(1024 * 1024):
                    yield chunk
            finally:
                await upstream_response.release()

        # Pass through relevant headers from upstream
        response_headers = {
            "Content-Type": upstream_response.headers.get("Content-Type", "application/octet-stream"),
            "Content-Length": upstream_response.headers.get("Content-Length"),
            "Accept-Ranges": upstream_response.headers.get("Accept-Ranges", "bytes"),
            "Content-Range": upstream_response.headers.get("Content-Range"),
        }
        # Filter out None values
        response_headers = {k: v for k, v in response_headers.items() if v is not None}

        return StreamingResponse(stream_content(), status_code=upstream_response.status, headers=response_headers)

    except aiohttp.ClientError as e:
        print(f"Proxy ClientError: {e}")
        raise HTTPException(status_code=502, detail=f"Proxy error: {e}")
    except Exception as e:
        print(f"Proxy generic error: {e}")
        raise HTTPException(status_code=500, detail=f"Internal proxy error: {e}")

@app.get("/download/{video_id}")
async def download_direct(video_id: int, db: Session = Depends(get_db)):
    v = db.query(Video).get(video_id)
    if not v: raise HTTPException(404)
    
    # Handle local files - serve directly from disk
    if not v.url.startswith('http://') and not v.url.startswith('https://'):
        # Local file - convert to absolute path
        if v.url.startswith('/static/'):
            file_path = os.path.abspath(os.path.join(os.getcwd(), v.url.lstrip('/')))
        else:
            file_path = os.path.abspath(v.url) if not os.path.isabs(v.url) else v.url
        
        # Security: Ensure file is within safe directory
        safe_base = os.path.abspath(os.path.join(os.getcwd(), "app/static"))
        if not file_path.startswith(safe_base):
            raise HTTPException(403, detail="Access denied")
        
        if not os.path.exists(file_path):
            raise HTTPException(404, detail="File not found")
        
        safe = "".join([c for c in v.title if c.isalnum() or c in (' ','-','_')]).strip()
        return FileResponse(file_path, media_type="video/mp4", 
                          headers={"Content-Disposition": f'attachment; filename="{safe}.mp4"'})
    
    # Remote URL - stream via HTTP
    async def iter_file():
        async with aiohttp.ClientSession() as session:
            async with session.get(v.url) as resp:
                async for chunk in resp.content.iter_chunked(64*1024): yield chunk
    safe = "".join([c for c in v.title if c.isalnum() or c in (' ','-','_')]).strip()
    return StreamingResponse(iter_file(), headers={"Content-Disposition": f'attachment; filename="{safe}.mp4"'})

def get_stream_url(video_id: int):
    return f"/stream_proxy/{video_id}.mp4"

# --- TURBO DOWNLOAD (Aria2c) ---

@app.post("/api/videos/turbo-download")
async def turbo_download_videos(data: TurboDownloadRequest, db: Session = Depends(get_db)):
    """
    Start turbo download for selected videos using Aria2c
    """
    if not data.video_ids:
        return JSONResponse(status_code=400, content={"error": "No videos selected"})
    
    videos = db.query(Video).filter(Video.id.in_(data.video_ids)).all()
    if not videos:
        return JSONResponse(status_code=404, content={"error": "Videos not found"})
    
    results = []
    for video in videos:
        try:
            # Get actual video URL (might need to refresh JIT link)
            video_url = video.url
            if not video_url.startswith('http'):
                # Local file, skip
                results.append({"video_id": video.id, "status": "skipped", "reason": "local_file"})
                continue
            
            # Generate filename
            safe_title = "".join(c for c in video.title if c.isalnum() or c in (' ', '-', '_')).rstrip()
            filename = f"{video.id}_{safe_title}.mp4"
            
            # Add to Aria2c
            gid = aria2_service.add_download(video_url, video.id, filename)
            if gid:
                results.append({"video_id": video.id, "status": "started", "gid": gid})
            else:
                results.append({"video_id": video.id, "status": "error", "reason": "aria2c_failed"})
        except Exception as e:
            logging.error(f"Error starting turbo download for video {video.id}: {e}")
            results.append({"video_id": video.id, "status": "error", "reason": str(e)})
    
    return {"results": results, "message": f"Started turbo download for {len([r for r in results if r['status'] == 'started'])} videos"}

@app.get("/api/videos/turbo-download/status")
async def get_turbo_download_status():
    """Get status of all active, waiting, and stopped Aria2c downloads"""
    try:
        # Get active downloads
        active = aria2_service.get_all_status()
        
        # Also check stopped downloads (completed or failed) - last 50
        stopped = aria2_service.get_stopped_downloads(limit=50)
        
        # Combine active and recently stopped
        all_downloads = active + stopped
        
        global_stat = aria2_service.get_global_stat()
        
        # Map GIDs to video IDs
        downloads = []
        for download in all_downloads:
            gid = download.get("gid")
            video_id = aria2_service.active_downloads.get(gid)
            status = download.get("status")
            
            # If download has error, include error info
            error_info = {}
            if status == "error" or download.get("errorCode"):
                error_info = {
                    "errorCode": download.get("errorCode"),
                    "errorMessage": download.get("errorMessage", "Unknown error")
                }
            
            downloads.append({
                "gid": gid,
                "video_id": video_id,
                "status": status,
                "completedLength": download.get("completedLength", "0"),
                "totalLength": download.get("totalLength", "0"),
                "downloadSpeed": download.get("downloadSpeed", "0"),
                "files": download.get("files", []),
                **error_info
            })
        
        return {
            "downloads": downloads,
            "global": global_stat
        }
    except Exception as e:
        logging.error(f"Error getting turbo download status: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/api/videos/turbo-download/{gid}/pause")
async def pause_turbo_download(gid: str):
    """Pause a turbo download"""
    if aria2_service.pause_download(gid):
        return {"status": "paused", "gid": gid}
    return JSONResponse(status_code=400, content={"error": "Failed to pause download"})

@app.post("/api/videos/turbo-download/{gid}/resume")
async def resume_turbo_download(gid: str):
    """Resume a turbo download"""
    if aria2_service.resume_download(gid):
        return {"status": "resumed", "gid": gid}
    return JSONResponse(status_code=400, content={"error": "Failed to resume download"})

@app.delete("/api/videos/turbo-download/{gid}")
async def cancel_turbo_download(gid: str):
    """Cancel a turbo download"""
    if aria2_service.remove_download(gid):
        return {"status": "cancelled", "gid": gid}
    return JSONResponse(status_code=400, content={"error": "Failed to cancel download"})

@app.get("/api/aria2/config")
async def get_aria2_config():
    """Get current Aria2c configuration"""
    return aria2_service.get_config()

@app.post("/api/aria2/config")
async def update_aria2_config(data: dict):
    """Update Aria2c configuration"""
    try:
        aria2_service.update_config(
            max_connections=data.get("max_connections_per_server"),
            split_count=data.get("split_count"),
            max_concurrent=data.get("max_concurrent_downloads"),
            min_split_size=data.get("min_split_size")
        )
        return {"status": "updated", "config": aria2_service.get_config()}
    except Exception as e:
        logging.error(f"Error updating Aria2c config: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

# ...napr. v get_videos alebo export_videos môžete pridať do výsledku:
# video['stream_url'] = get_stream_url(video.id)
@app.websocket("/ws/status")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep the connection open
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)