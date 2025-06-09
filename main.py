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
        try:
            for filename in os.listdir(TEMP_DIR):
                file_path = os.path.join(TEMP_DIR, filename)
                if os.path.isfile(file_path):
                    # Check if the file is older than 3600 seconds (1 hour)
                    if (time.time() - os.path.getmtime(file_path)) > 3600:
                        os.remove(file_path)
                        logger.info(f"Cleaned up old file: {filename}")
        except Exception as e:
            logger.error(f"Error during file cleanup: {e}")
        time.sleep(600) # Run cleanup every 10 minutes

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
        # Step 1: Search for the song to get its video ID and metadata
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'default_search': 'ytsearch1'}) as ydl:
            search_results = ydl.extract_info(search_query, download=False)
            if not search_results.get('entries'):
                logger.warning(f"PREPARE: No results found for \"{search_query}\"")
                return jsonify({"error": f"No results found for '{search_query}'"}), 404
            
            info = search_results['entries'][0]
            video_id = info.get('id')
            if not video_id:
                return jsonify({"error": "Could not extract video ID from search result."}), 500

        # Create a unique filename for the downloaded audio
        unique_id = str(uuid.uuid4())
        # Use a compatible extension like .webm which yt-dlp prefers for bestaudio
        output_filename = f"{unique_id}.webm"
        output_path = os.path.join(TEMP_DIR, output_filename)

        # Step 2: Set up yt-dlp options for downloading
        ydl_opts = {
            'format': 'bestaudio[ext=webm]/bestaudio/best',
            'outtmpl': output_path,
            'quiet': True,
            'no_warnings': True,
            'noplaylist': True,
        }
        if os.path.exists(absolute_cookies_path):
            ydl_opts['cookiefile'] = absolute_cookies_path

        # Step 3: Download the audio file
        logger.info(f"DOWNLOAD: Starting download for {video_id} to {output_path}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f'https://www.youtube.com/watch?v={video_id}'])
        
        logger.info(f"DOWNLOAD: Finished download for {video_id}")

        # Step 4: Prepare the response for the frontend
        song_details = {
            "title": info.get('title', 'Unknown Title'),
            "artist": info.get('artist') or info.get('channel') or 'Unknown Artist',
            "video_id": video_id,
            "duration_seconds": info.get('duration', 0),
            "thumbnail_url": info.get('thumbnails', [{}])[-1].get('url', ''),
        }

        # The URL the frontend will use to fetch the downloaded file
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
            return jsonify({"error": "Authentication Error: Your cookies.txt file may be invalid."}), 403
        else:
            logger.error(f"PREPARE: yt-dlp DownloadError for \"{search_query}\": {de}")
            return jsonify({"error": "A download error occurred."}), 500
    except Exception as e:
        logger.error(f"PREPARE: Unexpected error for \"{search_query}\": {e}", exc_info=True)
        return jsonify({"error": "An unexpected server error occurred."}), 500

@app.route('/audio/<filename>')
def serve_audio(filename):
    """Serves the downloaded audio file from the temp directory."""
    logger.info(f"SERVE: Client requesting audio file: {filename}")
    return send_from_directory(TEMP_DIR, filename, as_attachment=False)

if __name__ == '__main__':
    # Start the cleanup thread
    cleanup_thread = Thread(target=cleanup_old_files, daemon=True)
    cleanup_thread.start()
    
    port = int(os.environ.get('PORT', 5001))
    # Use a production-ready server like gunicorn or waitress instead of app.run in production
    app.run(host='0.0.0.0', port=port, debug=False, threaded=True)
