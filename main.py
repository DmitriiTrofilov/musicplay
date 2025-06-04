import flask
from flask import Flask, request, send_file, jsonify
from flask_cors import CORS
from ytmusicapi import YTMusic
import yt_dlp
import os
import uuid
import logging
import time

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# --- YouTube Music API Setup ---
try:
    ytmusic = YTMusic()
    logger.info("YTMusic initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize YTMusic: {e}", exc_info=True)
    # Consider how to handle this - app might not be functional

# --- yt-dlp Configuration ---
TEMP_AUDIO_DIR_NAME = 'temp_audio'
TEMP_AUDIO_DIR = os.path.abspath(TEMP_AUDIO_DIR_NAME)

if not os.path.exists(TEMP_AUDIO_DIR):
    try:
        os.makedirs(TEMP_AUDIO_DIR)
        logger.info(f"Created temporary audio directory: {TEMP_AUDIO_DIR}")
    except Exception as e:
        logger.error(f"Error creating temporary audio directory {TEMP_AUDIO_DIR}: {e}", exc_info=True)

COOKIES_FILE_PATH = 'cookies.txt'
absolute_cookies_path = os.path.abspath(COOKIES_FILE_PATH)

YDL_OPTS_BASE = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': False,        # Set to True for less console output from yt-dlp in production
    'no_warnings': False,  # Set to True to suppress yt-dlp warnings
    'paths': {'home': TEMP_AUDIO_DIR},
    # 'outtmpl' will be set per download to ensure unique naming based on video_id
    'verbose': True,       # Good for debugging, can be set to False in production
    'nocheckcertificate': True, # Can sometimes help with SSL issues on certain networks
    # Consider adding a modern User-Agent if issues persist
    # 'http_headers': {
    #     'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
    # },
}

if os.path.exists(absolute_cookies_path):
    logger.info(f"Cookies file found at {absolute_cookies_path}. Adding to yt-dlp options.")
    YDL_OPTS_BASE['cookiefile'] = absolute_cookies_path
