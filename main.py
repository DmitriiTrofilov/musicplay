from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import subprocess

app = FastAPI()

# Allow all CORS for testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

async def iter_process_stdout(process):
    loop = asyncio.get_event_loop()
    while True:
        chunk = await loop.run_in_executor(None, process.stdout.read, 8192)
        if not chunk:
            break
        yield chunk

@app.get("/stream")
async def stream(mode: str = Query("search"), query: str = Query(...)):
    if not query:
        raise HTTPException(status_code=400, detail="Query parameter required")

    # Build URL for yt-dlp input
    if mode == "search":
        url = f"ytsearch:{query}"
    elif mode == "playlist":
        url = query
    else:
        url = query

    yt_dlp_cmd = [
        "yt-dlp",
        "--cookies", "cookies.txt",
        "-f", "bestaudio",
        "-o", "-",
        "--quiet",
        "--no-warnings",
        url,
    ]

    ffmpeg_cmd = [
        "ffmpeg",
        "-i", "pipe:0",
        "-f", "mp3",
        "-codec:a", "libmp3lame",
        "-b:a", "192k",
        "-vn",
        "pipe:1",
    ]

    yt_process = subprocess.Popen(yt_dlp_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    ffmpeg_process = subprocess.Popen(ffmpeg_cmd, stdin=yt_process.stdout, stdout=subprocess.PIPE)
    yt_process.stdout.close()

    return StreamingResponse(iter_process_stdout(ffmpeg_process), media_type="audio/mpeg")
