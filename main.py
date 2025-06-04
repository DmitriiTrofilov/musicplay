from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from ytmusicapi import YTMusic
import yt_dlp # Keep for yt_dlp.utils.DownloadError if needed for error types
import os
import logging
import time
import json # For SSE data
import subprocess # For running yt-dlp as a subprocess for streaming

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# --- YouTube Music API Setup ---
try:
    ytmusic = YTMusic()
    logger.info("YTMusic initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize YTMusic: {e}")

# --- yt-dlp Configuration ---
# No TEMP_AUDIO_DIR needed for streaming
COOKIES_FILE_PATH = 'cookies.txt'
absolute_cookies_path = os.path.abspath(COOKIES_FILE_PATH)

# Base yt-dlp options for getting info (not for direct streaming pipe yet)
YDL_OPTS_INFO = {
    'quiet': True,
    'no_warnings': True,
    'skip_download': True, # We only want info
    'dumpjson': True, # Get metadata as JSON
}
if os.path.exists(absolute_cookies_path):
    YDL_OPTS_INFO['cookiefile'] = absolute_cookies_path
    logger.info(f"Cookies file found at {absolute_cookies_path}. Using for yt-dlp info.")
else:
    logger.warning(
        f"Cookies file not found at {absolute_cookies_path}. "
        f"yt-dlp will run without cookies. This may lead to 'Sign in to confirm' errors."
    )


@app.route('/')
def health_check():
    return jsonify({"status": "ok", "message": "Backend is running!"}), 200

def sse_message(data):
    """Helper to format SSE messages."""
    return f"data: {json.dumps(data)}\n\n"

@app.route('/search_and_prepare_song_sse')
def search_and_prepare_song_sse():
    search_query = request.args.get('query')
    if not search_query:
        def error_gen():
            yield sse_message({"status": "error", "message": "Search query is required"})
        return Response(error_gen(), mimetype='text/event-stream')

    logger.info(f"SSE request for search query: {search_query}")

    def generate_events():
        try:
            yield sse_message({"status": "searching", "message": f"Searching for \"{search_query}\"..."})
            
            search_results = ytmusic.search(search_query, filter='songs')
            if not search_results:
                yield sse_message({"status": "error", "message": f"No songs found for \"{search_query}\""})
                return

            first_song = search_results[0]
            video_id = first_song['videoId']
            song_title = first_song.get('title', 'Unknown Title')
            song_artist = first_song.get('artists', [{'name': 'Unknown Artist'}])[0]['name']
            duration_seconds = first_song.get('duration_seconds', 0) # Get duration if available
            thumbnail_url = first_song.get('thumbnails', [{}])[0].get('url', '')


            if not video_id:
                yield sse_message({"status": "error", "message": "Could not get video ID for the song."})
                return

            song_details = {
                "title": song_title,
                "artist": song_artist,
                "video_id": video_id,
                "duration_seconds": duration_seconds,
                "thumbnail_url": thumbnail_url
            }

            yield sse_message({
                "status": "found", 
                "message": f"Found: {song_title} by {song_artist}",
                "video_id": video_id, # Redundant here but consistent
                "song_details": song_details
            })
            
            # Optionally, you could add a step here to verify streamability with yt-dlp --dump-json
            # For now, assume it's streamable if found by ytmusicapi

            yield sse_message({
                "status": "ready_to_stream",
                "message": f"Ready to stream: {song_title}",
                "video_id": video_id,
                "song_details": song_details
            })

        except Exception as e:
            logger.error(f"Error in SSE generation for '{search_query}': {e}", exc_info=True)
            yield sse_message({"status": "error", "message": f"An unexpected error occurred: {str(e)}"})

    return Response(generate_events(), mimetype='text/event-stream')


