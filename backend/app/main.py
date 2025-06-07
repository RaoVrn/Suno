from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl, validator
import subprocess
import os
import uuid
import re
import shutil
from fastapi.responses import FileResponse
from typing import Optional
import asyncio
from datetime import datetime, timedelta
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from dotenv import load_dotenv

load_dotenv()

limiter = Limiter(key_func=get_remote_address)
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:*,http://127.0.0.1:*").split(",")

app = FastAPI(title="Suno API",
             description="API for converting YouTube videos to MP3",
             version="1.0.0")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allowing all origins for testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

class ConvertRequest(BaseModel):
    youtube_url: str
    quality: str = "high"

    @validator("youtube_url")
    def validate_youtube_url(cls, v):
        youtube_regex = r'^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[A-Za-z0-9_-]+([\?&][^&\s]*)*$'
        if not re.match(youtube_regex, v):
            raise ValueError("Invalid YouTube URL. Please provide a valid YouTube video URL.")
        return v

    @validator("quality")
    def validate_quality(cls, v):
        if v not in ["high", "medium", "low"]:
            raise ValueError("Quality must be high, medium, or low")
        return v

# Configuration
MAX_FILE_SIZE_MB = 100
DOWNLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "downloads")
FILE_RETENTION_HOURS = 24  # Files older than this will be deleted
ALLOWED_EXTENSIONS = {".mp3"}

# Ensure download directory exists
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

async def cleanup_old_files():
    while True:
        try:
            now = datetime.now()
            for filename in os.listdir(DOWNLOAD_FOLDER):
                filepath = os.path.join(DOWNLOAD_FOLDER, filename)
                file_modified = datetime.fromtimestamp(os.path.getmtime(filepath))
                if now - file_modified > timedelta(hours=FILE_RETENTION_HOURS):
                    os.remove(filepath)
        except Exception as e:
            print(f"Cleanup error: {e}")
        await asyncio.sleep(3600)  # Run every hour

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(cleanup_old_files())

def download_audio(youtube_url: str, quality: str, filename: str):
    try:
        # Set quality based on user preference
        quality_options = {
            "high": ["--audio-quality", "0"],
            "medium": ["--audio-quality", "5"],
            "low": ["--audio-quality", "9"]
        }
        
        command = [
            "yt-dlp",
            "-x", "--audio-format", "mp3",
            *quality_options[quality],
            "--max-filesize", f"{MAX_FILE_SIZE_MB}M",
            "-o", filename,
            youtube_url
        ]
        
        # Run the command and capture output
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        
        # Verify the file was created and is within size limit
        if os.path.exists(filename):
            file_size_mb = os.path.getsize(filename) / (1024 * 1024)
            if file_size_mb > MAX_FILE_SIZE_MB:
                os.remove(filename)
                raise ValueError(f"File size exceeds limit of {MAX_FILE_SIZE_MB}MB")
        else:
            raise FileNotFoundError("Failed to create output file")
            
    except subprocess.CalledProcessError as e:
        if os.path.exists(filename):
            os.remove(filename)
        raise ValueError(f"YouTube download failed: {e.stderr}")

@app.post("/convert")
@limiter.limit("5/minute")
async def convert(convert_request: ConvertRequest, background_tasks: BackgroundTasks, request: Request):
    print(f"Received convert request for URL: {convert_request.youtube_url}")  # Debug log
    # Generate unique ID for this conversion
    file_id = str(uuid.uuid4())
    output_path = os.path.join(DOWNLOAD_FOLDER, f"{file_id}.mp3")

    # Check available disk space
    _, _, free = shutil.disk_usage(DOWNLOAD_FOLDER)
    if free < MAX_FILE_SIZE_MB * 1024 * 1024 * 2:  # Ensure 2x max file size is available
        raise HTTPException(
            status_code=507,
            detail="Insufficient storage space. Please try again later."
        )

    try:        # Start the conversion in background
        background_tasks.add_task(download_audio, convert_request.youtube_url, convert_request.quality, output_path)
        
        return {
            "download_url": f"/download/{file_id}",
            "status": "processing",
            "message": "Your file is being processed. Please wait a few moments before downloading.",
            "estimated_wait_time": "15-30 seconds"
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="An unexpected error occurred. Please try again later."
        )

@app.get("/download/{file_id}")
@limiter.limit("30/minute")
async def download(file_id: str, request: Request):
    # Validate file_id format (UUID)
    if not re.match(r'^[0-9a-f-]{36}$', file_id):
        raise HTTPException(status_code=400, detail="Invalid file ID format")
        
    # Construct and validate file path
    filename = f"{file_id}.mp3"
    if not filename.endswith(tuple(ALLOWED_EXTENSIONS)):
        raise HTTPException(status_code=400, detail="Invalid file type requested")
        
    path = os.path.join(DOWNLOAD_FOLDER, filename)
    
    # Prevent directory traversal
    try:
        real_path = os.path.realpath(path)
        if not real_path.startswith(os.path.realpath(DOWNLOAD_FOLDER)):
            raise HTTPException(status_code=403, detail="Access denied")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid file path")
    
    # Check if file exists and is readable
    if not os.path.exists(path):
        raise HTTPException(
            status_code=404,
            detail="File not found. It might still be processing or has expired."
        )
    
    if not os.access(path, os.R_OK):
        raise HTTPException(
            status_code=500,
            detail="Server cannot access the file. Please try again later."
        )
    
    try:
        return FileResponse(
            path,
            media_type="audio/mpeg",
            filename=f"youtube_audio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp3"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Failed to send file. Please try again later."
        )
