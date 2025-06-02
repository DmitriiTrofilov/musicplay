from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
from ytmusicapi import YTMusic
import yt_dlp
import os
import uuid
import logging
import time # Added for retry delay

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app) # Enable CORS for all routes, or restrict to your local dev domain

# --- YouTube Music API Setup ---
try:
    ytmusic = YTMusic()
    logger.info("YTMusic initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize YTMusic: {e}")
    # Depending on the error, you might want to prevent the app from starting
    # or handle this gracefully in your routes.

# --- yt-dlp Configuration for MP3 download ---
TEMP_AUDIO_DIR = './temp_audio' # Define as a constant
YDL_OPTS_BASE = {
    'format': 'bestaudio/best',
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
    'noplaylist': True,
    'quiet': False, # Set to False for more verbose logging on Render
    'no_warnings': False,
    'paths': {'home': TEMP_AUDIO_DIR},
    'outtmpl': os.path.join(TEMP_AUDIO_DIR, '%(id)s.%(ext)s'), # Simplified outtmpl
    'ffmpeg_location': '/usr/bin/ffmpeg' # Explicitly set if known, or let yt-dlp find it.
                                          # On Render, you might need to ensure ffmpeg is in PATH
                                          # or use a buildpack/Dockerfile to install it.
}


# Ensure the temporary directory exists
if not os.path.exists(TEMP_AUDIO_DIR):
    try:
        os.makedirs(TEMP_AUDIO_DIR)
        logger.info(f"Created temporary audio directory: {TEMP_AUDIO_DIR}")
    except Exception as e:
        logger.error(f"Error creating temporary audio directory {TEMP_AUDIO_DIR}: {e}")


@app.route('/')
def health_check():
    return jsonify({"status": "ok", "message": "Backend is running!"}), 200

@app.route('/search_and_download', methods=['GET'])
def search_and_download():
    search_query = request.args.get('query')
    if not search_query:
        logger.warning("Search query missing.")
        return jsonify({"error": "Search query is required"}), 400

    logger.info(f"Received search query: {search_query}")

    try:
        # 1. Search on YouTube Music
        logger.info(f"Searching for: {search_query}")
        search_results = ytmusic.search(search_query, filter='songs')

        if not search_results:
            logger.warning(f"No songs found for query: {search_query}")
            return jsonify({"error": "No songs found for your query"}), 404

        first_song = search_results[0]
        video_id = first_song['videoId']
        song_title = first_song.get('title', 'Unknown Title')
        song_artist = first_song.get('artists', [{'name': 'Unknown Artist'}])[0]['name']
        logger.info(f"Found song: {song_title} by {song_artist} (ID: {video_id})")

        if not video_id:
            logger.error("Could not get video ID for the song.")
            return jsonify({"error": "Could not get video ID for the song"}), 500

        # 2. Download the audio using yt-dlp
        # Generate a unique filename for the final MP3 to avoid conflicts.
        # The initial download might use the video_id.
        unique_mp3_filename = f"{str(uuid.uuid4())}.mp3"
        final_mp3_path = os.path.join(TEMP_AUDIO_DIR, unique_mp3_filename)

        ydl_opts_specific = YDL_OPTS_BASE.copy()
        # yt-dlp will add .mp3 due to postprocessor, outtmpl should reflect the name *before* postprocessing
        # So we'll let it name it based on video_id first, then rename to our unique_mp3_filename if needed,
        # or better, directly control the final output name if yt-dlp allows that for postprocessed files.
        # For simplicity with current yt-dlp options:
        # We expect the file to be video_id.mp3 after conversion.

        # Path where yt-dlp is expected to save the processed mp3
        expected_downloaded_mp3_path = os.path.join(TEMP_AUDIO_DIR, f"{video_id}.mp3")

        # Clean up any pre-existing file with the same video_id to avoid confusion
        if os.path.exists(expected_downloaded_mp3_path):
            try:
                os.remove(expected_downloaded_mp3_path)
                logger.info(f"Removed pre-existing file: {expected_downloaded_mp3_path}")
            except Exception as e:
                logger.warning(f"Could not remove pre-existing file {expected_downloaded_mp3_path}: {e}")


        with yt_dlp.YoutubeDL(ydl_opts_specific) as ydl:
            logger.info(f"Starting download for video ID: {video_id}")
            # Download URL must be a valid YouTube video URL
            ydl.download([f'https://music.youtube.com/watch?v={video_id}'])
            logger.info(f"Download process completed for video ID: {video_id}. Expected at: {expected_downloaded_mp3_path}")


        # Check if the expected MP3 file exists
        if not os.path.exists(expected_downloaded_mp3_path):
            logger.error(f"MP3 file not found after download attempt: {expected_downloaded_mp3_path}")
            # Check other possible extensions if format conversion was unexpected
            possible_original_extensions = ['.webm', '.m4a', '.opus']
            found_original = None
            for ext in possible_original_extensions:
                original_file_path = os.path.join(TEMP_AUDIO_DIR, f"{video_id}{ext}")
                if os.path.exists(original_file_path):
                    logger.warning(f"Found original downloaded file: {original_file_path}. FFmpeg might have failed or is slow.")
                    found_original = original_file_path
                    break
            if found_original:
                 return jsonify({"error": f"Audio downloaded but MP3 conversion might have failed. Found: {os.path.basename(found_original)}"}), 500
            return jsonify({"error": "Could not process the audio file (MP3 not found after download)."}), 500

        # Rename to the unique path to avoid issues if another request for the same video_id comes in
        # before this one is cleaned up. This step is more for robustness.
        os.rename(expected_downloaded_mp3_path, final_mp3_path)
        logger.info(f"Renamed downloaded file to unique path: {final_mp3_path}")


        # 3. Send the MP3 file
        logger.info(f"Sending file: {final_mp3_path}")
        response = send_file(
            final_mp3_path,
            as_attachment=True, # Important for JavaScript blob handling
            download_name=f"{song_artist} - {song_title}.mp3",
            mimetype='audio/mpeg'
        )

        @response.call_on_close
        def cleanup_file():
            try:
                if os.path.exists(final_mp3_path):
                    os.remove(final_mp3_path)
                    logger.info(f"Cleaned up temporary file: {final_mp3_path}")
                else:
                    logger.warning(f"Cleanup: File not found to delete: {final_mp3_path}")
            except Exception as e:
                logger.error(f"Error cleaning up file {final_mp3_path}: {e}")

        return response

    except yt_dlp.utils.DownloadError as de:
        logger.error(f"yt-dlp DownloadError: {de}")
        return jsonify({"error": f"Failed to download audio: {str(de)}"}), 500
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}", exc_info=True) # exc_info=True for traceback
        return jsonify({"error": f"An internal server error occurred: {str(e)}"}), 500

if __name__ == '__main__':
    # Port is typically set by Render via PORT environment variable
    port = int(os.environ.get('PORT', 5000))
    # For local testing, you might run this directly, but for Render, Gunicorn is preferred.
    app.run(host='0.0.0.0', port=port, debug=False) # debug=False for production
