import logging
import shlex
import subprocess
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse

app = FastAPI()

# Configure logging to show INFO level messages and above
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

@app.get("/stream")
def stream_audio(query: str = Query(..., min_length=1)):
    logging.info(f"Received stream request with query: '{query}'")

    # Prepare yt-dlp command
    command = (
        f"yt-dlp -o - --quiet --no-warnings "
        f"-f bestaudio[ext=m4a]/bestaudio "
        f"--external-downloader ffmpeg "
        f"--external-downloader-args '-vn -f mp3' "
        f"ytsearch1:{shlex.quote(query)}"
    )
    logging.info(f"Running command: {command}")

    try:
        process = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except Exception as e:
        logging.error(f"Failed to start yt-dlp process: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to start yt-dlp: {e}")

    if not process.stdout:
        logging.error("No stdout pipe from yt-dlp process.")
        raise HTTPException(status_code=500, detail="Failed to capture yt-dlp output")

    logging.info("yt-dlp process started successfully, streaming audio now...")

    headers = {
        "Content-Type": "audio/mpeg",
        "Cache-Control": "no-cache",
        "Accept-Ranges": "bytes",
    }

    # Return StreamingResponse to stream raw mp3 data
    response = StreamingResponse(process.stdout, headers=headers)

    # Log when the response is returned
    logging.info("StreamingResponse prepared and returned to client.")

    return response
