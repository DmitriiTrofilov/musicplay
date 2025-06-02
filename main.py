from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import subprocess
import tempfile
import os
import urllib.parse
import logging

app = FastAPI()

COOKIES_FILE = "cookies.txt"

logging.basicConfig(level=logging.INFO)

def download_audio_stream(url: str):
    logging.info(f"Attempting to download audio stream for: {url}")

    ydl_opts = [
        "yt-dlp",
        "--cookies", COOKIES_FILE,
        "-f", "bestaudio",
        "-o", "-",  # output to stdout
        "--quiet",
        "--no-warnings"
    ]

    command = ydl_opts + [url]
    logging.info(f"Running command: {' '.join(command)}")

    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return process
    except Exception as e:
        logging.error(f"yt-dlp execution failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/stream")
async def stream_audio(request: Request):
    mode = request.query_params.get("mode")
    query = request.query_params.get("query")

    if not mode or not query:
        raise HTTPException(status_code=400, detail="Missing 'mode' or 'query' parameter")

    logging.info(f"Received stream request. Mode: {mode}, Query: {query}")

    if mode == "genre":
        # Example: map genre to a known YT video
        genre_map = {
            "lofi": "https://www.youtube.com/watch?v=jfKfPfyJRdk",
            "jazz": "https://www.youtube.com/watch?v=Dx5qFachd3A"
        }
        url = genre_map.get(query.lower())
        if not url:
            raise HTTPException(status_code=404, detail="Genre not supported")
    elif mode == "playlist" or mode == "song":
        # Decode URL in case it's encoded
        url = urllib.parse.unquote(query)
    else:
        raise HTTPException(status_code=400, detail="Invalid mode")

    process = download_audio_stream(url)

    def stream_generator():
        try:
            for chunk in iter(lambda: process.stdout.read(4096), b''):
                yield chunk
        finally:
            process.kill()

    return StreamingResponse(stream_generator(), media_type="audio/mpeg")
