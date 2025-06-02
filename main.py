from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import subprocess
import asyncio

app = FastAPI()

# Allow all origins for testing (adjust for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

async def iter_process_stdout(process):
    loop = asyncio.get_event_loop()
    while True:
        chunk = await loop.run_in_executor(None, process.stdout.read, 1024*8)
        if not chunk:
            break
        yield chunk

@app.get("/stream")
async def stream(mode: str = Query("search"), query: str = Query(...)):
    if not query:
        raise HTTPException(status_code=400, detail="Query parameter required")

    # Construct yt-dlp URL
    if mode == "search":
        url = f"ytsearch:{query}"
    elif mode == "playlist":
        url = query
    else:
        # fallback to direct URL or search
        url = query

    cmd = [
        "yt-dlp",
        "--cookies",
        "cookies.txt",
        "-f",
        "bestaudio",
        "-o",
        "-",
        "--quiet",
        "--no-warnings",
        url,
    ]

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    return StreamingResponse(iter_process_stdout(process), media_type="audio/mpeg")
