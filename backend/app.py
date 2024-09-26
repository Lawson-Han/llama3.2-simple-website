from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit
from database import get_db_connection, init_db
import requests
import json
from datetime import datetime, timezone

app = Flask(__name__)
CORS(
    app,
    resources={r"/*": {"origins": ["http://localhost:3000", "http://127.0.0.1:3000"]}},
)

socketio = SocketIO(
    app, cors_allowed_origins=["http://localhost:3000", "http://127.0.0.1:3000"]
)

OLLAMA_API_URL = "http://localhost:11434/api/chat"


@app.route("/")
def home():
    return "Welcome to the ChatGPT-like app."


@app.route("/get_sessions", methods=["GET"])
def get_sessions():
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM sessions")
        sessions = cursor.fetchall()
    conn.close()
    return jsonify({"sessions": sessions})


@app.route("/add_session", methods=["POST"])
def start_session():
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            created_at = datetime.now(timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )  # Store as UTC
            cursor.execute(
                "INSERT INTO sessions (title, created_at) VALUES (%s, %s)",
                ("Session", created_at),
            )
            session_id = cursor.lastrowid

            # Fetch the newly created session to return
            cursor.execute("SELECT * FROM sessions WHERE id = %s", (session_id,))
            new_session = cursor.fetchone()
    except Exception as e:
        print(f"Error creating session: {e}")
        return jsonify({"error": "Failed to create session"}), 500
    finally:
        conn.commit()
        conn.close()

    print(f"New session created with ID: {session_id}")
    return jsonify({"session": new_session}), 201


@socketio.on("send_message")
def handle_send_message(data):
    session_id = data["session_id"]
    text = data["text"]
    conn = get_db_connection()

    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO messages (session_id, role, text) VALUES (%s, %s, %s)",
                (session_id, "user", text),
            )
        conn.commit()

        payload = {
            "model": "llama3.2",
            "stream": True,
            "messages": [{"role": "user", "content": text}],
        }
        headers = {"Content-Type": "application/json"}

        response = requests.post(
            OLLAMA_API_URL, data=json.dumps(payload), headers=headers, stream=True
        )
        response.raise_for_status()

        for line in response.iter_lines():
            if line:
                json_data = json.loads(line.decode("utf-8"))
                if "message" in json_data and "content" in json_data["message"]:
                    emit(
                        "receive_message",
                        {"role": "assistant", "text": json_data["message"]["content"]},
                        to=request.sid,
                    )

                    if json_data.get("done", False):
                        emit(
                        "receive_message",
                        {"role": "assistant", "done": True},
                        to=request.sid,
                    )
                        print("Received 'done' flag, stopping the stream.")
                        break

    except requests.exceptions.RequestException as e:
        print(f"Error during request to LLaMA API: {e}")
        emit(
            "receive_message",
            {"role": "assistant", "text": "Error with LLaMA API"},
            to=request.sid,
        )
    finally:
        conn.close()


@app.route("/sessions/<int:session_id>/messages", methods=["GET"])
def get_messages(session_id):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM messages WHERE session_id = %s", (session_id,))
        messages = cursor.fetchall()
    conn.close()
    return jsonify({"messages": messages})


if __name__ == "__main__":
    with app.app_context():
        init_db()
    socketio.run(app, debug=True)