@app.route('/stream_audio/<video_id>')
def stream_audio(video_id):
    if not video_id:
        return jsonify({"error": "Video ID is required"}), 400

    logger.info(f"Request to stream audio for video_id: {video_id}")

    # yt-dlp command for streaming
    # We want raw audio data, 'bestaudio' is usually opus in webm or aac in m4a.
    # `-f bestaudio` picks the best audio-only format.
    # `-o -` outputs to stdout.
    command = [
        'yt-dlp',
        '-f', 'bestaudio', # Or 'bestaudio[ext=m4a]', 'bestaudio[ext=webm]' if you want to force
        '-o', '-',       # Output to stdout
        '--quiet',       # Suppress yt-dlp console output to keep stdout clean for audio
        '--no-warnings',
        f'https://music.youtube.com/watch?v={video_id}'
    ]

    if os.path.exists(absolute_cookies_path):
        command.extend(['--cookies', absolute_cookies_path])
        logger.info(f"Using cookies for streaming video_id: {video_id}")

    logger.info(f"Streaming command: {' '.join(command)}")
    
    try:
        # Start the yt-dlp process
        # bufsize=-1 means use system default, usually fully buffered. For streaming, smaller might be better
        # but Popen's stdout is a pipe, so it should be fine.
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Stream the output
        @stream_with_context
        def generate_audio_chunks():
            try:
                logger.info(f"Starting to stream chunks for {video_id}")
                # Read and yield chunks from yt-dlp's stdout
                for chunk in iter(lambda: process.stdout.read(8192), b''): # Read in 8KB chunks
                    yield chunk
                logger.info(f"Finished yielding chunks for {video_id}. Waiting for process to exit.")
                process.stdout.close() # Ensure stdout is closed
                process.wait() # Wait for the process to terminate
            except Exception as gen_exc:
                logger.error(f"Error during audio chunk generation for {video_id}: {gen_exc}", exc_info=True)
            finally:
                # Ensure the process is terminated when streaming is done or client disconnects
                if process.poll() is None: # If process is still running
                    logger.warning(f"yt-dlp process for {video_id} still running after stream. Terminating.")
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        logger.warning(f"yt-dlp process for {video_id} did not terminate gracefully. Killing.")
                        process.kill()
                
                stderr_output = process.stderr.read().decode(errors='ignore').strip()
                if stderr_output:
                    # Log as warning if it's just info, error if return code is non-zero
                    if process.returncode != 0:
                        logger.error(f"yt-dlp stderr for {video_id} (code {process.returncode}): {stderr_output}")
                    else:
                        logger.info(f"yt-dlp stderr for {video_id} (code {process.returncode}): {stderr_output}") # Some formats print info to stderr
                else:
                     logger.info(f"yt-dlp process for {video_id} exited with code {process.returncode}. No stderr output.")
                process.stderr.close()

        # Mimetype: 'bestaudio' usually results in WebM (Opus) or M4A (AAC).
        # Modern browsers are good at sniffing. 'audio/webm' or 'audio/aac' or 'application/octet-stream'
        # Let's try 'audio/webm' as a common default from 'bestaudio'.
        # If issues, 'application/octet-stream' is safer and lets browser decide.
        # Or, one could run `yt-dlp --print-json` in the SSE step to get the exact extension and pass it.
        return Response(generate_audio_chunks(), mimetype='audio/webm')

    except yt_dlp.utils.DownloadError as de: # This might not be caught here if subprocess fails
        error_message = str(de)
        logger.error(f"yt-dlp DownloadError during stream setup for {video_id}: {error_message}", exc_info=True)
        if "Sign in to confirm" in error_message:
             return jsonify({"error": "Failed to stream due to authentication issue. Cookies might be required or invalid."}), 500
        return jsonify({"error": f"Failed to stream audio: {error_message}"}), 500
    except Exception as e:
        logger.error(f"Error starting yt-dlp stream process for {video_id}: {e}", exc_info=True)
        return jsonify({"error": f"Failed to start audio stream: {str(e)}"}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    # debug=True can cause issues with subprocesses and SSE in some environments.
    # Use threaded=True for handling multiple requests like SSE and audio stream.
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
