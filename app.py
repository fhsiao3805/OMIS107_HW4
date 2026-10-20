"""SCU course prerequisite map: Flask server.

Run:  .venv/bin/python app.py   then open http://127.0.0.1:8000
"""

from flask import Flask, jsonify, request, send_from_directory

from bulletin import BulletinIndex

app = Flask(__name__, static_folder="static")
index = BulletinIndex()


@app.get("/")
def home():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/status")
def status():
    return jsonify({**index.status, "courses": len(index.courses)})


@app.get("/api/search")
def search():
    return jsonify(index.search(request.args.get("q", "")))


@app.get("/api/map")
def course_map():
    code = BulletinIndex.normalize(request.args.get("course", ""))
    if not code:
        return jsonify({"error": "Type a course like OMIS 107."}), 400
    if index.status["state"] != "ready":
        return jsonify({"error": "The bulletin is still being read.",
                        "status": index.status}), 503
    up = min(max(int(request.args.get("up", 6)), 1), 10)
    down = min(max(int(request.args.get("down", 1)), 1), 4)
    data = index.map_for(code, up_depth=up, down_depth=down)
    if not data["found"] and not data["unlocks"]:
        data["error"] = f"{code} isn't in the {index.status['source']} course listings."
    return jsonify(data)


if __name__ == "__main__":
    index.build()  # loads the saved index, or reads the bulletin in the background
    app.run(debug=False, port=8001)  # 5000 is taken by AirPlay on macOS
