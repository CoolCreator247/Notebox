import os
import uuid
import requests
from flask import Flask, request, jsonify, render_template, redirect, url_for, flash
from litedb import LiteDB

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your_default_secret_key')

# Hardcoded API Keys (Consider moving to .env)
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-62145360afc9429fa53e3179ef19ec14")
DEEPGRAM_API_KEY = os.environ.get("DEEPGRAM_API_KEY", "f06448398f23e5b0444bd76b542384cb9a6394a8")

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# LiteDB Initialization
DB_PATH = os.path.join(os.path.dirname(__file__), 'notes.db')
db = LiteDB(DB_PATH)
notes_collection = db.get_collection('notes')
counters_collection = db.get_collection('counters')

# Helper to get next note ID
def get_next_note_id():
    counter_doc = counters_collection.find_one({'_id': 'note_id_counter'})
    if counter_doc:
        next_id = counter_doc['value'] + 1
        counters_collection.update_one({'_id': 'note_id_counter'}, {'value': next_id})
        return next_id
    else:
        counters_collection.insert_one({'_id': 'note_id_counter', 'value': 1})
        return 1

# Unified Processing Pipeline
def process_audio_file(audio_file_storage, original_filename):
    _, ext = os.path.splitext(original_filename)
    unique_filename = f"{uuid.uuid4()}{ext}"
    audio_filepath = os.path.join(UPLOAD_FOLDER, unique_filename)
    audio_file_storage.save(audio_filepath)

    transcript_text = "Transcription failed."
    summary_text = "Summarization failed."
    processed_successfully = True # Overall success of both Deepgram and DeepSeek

    # Deepgram Integration
    try:
        with open(audio_filepath, 'rb') as audio:
            headers = {
                "Authorization": f"Token {DEEPGRAM_API_KEY}",
                "Content-Type": f"audio/{ext.strip('.')}" # Use actual extension
            }
            params = {"model": "nova-2", "punctuate": "true"}
            response = requests.post("https://api.deepgram.com/v1/listen", headers=headers, params=params, data=audio)
            response.raise_for_status()
            deepgram_data = response.json()
            transcript_text = deepgram_data.get('results', {}).get('channels', [{}])[0].get('alternatives', [{}])[0].get('transcript', 'Transcript not found in response.')
            if transcript_text == 'Transcript not found in response.':
                 processed_successfully = False # Transcription technically failed if no text
    except requests.exceptions.RequestException as e:
        transcript_text = f"Deepgram API Error: {str(e)}"
        processed_successfully = False
    except (KeyError, IndexError, TypeError) as e:
        transcript_text = f"Deepgram response parsing error: {str(e)}"
        processed_successfully = False

    # DeepSeek Integration (Summarization)
    if processed_successfully and transcript_text != "Transcription failed." and not transcript_text.startswith("Deepgram"):
        try:
            headers = {
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": "You are an AI assistant that summarizes text into bullet points, key ideas, and generates Q&A pairs."},
                    {"role": "user", "content": f"Please summarize the following transcript into bullet points, extract key ideas, and create a few question-answer pairs based on it: {transcript_text}"}
                ]
            }
            response = requests.post("https://api.deepseek.com/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
            deepseek_data = response.json()
            summary_text = deepseek_data.get('choices', [{}])[0].get('message', {}).get('content', 'Summary not found in response.')
            if summary_text == 'Summary not found in response.':
                processed_successfully = False # Summarization technically failed if no text
        except requests.exceptions.RequestException as e:
            summary_text = f"DeepSeek API Error: {str(e)}"
            processed_successfully = False
        except (KeyError, IndexError, TypeError) as e:
            summary_text = f"DeepSeek response parsing error: {str(e)}"
            processed_successfully = False
    else:
        if processed_successfully: # Only skip if transcription was successful but empty
             summary_text = "Summarization skipped due to empty or failed transcript."
        # If transcription failed, processed_successfully is already False. summary_text remains "Summarization failed."

    return {
        "original_filename": original_filename,
        "saved_filepath": audio_filepath,
        "transcript_data": {"transcript": transcript_text},
        "summary_data": {"summary": summary_text},
        "processed_successfully": processed_successfully, # Indicates if Deepgram AND Deepseek were successful
        "transcription_status_message": transcript_text, # More detailed status
        "summarization_status_message": summary_text    # More detailed status
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
        db_note_data = {
            "id": note_id,
            "filename": processed_data["original_filename"],
            "saved_filepath": processed_data["saved_filepath"],
            "transcript_data": processed_data["transcript_data"],
            "summary_data": processed_data["summary_data"],
            "transcription_status": processed_data["transcription_status_message"],
            "summarization_status": processed_data["summarization_status_message"]
        }
        notes_collection.insert_one(db_note_data)
        
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
            db_note_data = {
                "id": note_id,
                "filename": processed_data["original_filename"],
                "saved_filepath": processed_data["saved_filepath"],
                "transcript_data": processed_data["transcript_data"],
                "summary_data": processed_data["summary_data"],
                "transcription_status": processed_data["transcription_status_message"], # For display/debug
                "summarization_status": processed_data["summarization_status_message"]  # For display/debug
            }
            notes_collection.insert_one(db_note_data)
            
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
    all_notes = list(notes_collection.find({})) # Convert cursor to list for template
    # LiteDB might add its own `_id`. We are using custom `id`. Ensure templates use `note.id`.
    # If `_id` is an ObjectId, it's not directly JSON serializable for `jsonify` if we were to pass it.
    # For templates, it's fine.
    return render_template("notes_list.html", notes=all_notes)

# Web UI Display Specific Note
@app.route("/notes/<int:id>")
def display_note_page(id):
    note = notes_collection.find_one({'id': id})
    if note:
        return render_template("note_display.html", note=note)
    flash(f"Note with ID {id} not found.", "danger")
    return redirect(url_for('list_notes_page'))


# API: Get All Notes Endpoint
@app.route("/api/notes", methods=["GET"])
def get_all_notes_api():
    all_notes = list(notes_collection.find({}))
    # Remove LiteDB's internal _id if present and not desired in API response
    for note in all_notes:
        if '_id' in note: # LiteDB adds _id as string representation of ObjectId by default
            del note['_id'] # Or transform it if needed
    return jsonify(all_notes)

# API: Get Specific Note Endpoint
@app.route("/api/notes/<int:id>", methods=["GET"])
def get_note_api(id):
    note = notes_collection.find_one({'id': id})
    if note:
        if '_id' in note:
            del note['_id']
        return jsonify(note)
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
    for note in notes_collection.find({}):
        transcript = note.get("transcript_data", {}).get("transcript", "").lower()
        summary = note.get("summary_data", {}).get("summary", "").lower()
        if query in transcript or query in summary:
            if '_id' in note: # Clean up for API response
                del note['_id']
            results.append(note)
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

    note = notes_collection.find_one({'id': note_id})
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
                {"role": "system", "content": "You are an AI assistant. Answer the user's question based on the provided text."},
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
