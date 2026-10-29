"""TCC Student Parliament Election.

One program that:
  * keeps the voters, candidates and votes in a local SQLite database,
  * opens the voting screen (students enter their admission/index number, see their name and class,
    click a candidate's photo and confirm),
  * serves the admin panel at http://<this-computer>:8765/admin (also reachable from other computers on the school network).

Votes are secret: the database records THAT a student voted, never WHO they voted for.
Data is stored in the folder "TCC-Election-Data" next to the program.
"""
import base64, csv, hashlib, hmac, io, json, os, secrets, socket, sqlite3, sys, threading, time, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

PORT = int(os.environ.get("TCC_ELECTION_PORT", "8765"))
BASE = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
WEB = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "web")
DATA = os.environ.get("TCC_ELECTION_DATA") or os.path.join(BASE, "TCC-Election-Data")
PHOTOS = os.path.join(DATA, "photos")
os.makedirs(PHOTOS, exist_ok=True)
DB_PATH = os.path.join(DATA, "election.db")
LOCK = threading.Lock()
SESSIONS = {}  # admin session token -> expiry


def db():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS voters (id TEXT PRIMARY KEY, name TEXT, class TEXT, voted INTEGER DEFAULT 0, voted_at TEXT);
        CREATE TABLE IF NOT EXISTS candidates (id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT, name TEXT, class TEXT, photo TEXT);
        CREATE TABLE IF NOT EXISTS votes (id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER);
        """)


def setting(k, default=None):
    with db() as c:
        r = c.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    return r["v"] if r else default


def set_setting(k, v):
    with db() as c:
        c.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


def norm_id(v):
    s = str(v if v is not None else "").strip().replace(" ", "").upper()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s.lstrip("0") or s


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200000).hex()


def check_pw(pw, stored):
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_pw(pw, salt), stored)


# ---------- Student list import (Excel .xlsx or .csv) ----------
NAME_RE = ("name", "නම")
CLASS_RE = ("class", "grade", "පන්ති", "ශ්‍රේණි")
ID_RE = ("admission", "index", "adm", "reg", "ඇතුළත්", "ඇතුලත්", "විභාග අංක", "id")


def read_rows(filename, raw):
    if filename.lower().endswith(".csv"):
        text = raw.decode("utf-8-sig", errors="replace")
        return [r for r in csv.reader(io.StringIO(text))]
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    rows = []
    for ws in wb.worksheets:
        for r in ws.iter_rows(values_only=True):
            rows.append(["" if v is None else v for v in r])
    return rows


def detect(rows):
    def find(header, words, taken):
        for i, h in enumerate(header):
            t = str(h).strip().lower()
            if i not in taken and t and any(w in t for w in words):
                return i
        return None
    for hi, row in enumerate(rows[:20]):
        taken = set()
        idc = find(row, ID_RE, taken); taken.add(idc)
        name = find(row, NAME_RE, taken); taken.add(name)
        cls = find(row, CLASS_RE, taken)
        if idc is not None and name is not None:
            return hi, idc, name, cls
    return None


def import_students(filename, raw, replace):
    rows = read_rows(filename, raw)
    found = detect(rows)
    if not found:
        raise ValueError("Could not find the name and admission/index number columns. Make sure the file has a heading row such as: Admission No | Name | Class")
    hi, idc, namec, clsc = found
    added = skipped = 0
    seen = set()
    with LOCK, db() as c:
        if replace:
            c.execute("DELETE FROM voters WHERE voted=0")
        for r in rows[hi + 1:]:
            if len(r) <= max(idc, namec):
                continue
            vid, name = norm_id(r[idc]), str(r[namec]).strip()
            # skip repeated heading rows (files with one sheet/table per class)
            if not vid or not name or any(w in name.lower() for w in NAME_RE) or vid in seen:
                skipped += 1 if (vid or name) else 0
                continue
            seen.add(vid)
            cls = str(r[clsc]).strip() if clsc is not None and len(r) > clsc else ""
            c.execute("INSERT INTO voters(id,name,class) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, class=excluded.class", (vid, name, cls))
            added += 1
    return {"added": added, "skipped": skipped, "columns": {"id": rows[hi][idc], "name": rows[hi][namec], "class": rows[hi][clsc] if clsc is not None else None}}


# ---------- HTTP ----------
class H(BaseHTTPRequestHandler):
    server_version = "TCCElection/1.0"

    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 30 * 1024 * 1024:
            raise ValueError("File too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def is_admin(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "tccadmin" and SESSIONS.get(v, 0) > time.time():
                SESSIONS[v] = time.time() + 8 * 3600
                return True
        return False

    def file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                self.send(200, f.read(), ctype)
        except OSError:
            self.send(404, {"error": "Not found"})

    def do_GET(self):
        p = urlparse(self.path).path
        if p in ("/", "/vote"):
            return self.file(os.path.join(WEB, "vote.html"), "text/html; charset=utf-8")
        if p == "/admin":
            return self.file(os.path.join(WEB, "admin.html"), "text/html; charset=utf-8")
        if p in ("/logo.webp", "/style.css"):
            return self.file(os.path.join(WEB, p[1:]), "image/webp" if p.endswith("webp") else "text/css")
        if p.startswith("/photos/"):
            name = os.path.basename(p)
            ext = name.rsplit(".", 1)[-1].lower()
            return self.file(os.path.join(PHOTOS, name), {"png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg"))
        if p == "/api/state":
            with db() as c:
                cands = [dict(r) for r in c.execute("SELECT id, number, name, class, photo FROM candidates ORDER BY CAST(number AS INTEGER), number")]
            return self.send(200, {"open": setting("open") == "1", "title": setting("title", "Student Parliament Election"), "candidates": cands, "setup": setting("admin_pw") is None})
        if p == "/api/admin/results":
            if not self.is_admin():
                return self.send(401, {"error": "Please sign in"})
            with db() as c:
                res = [dict(r) for r in c.execute("SELECT c.id, c.number, c.name, c.class, c.photo, COUNT(v.id) AS votes FROM candidates c LEFT JOIN votes v ON v.candidate_id=c.id GROUP BY c.id ORDER BY votes DESC, CAST(c.number AS INTEGER)")]
                tot = c.execute("SELECT COUNT(*) n, SUM(voted) v FROM voters").fetchone()
                by_class = [dict(r) for r in c.execute("SELECT class, COUNT(*) total, SUM(voted) voted FROM voters GROUP BY class ORDER BY class")]
            return self.send(200, {"candidates": res, "voters": tot["n"], "voted": tot["v"] or 0, "byClass": by_class, "open": setting("open") == "1", "title": setting("title", "Student Parliament Election"), "addresses": addresses()})
        if p == "/api/admin/voters":
            if not self.is_admin():
                return self.send(401, {"error": "Please sign in"})
            with db() as c:
                rows = [dict(r) for r in c.execute("SELECT id, name, class, voted, voted_at FROM voters ORDER BY class, name")]
            return self.send(200, rows)
        self.send(404, {"error": "Not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        try:
            data = self.body()
        except Exception:
            return self.send(400, {"error": "Bad request"})

        # ----- voting -----
        if p == "/api/lookup":
            if setting("open") != "1":
                return self.send(403, {"error": "Voting is closed."})
            with db() as c:
                r = c.execute("SELECT id, name, class, voted FROM voters WHERE id=?", (norm_id(data.get("id")),)).fetchone()
            if not r:
                return self.send(404, {"error": "This number is not on the voters list."})
            if r["voted"]:
                return self.send(409, {"error": f"{r['name']} has already voted."})
            return self.send(200, {"id": r["id"], "name": r["name"], "class": r["class"]})
        if p == "/api/vote":
            if setting("open") != "1":
                return self.send(403, {"error": "Voting is closed."})
            vid, cid = norm_id(data.get("id")), data.get("candidate")
            with LOCK, db() as c:
                r = c.execute("SELECT voted FROM voters WHERE id=?", (vid,)).fetchone()
                if not r:
                    return self.send(404, {"error": "This number is not on the voters list."})
                if r["voted"]:
                    return self.send(409, {"error": "Already voted."})
                if not c.execute("SELECT 1 FROM candidates WHERE id=?", (cid,)).fetchone():
                    return self.send(400, {"error": "Choose a candidate."})
                c.execute("UPDATE voters SET voted=1, voted_at=? WHERE id=?", (time.strftime("%Y-%m-%d %H:%M:%S"), vid))
                c.execute("INSERT INTO votes(candidate_id) VALUES(?)", (cid,))
            return self.send(200, {"ok": True})

        # ----- admin sign-in -----
        if p == "/api/admin/setup":
            if setting("admin_pw") is not None:
                return self.send(403, {"error": "Already set up"})
            if len(data.get("password", "")) < 6:
                return self.send(400, {"error": "Use at least 6 characters."})
            set_setting("admin_pw", hash_pw(data["password"]))
            p = "/api/admin/login"
        if p == "/api/admin/login":
            stored = setting("admin_pw")
            if not stored or not check_pw(data.get("password", ""), stored):
                time.sleep(1)
                return self.send(401, {"error": "Wrong password."})
            tok = secrets.token_hex(24)
            SESSIONS[tok] = time.time() + 8 * 3600
            return self.send(200, {"ok": True}, headers={"Set-Cookie": f"tccadmin={tok}; Path=/; HttpOnly; SameSite=Strict"})

        if not p.startswith("/api/admin/"):
            return self.send(404, {"error": "Not found"})
        if not self.is_admin():
            return self.send(401, {"error": "Please sign in"})
        try:
            return self.admin(p, data)
        except ValueError as e:
            return self.send(400, {"error": str(e)})

    def admin(self, p, data):
        if p == "/api/admin/candidate":
            photo = None
            if data.get("photo"):
                head, _, b64 = data["photo"].partition(",")
                ext = "png" if "png" in head else "webp" if "webp" in head else "jpg"
                photo = f"{secrets.token_hex(8)}.{ext}"
                with open(os.path.join(PHOTOS, photo), "wb") as f:
                    f.write(base64.b64decode(b64))
            with LOCK, db() as c:
                if data.get("id"):
                    old = c.execute("SELECT photo FROM candidates WHERE id=?", (data["id"],)).fetchone()
                    c.execute("UPDATE candidates SET number=?, name=?, class=?, photo=? WHERE id=?", (data.get("number", ""), data.get("name", ""), data.get("class", ""), photo or (old["photo"] if old else None), data["id"]))
                else:
                    c.execute("INSERT INTO candidates(number,name,class,photo) VALUES(?,?,?,?)", (data.get("number", ""), data.get("name", ""), data.get("class", ""), photo))
            return self.send(200, {"ok": True})
        if p == "/api/admin/candidate/delete":
            with LOCK, db() as c:
                if c.execute("SELECT COUNT(*) n FROM votes WHERE candidate_id=?", (data.get("id"),)).fetchone()["n"]:
                    raise ValueError("This candidate already has votes. Reset the election first.")
                c.execute("DELETE FROM candidates WHERE id=?", (data.get("id"),))
            return self.send(200, {"ok": True})
        if p == "/api/admin/students":
            raw = base64.b64decode(data.get("file", "").partition(",")[2] or data.get("file", ""))
            return self.send(200, import_students(data.get("filename", "students.xlsx"), raw, bool(data.get("replace"))))
        if p == "/api/admin/voter":
            vid = norm_id(data.get("id"))
            if not vid or not data.get("name"):
                raise ValueError("Enter the number and name.")
            with LOCK, db() as c:
                c.execute("INSERT INTO voters(id,name,class) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, class=excluded.class", (vid, data["name"].strip(), data.get("class", "").strip()))
            return self.send(200, {"ok": True})
        if p == "/api/admin/open":
            set_setting("open", "1" if data.get("open") else "0")
            return self.send(200, {"ok": True})
        if p == "/api/admin/title":
            set_setting("title", str(data.get("title", "")).strip() or "Student Parliament Election")
            return self.send(200, {"ok": True})
        if p == "/api/admin/password":
            if len(data.get("password", "")) < 6:
                raise ValueError("Use at least 6 characters.")
            set_setting("admin_pw", hash_pw(data["password"]))
            return self.send(200, {"ok": True})
        if p == "/api/admin/reset":
            if data.get("confirm") != "RESET":
                raise ValueError('Type RESET to confirm.')
            with LOCK, db() as c:
                c.execute("DELETE FROM votes")
                c.execute("UPDATE voters SET voted=0, voted_at=NULL")
                if data.get("everything"):
                    c.execute("DELETE FROM voters")
                    c.execute("DELETE FROM candidates")
            set_setting("open", "0")
            return self.send(200, {"ok": True})
        return self.send(404, {"error": "Not found"})


def addresses():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for i in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not i.startswith("127."):
                ips.add(i)
    except OSError:
        pass
    return [f"http://{i}:{PORT}" for i in sorted(ips)]


def main():
    init_db()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{PORT}"
    print("Voting screen:", url, "\nAdmin panel:  ", url + "/admin", *addresses(), sep="\n")
    if os.environ.get("TCC_ELECTION_HEADLESS"):
        threading.Event().wait()
    try:
        import webview  # pywebview: a real Windows window
        webview.create_window("TCC Student Parliament Election", url, fullscreen="--window" not in sys.argv, min_size=(900, 640))
        webview.start()
    except Exception:
        webbrowser.open(url)
        webbrowser.open(url + "/admin")
        threading.Event().wait()


if __name__ == "__main__":
    main()
