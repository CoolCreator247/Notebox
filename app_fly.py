import os
import uuid
import requests
from flask import Flask, request, jsonify, render_template, redirect, url_for, flash
from litedb import DiskDatabase

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your_default_secret_key')

# API Key Definitions
# Note: For production environments, consider using a dedicated secret management service
# (e.g., HashiCorp Vault, AWS Secrets Manager, Google Secret Manager)
# instead of relying solely on environment variables.
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-62145360afc9429fa53e3179ef19ec14")
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "f06448398f23e5b0444bd76b542384cb9a6394a8")

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# LiteDB Initialization
DB_PATH = os.path.join(os.path.dirname(__file__), 'notes.db')
db = DiskDatabase(DB_PATH)
notes_collection = db.get_collection('notes')
counters_collection = db.get_collection('counters')

# Helper to get next note ID
def get_next_note_id():
    try:
        counter_doc = counters_collection.find_one({'_id': 'note_id_counter'})
        if counter_doc:
            next_id = counter_doc['value'] + 1
            counters_collection.update_one({'_id': 'note_id_counter'}, {'value': next_id})
            return next_id
        else:
            counters_collection.insert_one({'_id': 'note_id_counter', 'value': 1})
            return 1
    except Exception as e:
        print(f"Database error in get_next_note_id: {e}") # Using print for logging
        return None

def transcribe_audio(audio_filepath, audio_ext, api_key):
    """
    Transcribes the audio file at the given path using Deepgram API.
    Returns a dictionary with {"transcript": transcript_text, "error": error_message}.
    """
    try:
        with open(audio_filepath, 'rb') as audio:
            headers = {
                "Authorization": f"Token {api_key}",
                "Content-Type": f"audio/{audio_ext}"
            }
            params = {"model": "nova-2", "punctuate": "true"}
            response = requests.post("https://api.deepgram.com/v1/listen", headers=headers, params=params, data=audio)
            response.raise_for_status()
            deepgram_data = response.json()
            transcript_text = deepgram_data.get('results', {}).get('channels', [{}])[0].get('alternatives', [{}])[0].get('transcript')
            if transcript_text:
                return {"transcript": transcript_text, "error": None}
            else:
                return {"transcript": None, "error": "Transcript not found in Deepgram response."}
    except requests.exceptions.RequestException as e:
        return {"transcript": None, "error": f"Deepgram API Error: {str(e)}"}
    except (KeyError, IndexError, TypeError) as e:
        return {"transcript": None, "error": f"Deepgram response parsing error: {str(e)}"}

