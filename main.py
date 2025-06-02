from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from yt_dlp import YoutubeDL
import httpx
import asyncio
import logging

app = FastAPI()
logging.basicConfig(level=logging.INFO)

# Path to your cookies file for yt-dlp authentication
COOKIES_FILE = "cookies.txt"

def extract_audio_url(url: str):
    ydl_opts = {
        'ignoreerrors': True,  # skip videos with errors
        'quiet': True,
        'no_warnings': True,
        'format': 'bestaudio/best',
        'cookies': COOKIES_FILE,
        'extract_flat': True,  # get metadata only, no download
    }
    logging.info(f"Extracting info from URL: {url}")
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise HTTPException(status_code=404, detail="No info found for URL")
    # For playlists, extract first available video URL
    if 'entries' in info:
        for entry in info['entries']:
            if entry is None:
                continue
            # Try to get the direct audio URL from each entry
            with YoutubeDL({**ydl_opts, 'extract_flat': False}) as ydl_d:
                try:
                    video_info = ydl_d.extract_info(entry['url'], download=False)
                    audio_url = video_info.get('url')
                    if audio_url:
                        logging.info(f"Found audio URL: {audio_url}")
                        return audio_url
                except Exception as e:
                    logging.warning(f"Skipping video due to error: {e}")
        raise HTTPException(status_code=404, detail="No playable videos found in playlist")
    else:
        # Single video case
        audio_url = info.get('url')
        if audio_url:
            logging.info(f"Found audio URL: {audio_url}")
            return audio_url
        raise HTTPException(status_code=404, detail="No playable audio URL found")

@app.get("/stream")
async def stream(mode: str = Query(..., regex="^(playlist|song|genre)$"), query: str = Query(...)):
    logging.info(f"Received /stream request mode={mode} query={query}")
    
    # Basic mode handling: map genre or song name to a YouTube search URL
    if mode == "genre":
        # Search YouTube Music by genre, then get first playlist/video (simplified)
        search_url = f"ytsearch5:{query} music"
        target_url = search_url
    elif mode == "song":
        # Direct song URL or YouTube search
        if query.startswith("http"):
            target_url = query
        else:
            target_url = f"ytsearch1:{query}"
    elif mode == "playlist":
        # Use playlist URL directly
        target_url = query
    else:
        raise HTTPException(status_code=400, detail="Invalid mode")
    
    logging.info(f"Using target URL: {target_url}")

    # Extract direct audio URL
    try:
        audio_url = extract_audio_url(target_url)
    except Exception as e:
        logging.error(f"yt-dlp extraction failed: {e}")
        return JSONResponse(status_code=500, content={"detail": f"yt-dlp error: {str(e)}"})

    # Stream the audio URL as a proxy
    async def audio_stream():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", audio_url) as response:
                if response.status_code != 200:
                    logging.error(f"Failed to fetch audio stream, status {response.status_code}")
                    yield b""
                    return
                async for chunk in response.aiter_bytes(1024 * 32):
                    yield chunk

    logging.info("Starting audio stream response")
    return StreamingResponse(audio_stream(), media_type="audio/mpeg")
