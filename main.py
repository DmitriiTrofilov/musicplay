import logging
import shlex
import subprocess
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import Response

app = FastAPI()

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

@app.get("/stream")
def stream_audio(query: str = Query(..., min_length=1)):
    logging.info(f"Received stream request with query: '{query}'")

    # yt-dlp command with cookies.txt usage
    command = (
        f"yt-dlp --cookies cookies.txt -o - --quiet --no-warnings "
        f"-f bestaudio[ext=m4a]/bestaudio "
        f"ytsearch1:{shlex.quote(query)}"
    )
    logging.info(f"Running command: {command}")

    try:
        completed_process = subprocess.run(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        logging.error(f"yt-dlp failed: {e.stderr.decode(errors='ignore')}")
        raise HTTPException(status_code=500, detail="yt-dlp failed or no audio found")

    audio_data = completed_process.stdout
    data_len = len(audio_data)

    logging.info(f"yt-dlp finished successfully, downloaded {data_len} bytes of audio")

    headers = {
        "Content-Type": "audio/mpeg",
        "Content-Length": str(data_len),
        "Cache-Control": "no-cache",
        "Accept-Ranges": "bytes",
    }

    logging.info("Sending full audio response to client")
    return Response(content=audio_data, headers=headers, media_type="audio/mpeg")
