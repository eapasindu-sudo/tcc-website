"""TCC Student Parliament Election.

One program that:
  * keeps the voters, candidates and votes in a local SQLite database,
  * opens the voting screen (students enter their admission/index number, see their name and class,
    click a candidate's photo and confirm),
  * serves the admin panel at http://<this-computer>:8765/admin (also reachable from other computers on the school network).

Votes are secret: the database records THAT a student voted, never WHO they voted for.
Data is stored in the folder "TCC-Election-Data" next to the program.
"""
import base64, csv, re, subprocess, hashlib, hmac, io, json, os, secrets, socket, sqlite3, sys, threading, time, webbrowser
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


# ---------- Internet link (Cloudflare quick tunnel) ----------
TUNNEL = {"proc": None, "url": None, "error": None}


def cloudflared_path():
    for d in (getattr(sys, "_MEIPASS", ""), BASE, os.path.dirname(os.path.abspath(__file__))):
        for n in ("cloudflared.exe", "cloudflared"):
            p = os.path.join(d, n)
            if d and os.path.isfile(p):
                return p
    return None


def tunnel_start():
    if TUNNEL["proc"] and TUNNEL["proc"].poll() is None:
        return
    exe = cloudflared_path()
    if not exe:
        TUNNEL["error"] = "cloudflared was not found next to the program."
        return
    TUNNEL.update(url=None, error=None)
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    proc = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{PORT}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", creationflags=flags)
    TUNNEL["proc"] = proc

    def watch():
        for line in proc.stdout:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m and not TUNNEL["url"]:
                TUNNEL["url"] = m.group(0)
        if TUNNEL["proc"] is proc:
            TUNNEL.update(url=None, error=TUNNEL["error"] or "The internet link stopped. Check the internet connection and press Start again.")
    threading.Thread(target=watch, daemon=True).start()


def tunnel_stop():
    p = TUNNEL["proc"]
    TUNNEL.update(proc=None, url=None, error=None)
    if p and p.poll() is None:
        p.terminate()


# ---------- Voting screen window (opened from the admin panel) ----------
WINDOWS = {"webview": None}


def open_voting_window(fullscreen):
    wv = WINDOWS["webview"]
    url = f"http://127.0.0.1:{PORT}/"
    if wv is None:
        webbrowser.open(url)
        return
    guard_close(wv.create_window("TCC Election · Voting", url, fullscreen=fullscreen, width=1200, height=820, min_size=(800, 600)))


def db():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA synchronous=FULL")  # every saved vote is on the disk before the student sees "thank you"
    return c


# ---------- Backups (power cuts, crashes) ----------
BACKUPS = os.path.join(DATA, "backups")
os.makedirs(BACKUPS, exist_ok=True)
_last_backup = [0.0]


def backup(force=False):
    """Copy the whole database to TCC-Election-Data/backups. Keeps the newest 30 copies."""
    if not force and time.time() - _last_backup[0] < 15:
        return
    _last_backup[0] = time.time()
    try:
        dst = sqlite3.connect(os.path.join(BACKUPS, time.strftime("election-%Y%m%d-%H%M%S.db")))
        with db() as src:
            src.backup(dst)
        dst.close()
        files = sorted(f for f in os.listdir(BACKUPS) if f.endswith(".db"))
        for f in files[:-30]:
            os.remove(os.path.join(BACKUPS, f))
    except Exception as e:
        print("Backup failed:", e)


def backup_loop():
    while True:
        time.sleep(60)
        backup(force=True)


# ---------- Closing the program needs the close PIN ----------
CLOSE = {"allowed": False}


def close_pin_ok(pin):
    stored = setting("close_pin") or setting("admin_pw")
    return bool(stored) and check_pw(str(pin or ""), stored)


def quit_app():
    CLOSE["allowed"] = True
    backup(force=True)
    tunnel_stop()
    wv = WINDOWS.get("webview")
    if wv:
        for w in list(wv.windows):
            try:
                w.destroy()
            except Exception:
                pass
    threading.Timer(1.5, lambda: os._exit(0)).start()