else:
    logger.warning(
        f"Cookies file not found at {absolute_cookies_path}. "
        f"yt-dlp will run without cookies. This may lead to 'Sign in to confirm' or availability errors."
    )


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
        logger.info(f"Searching for: '{search_query}' using YTMusic API")
        # Fetch a few results to try if the first one fails
        search_results = ytmusic.search(search_query, filter='songs', limit=3)

        if not search_results:
            logger.warning(f"No songs found for query: '{search_query}' via YTMusic API")
            return jsonify({"error": "No songs found for your query"}), 404

        downloaded_successfully = False
        last_download_error_message = "No suitable video found or all download attempts failed."
        response_file_path = None
        final_song_title = "Unknown Title"
        final_song_artist = "Unknown Artist"
        final_downloaded_ext = "unknown"

        for i, song_data in enumerate(search_results):
            video_id = song_data.get('videoId')
            current_song_title = song_data.get('title', f'Unknown Title {i+1}')
            artist_info = song_data.get('artists', [{'name': f'Unknown Artist {i+1}'}])
            current_song_artist = artist_info[0]['name'] if artist_info else f'Unknown Artist {i+1}'

            logger.info(f"Attempt {i+1}/{len(search_results)}: Trying '{current_song_title}' by '{current_song_artist}' (ID: {video_id})")

            if not video_id:
                logger.warning(f"Skipping result {i+1} due to missing videoId.")
                last_download_error_message = "A search result was missing a video ID."
                continue

            # Use a fresh copy of YDL_OPTS_BASE and set a specific output template for this attempt
            ydl_opts_specific = YDL_OPTS_BASE.copy()
            # This ensures the downloaded file is named 'videoId.ext' in TEMP_AUDIO_DIR
            ydl_opts_specific['outtmpl'] = {'default': f'{video_id}.%(ext)s'}


            # Define paths for this attempt
            # Base name for the uniquely named file we'll send to the user (extension added later)
            unique_temp_filename_base = str(uuid.uuid4())
            # Path where yt-dlp will download (e.g., /temp_audio/videoId.webm)
            # Extension will be determined by yt-dlp
            expected_download_path_pattern = os.path.join(TEMP_AUDIO_DIR, f"{video_id}.") # Note the dot

            # Clean up any pre-existing files from previous (possibly failed) attempts for this video_id
            for f_cleanup in os.listdir(TEMP_AUDIO_DIR):
                if f_cleanup.startswith(video_id + "."): # Matches videoId.ext
                    try:
                        os.remove(os.path.join(TEMP_AUDIO_DIR, f_cleanup))
                        logger.info(f"Removed pre-existing/partial file: {f_cleanup}")
                    except Exception as e_clean:
                        logger.warning(f"Could not remove pre-existing file {f_cleanup}: {e_clean}")
            
            try:
                with yt_dlp.YoutubeDL(ydl_opts_specific) as ydl:
                    # Try standard YouTube URL, often more robust
                    download_url = f'https://www.youtube.com/watch?v={video_id}'
                    logger.info(f"Starting download for video ID: {video_id} from {download_url} with options: {ydl_opts_specific}")
                    
                    ydl.download([download_url])
                    logger.info(f"Download process reported as complete for video ID: {video_id}.")

                # Find the downloaded file and its extension
                downloaded_file_name = None
                for f_in_dir in os.listdir(TEMP_AUDIO_DIR):
                    if f_in_dir.startswith(video_id + "."):
                        downloaded_file_name = f_in_dir
                        break
                
                if not downloaded_file_name:
                    logger.error(f"Audio file NOT FOUND after download attempt for video ID: {video_id} (expected pattern: {expected_download_path_pattern}*).")
                    logger.info(f"Contents of {TEMP_AUDIO_DIR}: {os.listdir(TEMP_AUDIO_DIR)}")
                    last_download_error_message = f"Audio file not found post-download for {video_id}."
                    continue # Try next song in search_results

                actual_downloaded_path = os.path.join(TEMP_AUDIO_DIR, downloaded_file_name)
                final_downloaded_ext = downloaded_file_name.split('.')[-1]

                # Rename to unique name before sending
                response_file_path = os.path.join(TEMP_AUDIO_DIR, f"{unique_temp_filename_base}.{final_downloaded_ext}")
                os.rename(actual_downloaded_path, response_file_path)
                logger.info(f"Renamed downloaded audio from {actual_downloaded_path} to {response_file_path}")

                final_song_title = current_song_title
                final_song_artist = current_song_artist
                downloaded_successfully = True
                break # Exit loop on first successful download

            except yt_dlp.utils.DownloadError as de_inner:
                logger.warning(f"yt-dlp DownloadError for '{current_song_title}' (ID: {video_id}): {de_inner}")
                last_download_error_message = str(de_inner)
                # (Cleanup of partial files for this video_id already handled at the start of the loop)
                continue # Try next song
            except Exception as e_inner:
                logger.error(f"Unexpected error during download attempt for '{current_song_title}' (ID: {video_id}): {e_inner}", exc_info=True)
                last_download_error_message = str(e_inner)
                continue # Try next song

        if not downloaded_successfully:
            logger.error(f"All download attempts failed for query '{search_query}'. Last error: {last_download_error_message}")
            # Provide more specific feedback if known patterns are in the error
            if "Video unavailable" in last_download_error_message:
                error_to_report = f"Video unavailable: {last_download_error_message}. This might be due to regional restrictions or the video being removed/private."
            elif "Sign in to confirm" in last_download_error_message:
                error_to_report = f"Authentication error: {last_download_error_message}. Cookies might be invalid or required for this content."
            else:
                error_to_report = f"Failed to download audio after trying {len(search_results)} result(s). Last error: {last_download_error_message}"
            return jsonify({"error": error_to_report}), 500

        # Proceed with sending the successfully downloaded file
        logger.info(f"Sending file: {response_file_path} as '{final_song_artist} - {final_song_title}.{final_downloaded_ext}'")
        
        mimetype_map = {
            'mp3': 'audio/mpeg',
            'm4a': 'audio/mp4', # or audio/m4a
            'webm': 'audio/webm',
            'opus': 'audio/opus',
            # Add more as needed
        }
        mimetype = mimetype_map.get(final_downloaded_ext.lower(), 'application/octet-stream')


        # Ensure response is created within the try block so cleanup_file is registered
        response = send_file(
            response_file_path,
            as_attachment=True,
            download_name=f"{final_song_artist} - {final_song_title}.{final_downloaded_ext}",
            mimetype=mimetype
        )

        @response.call_on_close
        def cleanup_file():
            try:
                if os.path.exists(response_file_path):
                    os.remove(response_file_path)
                    logger.info(f"Cleaned up temporary file: {response_file_path}")
                # else: # No need to warn if file is already gone or never existed here
                #    logger.warning(f"Cleanup: File not found to delete: {response_file_path}")
            except Exception as e_cleanup:
                logger.error(f"Error cleaning up file {response_file_path}: {e_cleanup}", exc_info=True)
        
        return response

    except Exception as e_outer: # Catch-all for errors outside the download loop (e.g., YTMusic API search)
        logger.error(f"An unexpected error occurred in search_and_download: {e_outer}", exc_info=True)
        return jsonify({"error": f"An internal server error occurred: {str(e_outer)}"}), 500


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001)) # Default to 5001 if PORT not set
    # For Render, debug=False is typical. For local dev, debug=True can be helpful.
    app.run(host='0.0.0.0', port=port, debug=False)
