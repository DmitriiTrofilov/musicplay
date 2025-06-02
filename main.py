from fastapi import FastAPI, Query, HTTPException, Response
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import subprocess
import asyncio

app = FastAPI()

# Allow all origins (adjust for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

COOKIES_PATH = "cookies.txt"  # your cookies file path for yt-dlp auth

def get_audio_url(ytdlp_url: str) -> str:
    """Use yt-dlp to get direct audio stream URL."""
    ytdlp_opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "nocheckcertificate": True,
        "skip_download": True,
        "cookiefile": COOKIES_PATH,
        "extract_flat": False,
    }
    with yt_dlp.YoutubeDL(ytdlp_opts) as ydl:
        info = ydl.extract_info(ytdlp_url, download=False)
        # If this is a playlist, pick first entry
        if "entries" in info:
            info = info["entries"][0]
        audio_url = info.get("url")
        if not audio_url:
            # fallback to formats
            formats = info.get("formats", [])
            for f in formats:
                if f.get("acodec") != "none":
                    audio_url = f.get("url")
                    break
        if not audio_url:
            raise ValueError("No audio URL found")
        return audio_url

@app.get("/stream")
async def stream_audio(mode: str = Query(...), query: str = Query(...)):
    """
    Modes:
    - playlist: query = playlist URL
    - song: query = song URL
    - genre: query = genre or search term
    """
    # Determine the yt-dlp search URL
    if mode == "playlist":
        url = query
    elif mode == "song":
        url = query
    elif mode == "genre":
        # yt-dlp YouTube search URL for genre term, pick first video
        url = f"ytsearch1:{query} audio"
    else:
        raise HTTPException(status_code=400, detail="Invalid mode")

    try:
        audio_url = get_audio_url(url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"yt-dlp error: {e}")

    # Stream audio to client via proxying with requests
    import httpx

    async def audio_streamer():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", audio_url) as r:
                if r.status_code != 200:
                    raise HTTPException(status_code=500, detail="Failed to fetch audio stream")
                async for chunk in r.aiter_bytes(chunk_size=1024*32):
                    yield chunk

    return StreamingResponse(audio_streamer(), media_type="audio/mpeg")