def guard_close(window):
    def closing():
        if CLOSE["allowed"]:
            return True
        try:
            window.evaluate_js("window.askClosePin && window.askClosePin()")
        except Exception:
            pass
        return False  # keep the window open until the PIN is entered
    window.events.closing += closing


def init_db():
    with db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS voters (id TEXT PRIMARY KEY, name TEXT, class TEXT, voted INTEGER DEFAULT 0, voted_at TEXT, pin TEXT);
        CREATE TABLE IF NOT EXISTS candidates (id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT, name TEXT, class TEXT, photo TEXT);
        CREATE TABLE IF NOT EXISTS votes (id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id INTEGER);
        """)
        if "pin" not in [r[1] for r in c.execute("PRAGMA table_info(voters)")]:
            c.execute("ALTER TABLE voters ADD COLUMN pin TEXT")


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


def norm_pin(v):
    s = str(v if v is not None else "").strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s or None


def pin_ok(row, given):
    return not row["pin"] or row["pin"] == norm_pin(given)


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200000).hex()


def check_pw(pw, stored):
    salt, _ = stored.split("$", 1)
    return hmac.compare_digest(hash_pw(pw, salt), stored)


# ---------- Student list import (Excel .xlsx or .csv) ----------
NAME_RE = ("name", "නම")
CLASS_RE = ("class", "grade", "පන්ති", "ශ්‍රේණි")
PIN_RE = ("pin", "password", "රහස්")
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
        taken.add(pin := find(row, PIN_RE, taken))
        idc = find(row, ID_RE, taken); taken.add(idc)
        name = find(row, NAME_RE, taken); taken.add(name)
        cls = find(row, CLASS_RE, taken)
        if idc is not None and name is not None:
            return hi, idc, name, cls, pin
    return None


def import_students(filename, raw, replace):
    rows = read_rows(filename, raw)
    found = detect(rows)
    if not found:
        raise ValueError("Could not find the name and admission/index number columns. Make sure the file has a heading row such as: Admission No | Name | Class")
    hi, idc, namec, clsc, pinc = found
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
            pin = norm_pin(r[pinc]) if pinc is not None and len(r) > pinc else None
            c.execute("INSERT INTO voters(id,name,class,pin) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, class=excluded.class, pin=excluded.pin", (vid, name, cls, pin))
            added += 1
    return {"added": added, "skipped": skipped, "columns": {"id": rows[hi][idc], "name": rows[hi][namec], "class": rows[hi][clsc] if clsc is not None else None, "pin": rows[hi][pinc] if pinc is not None else None}}


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

    def via_internet(self):
        return bool(self.headers.get("Cf-Ray") or self.headers.get("Cf-Connecting-Ip"))

    def is_admin(self):
        if self.via_internet():
            return False
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
            if self.via_internet():
                return self.send(403, b"<h1>The admin panel can only be opened on the school computer.</h1>", "text/html; charset=utf-8")
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
            return self.send(200, {"candidates": res, "voters": tot["n"], "voted": tot["v"] or 0, "byClass": by_class, "open": setting("open") == "1", "title": setting("title", "Student Parliament Election"), "addresses": addresses(),
                                    "closePinSet": setting("close_pin") is not None, "backups": BACKUPS,
                                    "tunnel": {"running": bool(TUNNEL["proc"] and TUNNEL["proc"].poll() is None), "url": TUNNEL["url"], "error": TUNNEL["error"], "available": bool(cloudflared_path())}})
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
                r = c.execute("SELECT id, name, class, voted, pin FROM voters WHERE id=?", (norm_id(data.get("id")),)).fetchone()
            if not r:
                return self.send(404, {"error": "This number is not on the voters list."})
            if not pin_ok(r, data.get("pin")):
                time.sleep(1)
                return self.send(401, {"error": "PIN", "needPin": True})
            if r["voted"]:
                return self.send(409, {"error": f"{r['name']} has already voted."})
            return self.send(200, {"id": r["id"], "name": r["name"], "class": r["class"]})
        if p == "/api/vote":
            if setting("open") != "1":
                return self.send(403, {"error": "Voting is closed."})
            vid, cid = norm_id(data.get("id")), data.get("candidate")
            with LOCK, db() as c:
                r = c.execute("SELECT voted, pin FROM voters WHERE id=?", (vid,)).fetchone()
                if not r:
                    return self.send(404, {"error": "This number is not on the voters list."})
                if not pin_ok(r, data.get("pin")):
                    return self.send(401, {"error": "Wrong PIN."})
                if r["voted"]:
                    return self.send(409, {"error": "Already voted."})
                if not c.execute("SELECT 1 FROM candidates WHERE id=?", (cid,)).fetchone():
                    return self.send(400, {"error": "Choose a candidate."})
                c.execute("UPDATE voters SET voted=1, voted_at=? WHERE id=?", (time.strftime("%Y-%m-%d %H:%M:%S"), vid))
                c.execute("INSERT INTO votes(candidate_id) VALUES(?)", (cid,))
            threading.Thread(target=backup, daemon=True).start()
            return self.send(200, {"ok": True})

        # ----- closing the program (only on this computer) -----
        if p == "/api/quit":
            if self.via_internet() or self.client_address[0] not in ("127.0.0.1", "::1"):
                return self.send(403, {"error": "Not allowed"})
            if not close_pin_ok(data.get("pin")):
                time.sleep(1)
                return self.send(401, {"error": "Wrong PIN"})
            quit_app()
            return self.send(200, {"ok": True})

        # ----- admin sign-in -----
        if p.startswith("/api/admin/") and self.via_internet():
            return self.send(403, {"error": "The admin panel can only be used on the school computer."})
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
                c.execute("INSERT INTO voters(id,name,class,pin) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, class=excluded.class, pin=excluded.pin", (vid, data["name"].strip(), data.get("class", "").strip(), norm_pin(data.get("pin"))))
            return self.send(200, {"ok": True})
        if p == "/api/admin/votewin":
            open_voting_window(bool(data.get("fullscreen")))
            return self.send(200, {"ok": True})
        if p == "/api/admin/tunnel":
            tunnel_start() if data.get("on") else tunnel_stop()
            return self.send(200, {"ok": True})
        if p == "/api/admin/open":
            set_setting("open", "1" if data.get("open") else "0")
            return self.send(200, {"ok": True})
        if p == "/api/admin/title":
            set_setting("title", str(data.get("title", "")).strip() or "Student Parliament Election")
            return self.send(200, {"ok": True})
        if p == "/api/admin/closepin":
            if len(str(data.get("pin", ""))) < 4:
                raise ValueError("Use at least 4 digits.")
            set_setting("close_pin", hash_pw(str(data["pin"])))
            return self.send(200, {"ok": True})
        if p == "/api/admin/backup":
            backup(force=True)
            return self.send(200, {"ok": True, "folder": BACKUPS})
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
    import atexit
    atexit.register(tunnel_stop)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=backup_loop, daemon=True).start()
    backup(force=True)
    url = f"http://127.0.0.1:{PORT}"
    print("Voting screen:", url, "\nAdmin panel:  ", url + "/admin", *addresses(), sep="\n")
    if os.environ.get("TCC_ELECTION_HEADLESS"):
        threading.Event().wait()
    try:
        import webview  # pywebview: real Windows windows, no browser needed
        WINDOWS["webview"] = webview
        guard_close(webview.create_window("TCC Election · Admin", url + "/admin", width=1280, height=860, min_size=(900, 640), maximized=True))
        webview.start()
        tunnel_stop()
        os._exit(0)
    except Exception:
        webbrowser.open(url)
        webbrowser.open(url + "/admin")
        threading.Event().wait()


if __name__ == "__main__":
    main()
