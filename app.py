"""SCU course prerequisite map: Flask server.

Run:  .venv/bin/python app.py   then open http://127.0.0.1:8000
"""

from flask import Flask, jsonify, redirect, request, send_from_directory, url_for

from bulletin import BulletinIndex

app = Flask(__name__, static_folder="static")
index = BulletinIndex()


@app.get("/")
def home():
    return send_from_directory(app.static_folder, "home.html")


@app.get("/map")
def map_page():
    if not request.args.get("course", "").strip():
        return redirect(url_for("home"))
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/status")
def status():
    return jsonify({**index.status, "courses": len(index.courses)})


@app.get("/api/search")
def search():
    return jsonify(index.search(request.args.get("q", "")))


@app.get("/api/map")
def course_map():
    raw = request.args.get("course", "").strip()
    code = BulletinIndex.normalize(raw)
    if not code:
        return jsonify({"error": f"“{raw}” isn't a course code. Type a course like OMIS 107.",
                        "not_found": True}), 400
    if index.status["state"] != "ready":
        return jsonify({"error": "The bulletin is still being read.",
                        "status": index.status}), 503
    major_id = request.args.get("major", "").strip() or None
    major = index.majors.get(major_id) if major_id else None
    if major_id and not major:
        return jsonify({"error": "That major isn't in the list. Pick one from the menu.",
                        "not_found": True}), 400
    if code not in index.courses and not index.unlocks.get(code):
        return jsonify({"error": f"{code} isn't in the SCU Undergraduate Bulletin. "
                                 "Check the subject and number and try again.",
                        "not_found": True}), 404
    if major and code not in major["courses"]:
        return jsonify({"error": f"{code} isn't one of the courses listed in the "
                                 f"requirements for the {major['name']} major.",
                        "not_in_major": True}), 404
    up = min(max(int(request.args.get("up", 6)), 1), 10)
    down = min(max(int(request.args.get("down", 1)), 1), 4)
    data = index.map_for(code, up_depth=up, down_depth=down, major=major_id)
    if major:
        data["major"] = {"id": major["id"], "name": major["name"], "url": major["url"]}
    return jsonify(data)


@app.get("/api/majors")
def majors():
    return jsonify(index.major_list())


if __name__ == "__main__":
    index.build()  # loads the saved index, or reads the bulletin in the background
    app.run(debug=False, port=8001)  # 5000 is taken by AirPlay on macOS
