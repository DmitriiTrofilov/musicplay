import os
import logging
import json
import uuid
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from ytmusicapi import YTMusic
import yt_dlp
from threading import Thread
import time

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)

# --- App & Temp Directory Setup ---
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
TEMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'temp_audio')
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)
    logger.info(f"Created temporary audio directory: {TEMP_DIR}")

# --- YouTube Music API Setup ---
try:
    ytmusic = YTMusic()
    logger.info("YTMusic initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize YTMusic: {e}", exc_info=True)

COOKIES_FILE_PATH = 'cookies.txt'
absolute_cookies_path = os.path.abspath(COOKIES_FILE_PATH)

# --- File Cleanup ---
def cleanup_old_files():
    """Cleans up audio files older than 1 hour."""
    while True:
        time.sleep(600)  # Run cleanup every 10 minutes
        try:
            for filename in os.listdir(TEMP_DIR):
                file_path = os.path.join(TEMP_DIR, filename)
                if os.path.isfile(file_path):
                    if (time.time() - os.path.getmtime(file_path)) > 3600:
                        os.remove(file_path)
                        logger.info(f"Cleaned up old file: {filename}")
        except Exception as e:
            logger.error(f"Error during file cleanup: {e}")

# --- Main Endpoints ---
@app.route('/')
def health_check():
    return jsonify({"status": "ok", "message": "Backend is running (Download & Play Mode)!"}), 200

@app.route('/prepare_song', methods=['GET'])
def prepare_song():
    search_query = request.args.get('query')
    if not search_query:
        return jsonify({"error": "Search query is required"}), 400

    logger.info(f"PREPARE: Request received for query: \"{search_query}\"")

    try:
        # Create a unique filename for the eventual download
        unique_id = str(uuid.uuid4())
        output_filename = f"{unique_id}.webm"
        output_path = os.path.join(TEMP_DIR, output_filename)

        # --- CORE FIX: Combine Search & Download into a single atomic operation ---
        ydl_opts = {
            'format': 'bestaudio[ext=webm]/bestaudio/best',
            'outtmpl': output_path, # Tell it where to save the file
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
            'default_search': 'ytsearch1', # Search for 1 result
        }

        # Add cookies if the file exists. This is now used for the initial search AND download.
        if os.path.exists(absolute_cookies_path):
            logger.info("Using cookies file for download operation.")
            ydl_opts['cookiefile'] = absolute_cookies_path
        else:
            logger.warning("cookies.txt not found. Authentication may fail.")

        logger.info(f"DOWNLOAD: Starting search and download for: \"{search_query}\"")

        # Use 'with' to ensure resources are managed correctly
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # This call will now search AND download in one step, using the cookies
            info = ydl.extract_info(search_query, download=True)
            # The info dict for the downloaded file is in the 'entries' list
            if not info.get('entries'):
                raise yt_dlp.utils.DownloadError("No video found from search.")
            
            # Since we searched for one, it's the first entry
            song_info = info['entries'][0]

        logger.info(f"DOWNLOAD: Finished download for \"{search_query}\"")

        # Prepare the response for the frontend
        song_details = {
            "title": song_info.get('title', 'Unknown Title'),
            "artist": song_info.get('artist') or song_info.get('channel') or 'Unknown Artist',
            "video_id": song_info.get('id'),
            "duration_seconds": song_info.get('duration', 0),
            "thumbnail_url": song_info.get('thumbnail', ''), # yt-dlp uses 'thumbnail' key here
        }

        play_url = f"/audio/{output_filename}"

        return jsonify({
            "status": "success",
            "message": "Song downloaded and ready for playback.",
            "song_details": song_details,
            "play_url": play_url
        })

    except yt_dlp.utils.DownloadError as de:
        error_string = str(de).lower()
        if 'sign in' in error_string or 'authentication' in error_string:
            logger.error(f"PREPARE: Authentication error for \"{search_query}\".")
            return jsonify({"error": "Authentication Error: Your cookies.txt file may be invalid or expired."}), 403
        else:
            logger.error(f"PREPARE: yt-dlp DownloadError for \"{search_query}\": {de}")
            return jsonify({"error": "A download error occurred while fetching the song."}), 500
    except Exception as e:
        logger.error(f"PREPARE: Unexpected error for \"{search_query}\": {e}", exc_info=True)
        return jsonify({"error": "An unexpected server error occurred."}), 500

@app.route('/audio/<filename>')
def serve_audio(filename):
    """Serves the downloaded audio file from the temp directory."""
    logger.info(f"SERVE: Client requesting audio file: {filename}")
    return send_from_directory(TEMP_DIR, filename, as_attachment=False)

if __name__ == '__main__':
    cleanup_thread = Thread(target=cleanup_old_files, daemon=True)
    cleanup_thread.start()
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