def summarize_transcript(transcript_text, api_key):
    """
    Summarizes the given transcript text using DeepSeek API.
    Returns a dictionary with {"summary": summary_text, "error": error_message}.
    """
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "You are a highly skilled AI assistant. Your task is to process the provided transcript and generate a comprehensive analysis. This analysis should include:\n1. A concise overall summary of the transcript.\n2. Key ideas presented, ideally as a bulleted list.\n3. A few relevant Question & Answer pairs based *only* on the information present in the transcript. Ensure the questions are insightful and the answers are extracted directly from the text."},
                {"role": "user", "content": f"Please summarize the following transcript into bullet points, extract key ideas, and create a few question-answer pairs based on it: {transcript_text}"}
            ]
        }
        response = requests.post("https://api.deepseek.com/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        deepseek_data = response.json()
        summary_text = deepseek_data.get('choices', [{}])[0].get('message', {}).get('content')
        if summary_text:
            return {"summary": summary_text, "error": None}
        else:
            return {"summary": None, "error": "Summary not found in DeepSeek response."}
    except requests.exceptions.RequestException as e:
        return {"summary": None, "error": f"DeepSeek API Error: {str(e)}"}
    except (KeyError, IndexError, TypeError) as e:
        return {"summary": None, "error": f"DeepSeek response parsing error: {str(e)}"}

# Unified Processing Pipeline
def process_audio_file(audio_file_storage, original_filename):
    _, ext = os.path.splitext(original_filename)
    unique_filename = f"{uuid.uuid4()}{ext}"
    audio_filepath = os.path.join(UPLOAD_FOLDER, unique_filename)
    audio_file_storage.save(audio_filepath)

    transcription_result = transcribe_audio(audio_filepath, ext.strip('.'), DEEPGRAM_API_KEY)
    transcript_text = transcription_result["transcript"]
    transcription_status_message = transcription_result["error"] or "Transcription successful."

    summary_text = "Summarization skipped." # Default if transcription fails or is empty
    summarization_status_message = "Summarization skipped due to transcription issue or empty transcript."
    processed_successfully = False # Overall success
    summarization_result = {"summary": None, "error": None} # Ensure summarization_result is defined

    if transcript_text:
        processed_successfully = True # At least transcription worked
        summarization_result = summarize_transcript(transcript_text, DEEPSEEK_API_KEY)
        summary_text = summarization_result["summary"]
        summarization_status_message = summarization_result["error"] or "Summarization successful."
        if summarization_result["error"]:
            processed_successfully = False # Summarization failed, so overall not fully successful
    else: # Transcription failed
        transcript_text = "Transcription failed." # Ensure this is set for DB
        # transcription_status_message already holds the error from transcribe_audio

    # Adjust status messages if needed for clarity for DB / flash messages
    if transcription_result["error"]:
        transcription_status_message = transcription_result["error"] # Use specific error
        transcript_text = f"Transcription failed: {transcription_result['error']}"


    if not summary_text and not summarization_result.get("error") and transcript_text and not transcription_result.get("error"):
         # Handles case where summarization returns empty but no error, and transcription was fine.
         summarization_status_message = "Summary generation resulted in empty content."
         summary_text = "Summary generation resulted in empty content." # Ensure this is set for DB
    elif summarization_result.get("error"):
        summarization_status_message = summarization_result["error"] # Use specific error
        summary_text = f"Summarization failed: {summarization_result['error']}"

    final_processed_successfully = transcript_text is not None and \
                                   not transcription_result["error"] and \
                                   summary_text is not None and \
                                   not summarization_result.get("error") and \
                                   summary_text != "Summary generation resulted in empty content." # Explicitly check for this case


    return {
        "original_filename": original_filename,
        "saved_filepath": audio_filepath,
        "transcript_data": {"transcript": transcript_text or "Transcription failed or was empty."},
        "summary_data": {"summary": summary_text or "Summarization failed or was skipped."},
        "processed_successfully": final_processed_successfully,
        "transcription_status_message": transcription_status_message,
        "summarization_status_message": summarization_status_message
    }

# Main Route / Web UI Routes
@app.route("/")
def home_page():
    return render_template("index.html")

# API Upload Endpoint - remains for programmatic access
@app.route("/api/upload", methods=["POST"])
def api_upload():
    if 'audio_file' not in request.files:
        return jsonify({"error": "No audio file part"}), 400
    
    file_storage = request.files['audio_file']
    if file_storage.filename == '':
        return jsonify({"error": "No selected file"}), 400

    if file_storage:
        processed_data = process_audio_file(file_storage, file_storage.filename)
        
        note_id = get_next_note_id()
        if note_id is None:
            print("Error: Failed to get next note ID in api_upload.")
            return jsonify({"error": "Database error occurred while generating note ID"}), 500

        db_note_data = {
            "id": note_id,
            "filename": processed_data["original_filename"],
            "saved_filepath": processed_data["saved_filepath"],
            "transcript_data": processed_data["transcript_data"],
            "summary_data": processed_data["summary_data"],
            "transcription_status": processed_data["transcription_status_message"],
            "summarization_status": processed_data["summarization_status_message"]
        }
        try:
            notes_collection.insert_one(db_note_data)
        except Exception as e:
            print(f"Database insert error in api_upload: {e}")
            return jsonify({"error": "Database error occurred"}), 500
        
        # Remove LiteDB's _id before returning, if it's added automatically and not desired in API response
        # However, LiteDB typically returns the document as inserted. Let's assume db_note_data is fine.
        return jsonify(db_note_data), 201

# Web UI Upload Endpoint
@app.route("/upload", methods=["GET", "POST"])
def upload_page():
    if request.method == "GET":
        return render_template("upload_form.html")
    
    if request.method == "POST":
        if 'audio_file' not in request.files:
            flash("No audio file part", "danger")
            return render_template("upload_form.html", message="No audio file part", success=False)
        
        file_storage = request.files['audio_file']
        if file_storage.filename == '':
            flash("No selected file", "danger")
            return render_template("upload_form.html", message="No selected file", success=False)

        if file_storage:
            processed_data = process_audio_file(file_storage, file_storage.filename)
            
            note_id = get_next_note_id()
            if note_id is None:
                print("Error: Failed to get next note ID in upload_page.")
                flash("A critical error occurred while generating note ID. Please try again later.", "danger")
                return render_template("upload_form.html", message="Failed to generate note ID.", success=False)

            db_note_data = {
                "id": note_id,
                "filename": processed_data["original_filename"],
                "saved_filepath": processed_data["saved_filepath"],
                "transcript_data": processed_data["transcript_data"],
                "summary_data": processed_data["summary_data"],
                "transcription_status": processed_data["transcription_status_message"], # For display/debug
                "summarization_status": processed_data["summarization_status_message"]  # For display/debug
            }
            try:
                notes_collection.insert_one(db_note_data)
            except Exception as e:
                print(f"Database insert error in upload_page: {e}")
                flash("A critical error occurred while saving your note. Please try again later.", "danger")
                # It might be good to still pass processed_data if available and relevant for the template
                return render_template("upload_form.html", message="Failed to save note to database.", success=False, note_data=db_note_data if 'db_note_data' in locals() else None)

            # Use processed_successfully from the unified function
            if processed_data["processed_successfully"]:
                flash("File processed successfully!", "success")
                return render_template("upload_form.html", message="File processed successfully!", success=True, note_data=db_note_data)
            else:
                error_message = f"File processed with errors. Transcription: {processed_data['transcription_status_message']}. Summarization: {processed_data['summarization_status_message']}"
                flash(error_message, "danger")
                return render_template("upload_form.html", message=error_message, success=False, note_data=db_note_data)

# Web UI List All Notes
@app.route("/notes")
def list_notes_page():
    all_notes = []
    try:
        all_notes = list(notes_collection.find({})) # Convert cursor to list for template
    except Exception as e:
        print(f"Database find error in list_notes_page: {e}")
        flash("Error retrieving notes from database. Please try again later.", "danger")
    # LiteDB might add its own `_id`. We are using custom `id`. Ensure templates use `note.id`.
    # If `_id` is an ObjectId, it's not directly JSON serializable for `jsonify` if we were to pass it.
    # For templates, it's fine.
    return render_template("notes_list.html", notes=all_notes)

# Web UI Display Specific Note
@app.route("/notes/<int:id>")
def display_note_page(id):
    note = None
    try:
        note = notes_collection.find_one({'id': id})
    except Exception as e:
        print(f"Database find_one error in display_note_page for id {id}: {e}")
        flash(f"Error retrieving note {id} from database. Please try again later.", "danger")
        return redirect(url_for('list_notes_page'))

    if note:
        return render_template("note_display.html", note=note)
    else: # Note not found (not a DB error, but handled here)
        flash(f"Note with ID {id} not found.", "danger")
        return redirect(url_for('list_notes_page'))


# API: Get All Notes Endpoint
@app.route("/api/notes", methods=["GET"])
def get_all_notes_api():
    all_notes = []
    try:
        all_notes = list(notes_collection.find({}))
    except Exception as e:
        print(f"Database find error in get_all_notes_api: {e}")
        # For an API, we might return a 500 error or an empty list with a warning
        # Depending on desired API behavior. For now, returning empty list on error.
        return jsonify({"error": "Could not retrieve notes due to a database error."}), 500

    # Remove LiteDB's internal _id if present and not desired in API response
    for note in all_notes:
        if '_id' in note: # LiteDB adds _id as string representation of ObjectId by default
            del note['_id'] # Or transform it if needed
    return jsonify(all_notes)

# API: Get Specific Note Endpoint
@app.route("/api/notes/<int:id>", methods=["GET"])
def get_note_api(id):
    note = None
    try:
        note = notes_collection.find_one({'id': id})
    except Exception as e:
        print(f"Database find_one error in get_note_api for id {id}: {e}")
        return jsonify({"error": "Database error occurred while retrieving note."}), 500

    if note:
        if '_id' in note:
            del note['_id']
        return jsonify(note)
    else:
        return jsonify({"error": "Note not found"}), 404

# API: Search Notes Endpoint (remains API only for now)
@app.route("/api/notes/search", methods=["GET"])
def search_notes_api():
    query = request.args.get("q", "").lower()
    if not query:
        return jsonify({"error": "Search query cannot be empty"}), 400
    
    # Basic text search in transcript and summary. LiteDB doesn't have complex text indexing.
    # For larger datasets, a more robust search solution would be needed.
    results = []
    try:
        for note in notes_collection.find({}):
            transcript = note.get("transcript_data", {}).get("transcript", "").lower()
            summary = note.get("summary_data", {}).get("summary", "").lower()
            if query in transcript or query in summary:
                if '_id' in note: # Clean up for API response
                    del note['_id']
                results.append(note)
    except Exception as e:
        print(f"Database find error during search in search_notes_api: {e}")
        return jsonify({"error": "Database error occurred during search."}), 500
    return jsonify(results)

# API: Q&A Endpoint
@app.route("/api/qa_note", methods=["POST"])
def qa_page_api_placeholder():
    data = request.get_json()
    if not data or "note_id" not in data or "question" not in data:
        return jsonify({"error": "Missing note_id or question in request body"}), 400

    try:
        note_id = int(data["note_id"])
    except ValueError:
        return jsonify({"error": "Invalid note_id format, must be an integer"}), 400
        
    question = data["question"]

    note = None
    try:
        note = notes_collection.find_one({'id': note_id})
    except Exception as e:
        print(f"Database find_one error in qa_page_api_placeholder for id {note_id}: {e}")
        return jsonify({"error": "Database error occurred when retrieving note for Q&A."}), 500

    if not note:
        return jsonify({"error": f"Note with id {note_id} not found"}), 404

    context_text = note.get("transcript_data", {}).get("transcript", "")
    # Check if transcript is usable for Q&A
    if not context_text or \
       context_text.startswith("Transcription failed") or \
       context_text.startswith("Deepgram API Error") or \
       context_text == "Transcript not found in response.":
        return jsonify({"error": "Cannot perform Q&A due to missing, failed, or empty transcript"}), 400

    answer = "Q&A processing failed."
    try:
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": "You are an AI assistant specializing in answering questions based *strictly* on the provided text. Do not infer information or use external knowledge. If the answer is not found in the text, clearly state that the information is not available in the provided context."},
                {"role": "user", "content": f"Based on the following text: \"{context_text}\", please answer this question: \"{question}\""}
            ]
        }
        response = requests.post("https://api.deepseek.com/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        deepseek_data = response.json()
        answer = deepseek_data.get('choices', [{}])[0].get('message', {}).get('content', 'Answer not found in response.')
    except requests.exceptions.RequestException as e:
        answer = f"DeepSeek API Error during Q&A: {str(e)}"
    except (KeyError, IndexError, TypeError) as e:
        answer = f"DeepSeek Q&A response parsing error: {str(e)}"

    return jsonify({"note_id": note_id, "question": question, "answer": answer})

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
