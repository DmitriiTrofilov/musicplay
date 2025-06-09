import os
import logging
import json
import uuid
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import yt_dlp
from threading import Thread
import time

# --- Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)
app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})
TEMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'temp_audio')
if not os.path.exists(TEMP_DIR):
    os.makedirs(TEMP_DIR)

# --- Cookies & API ---
COOKIES_FILE_PATH = 'cookies.txt'
absolute_cookies_path = os.path.abspath(COOKIES_FILE_PATH)

# --- File Cleanup ---
def cleanup_old_files():
    while True:
        time.sleep(600)
        try:
            for filename in os.listdir(TEMP_DIR):
                file_path = os.path.join(TEMP_DIR, filename)
                if os.path.isfile(file_path) and (time.time() - os.path.getmtime(file_path)) > 3600:
                    os.remove(file_path)
                    logger.info(f"Cleaned up old file: {filename}")
        except Exception as e:
            logger.error(f"Error during file cleanup: {e}")

# --- Helper ---
def get_ydl_opts():
    opts = {'format': 'bestaudio[ext=webm]/bestaudio/best', 'noplaylist': True, 'quiet': True, 'no_warnings': True}
    if os.path.exists(absolute_cookies_path):
        opts['cookiefile'] = absolute_cookies_path
    return opts

# --- Endpoints ---
@app.route('/')
def health_check():
    return jsonify({"status": "ok", "message": "Backend is running (Download & Play Mode)!"}), 200

# NEW: Lightweight endpoint for preloading metadata
@app.route('/get_song_info', methods=['GET'])
def get_song_info():
    search_query = request.args.get('query')
    if not search_query:
        return jsonify({"error": "Search query is required"}), 400
    
    logger.info(f"INFO: Request for query: \"{search_query}\"")
    
    try:
        ydl_opts = get_ydl_opts()
        ydl_opts['extract_flat'] = True
        ydl_opts['default_search'] = 'ytsearch1'

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_query, download=False)
            if not info.get('entries'):
                raise yt_dlp.utils.DownloadError("No video found from search.")
            song_info = info['entries'][0]

        song_details = {
            "title": song_info.get('title', 'Unknown Title'),
            "artist": song_info.get('artist') or song_info.get('channel') or 'Unknown Artist',
            "video_id": song_info.get('id'),
            "duration_seconds": song_info.get('duration', 0),
            "thumbnail_url": song_info.get('thumbnail', ''),
        }
        return jsonify({"status": "success", "song_details": song_details})

    except Exception as e:
        logger.error(f"INFO: Unexpected error for \"{search_query}\": {e}", exc_info=True)
        return jsonify({"error": "An unexpected server error occurred."}), 500


@app.route('/prepare_song', methods=['GET'])
def prepare_song():
    search_query = request.args.get('query')
    if not search_query:
        return jsonify({"error": "Search query is required"}), 400

    logger.info(f"PREPARE: Request for query: \"{search_query}\"")
    try:
        output_filename = f"{uuid.uuid4()}.webm"
        output_path = os.path.join(TEMP_DIR, output_filename)
        
        ydl_opts = get_ydl_opts()
        ydl_opts['outtmpl'] = output_path
        ydl_opts['default_search'] = 'ytsearch1'

        logger.info(f"DOWNLOAD: Starting search and download for: \"{search_query}\"")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(search_query, download=True)
            if not info.get('entries'):
                raise yt_dlp.utils.DownloadError("No video found from search.")
            song_info = info['entries'][0]

        logger.info(f"DOWNLOAD: Finished for \"{search_query}\"")
        song_details = {
            "title": song_info.get('title', 'Unknown Title'),
            "artist": song_info.get('artist') or song_info.get('channel') or 'Unknown Artist',
            "video_id": song_info.get('id'),
            "duration_seconds": song_info.get('duration', 0),
            "thumbnail_url": song_info.get('thumbnail', ''),
        }
        play_url = f"/audio/{output_filename}"

        return jsonify({"status": "success", "song_details": song_details, "play_url": play_url})

    except yt_dlp.utils.DownloadError as de:
        error_string = str(de).lower()
        if 'sign in' in error_string or 'authentication' in error_string:
            return jsonify({"error": "Authentication Error: Cookies may be invalid."}), 403
        else:
            return jsonify({"error": "A download error occurred."}), 500
    except Exception as e:
        logger.error(f"PREPARE: Unexpected error for \"{search_query}\": {e}", exc_info=True)
        return jsonify({"error": "An unexpected server error occurred."}), 500

@app.route('/audio/<filename>')
def serve_audio(filename):
    logger.info(f"SERVE: Client requesting audio file: {filename}")
    return send_from_directory(TEMP_DIR, filename, as_attachment=False)

if __name__ == '__main__':
    cleanup_thread = Thread(target=cleanup_old_files, daemon=True)
    cleanup_thread.start()
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
