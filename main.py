from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import subprocess
import os

app = FastAPI()

# Allow all origins - adjust if needed
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

COOKIES_PATH = "cookies.txt"

@app.get("/stream")
async def stream(query: str = Query(...)):
    if not os.path.exists(COOKIES_PATH):
        raise HTTPException(status_code=500, detail="cookies.txt file not found on server")

    cmd = [
        "yt-dlp",
        "-f", "bestaudio",
        f"ytsearch1:{query}",
        "-o", "-",  # stream to stdout
        "--quiet",
        "--no-warnings",
        "--no-playlist",
        "--no-cache-dir",
        "--cookies", COOKIES_PATH  # <-- Add cookies file here
    ]

    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return StreamingResponse(process.stdout, media_type="audio/webm")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error streaming audio: {e}")
