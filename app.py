"""
Software Manager - Backend
============================
Einfache REST-API (Flask + SQLite), die als zentraler Server dient.
Alle Clients (PCs deiner Nutzer) greifen über das Internet auf diese
API zu -> dadurch sind Änderungen, die der Owner macht, für alle live.

Starten (lokal zum Testen):
    pip install -r requirements.txt
    python app.py
    -> läuft dann auf http://localhost:5000

Für den echten Einsatz musst du dieses Backend auf einen Server im
Internet legen (z.B. Render.com, PythonAnywhere, Railway - siehe
ANLEITUNG.md). Danach trägst du die Server-URL im Client
(client/config.py) ein.
"""

from flask import Flask, request, jsonify, g
import sqlite3
import os
import datetime

OWNER_PASSWORT = "julian2014hartwig"   # <- dein Owner-Passwort
DB_PFAD = os.path.join(os.path.dirname(__file__), "daten.db")
MAX_TABS_PRO_GERAET = 6

app = Flask(__name__)


# ---------------------------------------------------------------
# Datenbank
# ---------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PFAD)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PFAD)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device TEXT NOT NULL,        -- 'PC' oder 'Anzeige'
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS software (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            code TEXT NOT NULL,
            has_update INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY(category_id) REFERENCES categories(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS purchase_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device TEXT NOT NULL,        -- 'PC' oder 'Anzeige'
            name TEXT NOT NULL,
            price TEXT NOT NULL,
            image_base64 TEXT,
            created_at TEXT NOT NULL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS logins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------
def owner_erlaubt(req):
    return req.headers.get("X-Owner-Passwort") == OWNER_PASSWORT


def jetzt():
    return datetime.datetime.utcnow().isoformat()


# ---------------------------------------------------------------
# Login (nur Name/Email speichern, keine echte Authentifizierung)
# ---------------------------------------------------------------
@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    if not name or not email:
        return jsonify({"error": "Name und Email erforderlich"}), 400
    db = get_db()
    db.execute("INSERT INTO logins (name, email, created_at) VALUES (?,?,?)",
               (name, email, jetzt()))
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------
# Owner-Login prüfen
# ---------------------------------------------------------------
@app.route("/api/owner/auth", methods=["POST"])
def owner_auth():
    data = request.get_json(force=True)
    pw = data.get("password", "")
    if pw == OWNER_PASSWORT:
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 401


# ---------------------------------------------------------------
# Status für das Dashboard (großer Banner oben)
# ---------------------------------------------------------------
@app.route("/api/status", methods=["GET"])
def status():
    db = get_db()
    def hat_update(device):
        row = db.execute("""
            SELECT COUNT(*) as anzahl FROM software s
            JOIN categories c ON c.id = s.category_id
            WHERE c.device=? AND s.has_update=1
        """, (device,)).fetchone()
        return row["anzahl"] > 0

    return jsonify({
        "PC": hat_update("PC"),
        "Anzeige": hat_update("Anzeige"),
    })


# ---------------------------------------------------------------
# Kategorien (Tabs), max. 6 pro Gerät
# ---------------------------------------------------------------
@app.route("/api/categories", methods=["GET"])
def get_categories():
    device = request.args.get("device")
    db = get_db()
    rows = db.execute(
        "SELECT * FROM categories WHERE device=? ORDER BY sort_order ASC",
        (device,)
    ).fetchall()
    ergebnis = []
    for r in rows:
        hat_update = db.execute("""
            SELECT COUNT(*) as anzahl FROM software
            WHERE category_id=? AND has_update=1
        """, (r["id"],)).fetchone()["anzahl"] > 0
        ergebnis.append({
            "id": r["id"], "device": r["device"], "name": r["name"],
            "sort_order": r["sort_order"], "has_update": hat_update
        })
    return jsonify(ergebnis)


@app.route("/api/categories", methods=["POST"])
def create_category():
    if not owner_erlaubt(request):
        return jsonify({"error": "nicht erlaubt"}), 403
    data = request.get_json(force=True)
    device = data.get("device")
    name = (data.get("name") or "").strip()
    if device not in ("PC", "Anzeige") or not name:
        return jsonify({"error": "ungültige Daten"}), 400
    db = get_db()
    anzahl = db.execute(
        "SELECT COUNT(*) as n FROM categories WHERE device=?", (device,)
    ).fetchone()["n"]
    if anzahl >= MAX_TABS_PRO_GERAET:
        return jsonify({"error": f"Maximal {MAX_TABS_PRO_GERAET} Kategorien pro Gerät"}), 400
    db.execute(
        "INSERT INTO categories (device, name, sort_order) VALUES (?,?,?)",
        (device, name, anzahl)
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/categories/<int:cat_id>", methods=["DELETE"])
def delete_category(cat_id):
    if not owner_erlaubt(request):
        return jsonify({"error": "nicht erlaubt"}), 403
    db = get_db()
    db.execute("DELETE FROM software WHERE category_id=?", (cat_id,))
    db.execute("DELETE FROM categories WHERE id=?", (cat_id,))
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------
# Software (Code-Einträge)
# ---------------------------------------------------------------
@app.route("/api/software", methods=["GET"])
def get_software_list():
    category_id = request.args.get("category_id")
    db = get_db()
    rows = db.execute("""
        SELECT id, name, has_update, created_at FROM software
        WHERE category_id=? ORDER BY created_at DESC
    """, (category_id,)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/software/<int:sw_id>", methods=["GET"])
def get_software_detail(sw_id):
    db = get_db()
    row = db.execute("SELECT * FROM software WHERE id=?", (sw_id,)).fetchone()
    if not row:
        return jsonify({"error": "nicht gefunden"}), 404
    # Beim Ansehen gilt das Update als "gesehen"
    db.execute("UPDATE software SET has_update=0 WHERE id=?", (sw_id,))
    db.commit()
    return jsonify(dict(row))


@app.route("/api/software", methods=["POST"])
def create_software():
    if not owner_erlaubt(request):
        return jsonify({"error": "nicht erlaubt"}), 403
    data = request.get_json(force=True)
    category_id = data.get("category_id")
    name = (data.get("name") or "").strip()
    code = data.get("code") or ""
    if not category_id or not name:
        return jsonify({"error": "ungültige Daten"}), 400
    db = get_db()
    db.execute("""
        INSERT INTO software (category_id, name, code, has_update, created_at)
        VALUES (?,?,?,1,?)
    """, (category_id, name, code, jetzt()))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/software/<int:sw_id>", methods=["DELETE"])
def delete_software(sw_id):
    if not owner_erlaubt(request):
        return jsonify({"error": "nicht erlaubt"}), 403
    db = get_db()
    db.execute("DELETE FROM software WHERE id=?", (sw_id,))
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------
# Kaufartikel
# ---------------------------------------------------------------
@app.route("/api/purchase", methods=["GET"])
def get_purchase_items():
    device = request.args.get("device")
    limit = request.args.get("limit", type=int)
    db = get_db()
    if device:
        q = "SELECT * FROM purchase_items WHERE device=? ORDER BY created_at DESC"
        params = (device,)
    else:
        q = "SELECT * FROM purchase_items ORDER BY created_at DESC"
        params = ()
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = db.execute(q, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/purchase", methods=["POST"])
def create_purchase_item():
    if not owner_erlaubt(request):
        return jsonify({"error": "nicht erlaubt"}), 403
    data = request.get_json(force=True)
    device = data.get("device")
    name = (data.get("name") or "").strip()
    price = (data.get("price") or "").strip()
    image_base64 = data.get("image_base64", "")
    if device not in ("PC", "Anzeige") or not name or not price:
        return jsonify({"error": "ungültige Daten"}), 400
    db = get_db()
    db.execute("""
        INSERT INTO purchase_items (device, name, price, image_base64, created_at)
        VALUES (?,?,?,?,?)
    """, (device, name, price, image_base64, jetzt()))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/purchase/<int:item_id>", methods=["DELETE"])
def delete_purchase_item(item_id):
    if not owner_erlaubt(request):
        return jsonify({"error": "nicht erlaubt"}), 403
    db = get_db()
    db.execute("DELETE FROM purchase_items WHERE id=?", (item_id,))
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------
# Datenbank wird auch beim Import initialisiert (wichtig für gunicorn
# auf Render.com & Co., wo nicht "__main__" ausgeführt wird)
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
