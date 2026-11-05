"""TCC Student Parliament Election.

One program that:
  * keeps the voters, candidates and votes in a local SQLite database,
  * opens the voting screen (students enter their admission/index number, see their name and class,
    click a candidate's photo and confirm),
  * serves the admin panel at http://<this-computer>:8765/admin (also reachable from other computers on the school network).

Votes are secret: the database records THAT a student voted, never WHO they voted for.
Data is stored in the folder "TCC-Election-Data" next to the program.
"""
import base64, csv, re, subprocess, hashlib, hmac, io, json, os, secrets, socket, sqlite3, sys, threading, time, urllib.request, webbrowser
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
MASTER_PIN = "7274"  # installs the admin version, unlocks the admin panel, closes the program (until a close PIN is set)
FIND_PORT = 8766     # UDP: voting PCs find the admin PC on the network
LOCAL_PORT = int(os.environ.get("TCC_ELECTION_LOCAL_PORT", "8767"))  # setup / "connecting" screen on a voting PC
CONF_PATH = os.path.join(DATA, "install.json")
POSTS = os.path.join(DATA, "posts")
EXPORTS = os.path.join(DATA, "exports")
try:
    with open(os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "version.txt")) as _f:
        VERSION = _f.read().strip() or "dev"
except OSError:
    VERSION = "dev"
FROZEN = bool(getattr(sys, "frozen", False))


# ---------- Updates: voting PCs copy the new program from the admin PC ----------
def swap_exe(new_path):
    """Replace the running program with new_path and start it again (data folder is kept)."""
    exe = sys.executable
    old = exe + ".old"
    try:
        if os.path.exists(old):
            os.remove(old)
    except OSError:
        pass
    os.replace(exe, old)          # Windows lets a running .exe be renamed
    os.replace(new_path, exe)
    CLOSE["allowed"] = True
    backup(force=True)
    tunnel_stop()
    subprocess.Popen([exe], cwd=os.path.dirname(exe), close_fds=True)
    os._exit(0)


def update_from_admin(server):
    tmp = sys.executable + ".new"
    try:
        with urllib.request.urlopen(f"http://{server}/app/TCC-Election.exe", timeout=60) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        if os.path.getsize(tmp) < 1_000_000:
            raise ValueError("download too small")
        swap_exe(tmp)
    except Exception as e:
        print("Update failed:", e)
        CLIENT["updating"] = False


def cleanup_old_exe():
    if FROZEN:
        try:
            os.remove(sys.executable + ".old")
        except OSError:
            pass


def load_conf():
    if os.environ.get("TCC_ELECTION_MODE"):
        return {"mode": os.environ["TCC_ELECTION_MODE"], "pc": os.environ.get("TCC_ELECTION_PC", "PC01"), "server": os.environ.get("TCC_ELECTION_SERVER", "")}
    try:
        with open(CONF_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


CONF = {"c": load_conf()}


def save_conf(c):
    with open(CONF_PATH, "w", encoding="utf-8") as f:
        json.dump(c, f)
    CONF["c"] = c


# ---------- Network: voting PCs find the admin PC by themselves ----------
def find_responder():
    """Admin PC answers "where is the election?" broadcasts from voting PCs."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", FIND_PORT))
    except OSError as e:
        print("Network finder not started:", e)
        return
    while True:
        try:
            msg, addr = s.recvfrom(256)
            if msg == b"TCC-ELECTION?":
                s.sendto(f"TCC-ELECTION:{PORT}:{socket.gethostname()}".encode(), addr)
        except OSError:
            time.sleep(1)


def discover(wait=1.5):
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(0.3)
    targets = {"255.255.255.255"}
    for a in addresses():
        ip = a.split("//")[1].split(":")[0]
        targets.add(ip.rsplit(".", 1)[0] + ".255")
    end = time.time() + wait
    sent = 0
    while time.time() < end:
        if sent < 3:
            for t in targets:
                try:
                    s.sendto(b"TCC-ELECTION?", (t, FIND_PORT))
                except OSError:
                    pass
            sent += 1
        try:
            msg, addr = s.recvfrom(256)
            parts = msg.decode(errors="replace").split(":")
            if parts[0] == "TCC-ELECTION":
                found[addr[0]] = {"ip": addr[0], "port": int(parts[1]), "name": parts[2] if len(parts) > 2 else addr[0]}
        except (socket.timeout, OSError, ValueError, IndexError):
            pass
    s.close()
    return list(found.values())


def server_ok(server):
    try:
        with urllib.request.urlopen(f"http://{server}/api/state", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def setup_network():
    """Windows: open the firewall for the election and mark the network private. Windows shows one "allow" popup."""
    if os.name != "nt":
        return False
    script = ("Remove-NetFirewallRule -DisplayName 'TCC Election*' -ErrorAction SilentlyContinue; "
              f"New-NetFirewallRule -DisplayName 'TCC Election' -Direction Inbound -Action Allow -Protocol TCP -LocalPort {PORT} -Profile Any; "
              f"New-NetFirewallRule -DisplayName 'TCC Election Find' -Direction Inbound -Action Allow -Protocol UDP -LocalPort {FIND_PORT} -Profile Any; "
              "Get-NetConnectionProfile | Set-NetConnectionProfile -NetworkCategory Private -ErrorAction SilentlyContinue")
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
                            f"Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList '-NoProfile','-EncodedCommand','{enc}'"],
                           creationflags=0x08000000, timeout=180)
        return r.returncode == 0
    except Exception as e:
        print("Network setup failed:", e)
        return False


def desktop_shortcut(label):
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    exe = sys.executable.replace("'", "''")
    script = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\\" + label + ".lnk');"
              f"$s.TargetPath='{exe}';$s.WorkingDirectory='{os.path.dirname(exe)}';$s.Save()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script], creationflags=0x08000000, timeout=60)
    except Exception:
        pass


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
    guard_close(wv.create_window("TCC Election · Voting", url + "?pc=ADMIN-PC", fullscreen=fullscreen, width=1200, height=820, min_size=(800, 600), js_api=Api()))


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
        export_xlsx(os.path.join(BACKUPS, "students-by-class-latest.xlsx"))
    except Exception as e:
        print("Backup failed:", e)


def export_xlsx(path):
    """Excel file: a summary sheet plus one sheet per class (who voted, for manual checking/counting)."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Summary"
    bold = Font(bold=True)
    with db() as c:
        cands = c.execute("SELECT c.number, c.name, c.class, COUNT(v.id) n FROM candidates c LEFT JOIN votes v ON v.candidate_id=c.id GROUP BY c.id ORDER BY n DESC").fetchall()
        tot = c.execute("SELECT COUNT(*) n, SUM(voted) v FROM voters").fetchone()
        voters = c.execute("SELECT id, name, class, voted, voted_at, voted_pc FROM voters ORDER BY class, name").fetchall()
    ws.append([setting("title", "Student Parliament Election"), time.strftime("%Y-%m-%d %H:%M:%S")])
    ws.append(["Voters", tot["n"], "Voted", tot["v"] or 0, "Not voted", tot["n"] - (tot["v"] or 0)])
    ws.append([])
    ws.append(["Candidate No.", "Name", "Class", "Votes"])
    for cell in ws[4]:
        cell.font = bold
    for r in cands:
        ws.append([r["number"], r["name"], r["class"], r["n"]])
    by = {}
    for v in voters:
        by.setdefault(v["class"] or "No class", []).append(v)
    ws.append([])
    ws.append(["Class", "Students", "Voted", "%"])
    for cell in ws[ws.max_row]:
        cell.font = bold
    green = PatternFill("solid", fgColor="E3F4E6")
    used = set()
    for cls, rows in by.items():
        voted = sum(1 for r in rows if r["voted"])
        ws.append([cls, len(rows), voted, round(voted * 100 / len(rows), 1)])
        name = re.sub(r"[\\/*?:\[\]]", "-", str(cls))[:28] or "Class"
        while name in used:
            name += "_"
        used.add(name)
        sh = wb.create_sheet(name)
        sh.append([f"Class {cls}", f"{voted} / {len(rows)} voted"])
        sh.append(["#", "Admission / index no.", "Name", "Voted", "Time", "PC"])
        for cell in sh[2]:
            cell.font = bold
        for i, r in enumerate(rows, 1):
            sh.append([i, r["id"], r["name"], "YES" if r["voted"] else "", r["voted_at"] or "", r["voted_pc"] or ""])
            if r["voted"]:
                for cell in sh[sh.max_row]:
                    cell.fill = green
        for col, w in zip("ABCDEF", (5, 22, 34, 8, 20, 12)):
            sh.column_dimensions[col].width = w
    tmp = path + ".tmp"
    wb.save(tmp)
    os.replace(tmp, path)


def backup_loop():
    while True:
        time.sleep(60)
        record_turnout()
        backup(force=True)


def record_turnout():
    """Keep the turnout at the end of every hour (for the hourly percentage and the Facebook post)."""
    if setting("open") != "1":
        return
    with db() as c:
        t = c.execute("SELECT COUNT(*) n, SUM(voted) v FROM voters").fetchone()
        c.execute("INSERT INTO turnout(hour,voted,voters) VALUES(?,?,?) ON CONFLICT(hour) DO UPDATE SET voted=excluded.voted, voters=excluded.voters",
                  (time.strftime("%Y-%m-%d %H"), t["v"] or 0, t["n"]))


# ---------- Closing the program needs the close PIN ----------
CLOSE = {"allowed": False}


def close_pin_ok(pin):
    stored = setting("close_pin")
    if not stored:
        return hmac.compare_digest(str(pin or "").strip(), MASTER_PIN)
    return check_pw(str(pin or "").strip(), stored)


class Api:
    """Called from the page inside the program window (window.pywebview.api)."""
    def quit(self, pin):
        conf = CONF["c"] or {}
        if conf.get("mode") == "voting":
            try:
                req = urllib.request.Request(f"http://{conf['server']}/api/closecheck", data=json.dumps({"pin": pin}).encode(), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5) as r:
                    ok = r.status == 200
            except Exception:
                ok = hmac.compare_digest(str(pin or "").strip(), MASTER_PIN)  # admin PC unreachable: master PIN still works
        else:
            ok = close_pin_ok(pin)
        if ok:
            threading.Timer(0.3, quit_app).start()  # answer the page first, then close
        else:
            time.sleep(1)
        return ok


def quit_app():
    CLOSE["allowed"] = True
    threading.Timer(2.0, lambda: os._exit(0)).start()  # always exits, even if a window does not close
    backup(force=True)
    tunnel_stop()
    wv = WINDOWS.get("webview")
    if wv:
        for w in list(wv.windows):
            try:
                w.destroy()
            except Exception:
                pass


def guard_close(window):
    def closing():
        if CLOSE["allowed"] or not CONF["c"]:
            return True
        # evaluate_js waits for the window's thread, which is busy running this handler:
        # call it from another thread, or the window freezes (PIN box and Cancel stop working).
        def ask():
            try:
                window.evaluate_js("window.askClosePin && window.askClosePin()")
            except Exception:
                pass
        threading.Thread(target=ask, daemon=True).start()
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
        CREATE TABLE IF NOT EXISTS pcs (name TEXT PRIMARY KEY, ip TEXT, last_seen REAL, votes INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS turnout (hour TEXT PRIMARY KEY, voted INTEGER, voters INTEGER);
        CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, voter TEXT, name TEXT, class TEXT, pc TEXT, first_at TEXT, first_pc TEXT, seen INTEGER DEFAULT 0);
        """)
        cols = [r[1] for r in c.execute("PRAGMA table_info(voters)")]
        if "pin" not in cols:
            c.execute("ALTER TABLE voters ADD COLUMN pin TEXT")
        if "voted_pc" not in cols:
            c.execute("ALTER TABLE voters ADD COLUMN voted_pc TEXT")


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
        if n > 150 * 1024 * 1024:
            raise ValueError("File too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def via_internet(self):
        return bool(self.headers.get("Cf-Ray") or self.headers.get("Cf-Connecting-Ip"))

    def local(self):
        return not self.via_internet() and self.client_address[0] in ("127.0.0.1", "::1")

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
        if p in ("/setup", "/connect", "/results"):
            if not self.local() and p != "/results" or self.via_internet():
                return self.send(403, {"error": "Not allowed"})
            return self.file(os.path.join(WEB, p[1:] + ".html"), "text/html; charset=utf-8")
        if p == "/api/version":
            return self.send(200, {"version": VERSION, "frozen": FROZEN})
        if p == "/app/TCC-Election.exe":
            if self.via_internet() or not FROZEN:
                return self.send(404, {"error": "Not found"})
            return self.file(sys.executable, "application/octet-stream")
        if p == "/api/admin/alerts":
            if not self.is_admin():
                return self.send(401, {"error": "Please sign in"})
            with db() as c:
                return self.send(200, [dict(r) for r in c.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT 500")])
        if p == "/api/whoami":
            return self.send(200, {"local": self.local(), "mode": (CONF["c"] or {}).get("mode"), "conf": CONF["c"] if self.local() else None})
        if p == "/api/client/status":
            conf = CONF["c"] or {}
            if not self.local() or conf.get("mode") != "voting":
                return self.send(403, {"error": "Not allowed"})
            return self.send(200, client_status())
        if p in ("/", "/vote"):
            return self.file(os.path.join(WEB, "vote.html"), "text/html; charset=utf-8")
        if p == "/admin":
            if self.via_internet():
                return self.send(403, b"<h1>The admin panel can only be opened on the school computer.</h1>", "text/html; charset=utf-8")
            return self.file(os.path.join(WEB, "admin.html"), "text/html; charset=utf-8")
        if p in ("/logo.webp", "/style.css", "/close.js"):
            return self.file(os.path.join(WEB, p[1:]), {"webp": "image/webp", "css": "text/css", "js": "text/javascript"}[p.rsplit(".", 1)[1]])
        if p.startswith("/photos/"):
            name = os.path.basename(p)
            ext = name.rsplit(".", 1)[-1].lower()
            return self.file(os.path.join(PHOTOS, name), {"png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg"))
        if p == "/api/state":
            with db() as c:
                cands = [dict(r) for r in c.execute("SELECT id, number, name, class, photo FROM candidates ORDER BY CAST(number AS INTEGER), number")]
            return self.send(200, {"open": setting("open") == "1", "title": setting("title", "Student Parliament Election"), "candidates": cands, "setup": setting("admin_pw") is None, "ended": setting("ended") == "1", "perStudent": int(setting("per_student", "1") or 1), "version": VERSION})
        if p == "/api/admin/results":
            if not self.is_admin():
                return self.send(401, {"error": "Please sign in"})
            with db() as c:
                res = [dict(r) for r in c.execute("SELECT c.id, c.number, c.name, c.class, c.photo, COUNT(v.id) AS votes FROM candidates c LEFT JOIN votes v ON v.candidate_id=c.id GROUP BY c.id ORDER BY votes DESC, CAST(c.number AS INTEGER)")]
                tot = c.execute("SELECT COUNT(*) n, SUM(voted) v FROM voters").fetchone()
                by_class = [dict(r) for r in c.execute("SELECT class, COUNT(*) total, SUM(voted) voted FROM voters GROUP BY class ORDER BY class")]
                now = time.time()
                pcs = [dict(r, online=(now - (r["last_seen"] or 0)) < 15) for r in c.execute("SELECT name, ip, last_seen, votes FROM pcs ORDER BY name")]
                hourly = [dict(r) for r in c.execute("SELECT hour, voted, voters FROM turnout ORDER BY hour")]
            with db() as c:
                alerts = c.execute("SELECT COUNT(*) n, SUM(seen=0) u FROM alerts").fetchone()
            return self.send(200, {"ended": setting("ended") == "1", "alerts": alerts["n"], "alertsNew": alerts["u"] or 0, "perStudent": int(setting("per_student", "1") or 1), "version": VERSION, "pcs": pcs, "hourly": hourly, "now": time.strftime("%Y-%m-%d %H:%M"),
                                   "candidates": res, "voters": tot["n"], "voted": tot["v"] or 0, "byClass": by_class, "open": setting("open") == "1", "title": setting("title", "Student Parliament Election"), "addresses": addresses(),
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
        if p == "/api/ping":
            pc = str(data.get("pc") or "").strip()[:20]
            if pc and not self.via_internet():
                with LOCK, db() as c:
                    c.execute("INSERT INTO pcs(name,ip,last_seen) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET ip=excluded.ip, last_seen=excluded.last_seen", (pc, self.client_address[0], time.time()))
            return self.send(200, {"ok": True, "open": setting("open") == "1"})
        if p == "/api/closecheck":
            if self.via_internet():
                return self.send(403, {"error": "Not allowed"})
            if not close_pin_ok(data.get("pin")):
                time.sleep(1)
                return self.send(401, {"error": "Wrong PIN"})
            return self.send(200, {"ok": True})
        if p.startswith("/api/setup/"):
            if not self.local() or CONF["c"]:
                return self.send(403, {"error": "Already installed"})
            return self.setup_api(p, data)
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
                pc = "Internet link" if self.via_internet() else str(data.get("pc") or "Browser").strip()[:20]
                with LOCK, db() as c:
                    f = c.execute("SELECT voted_at, voted_pc FROM voters WHERE id=?", (r["id"],)).fetchone()
                    c.execute("INSERT INTO alerts(at,voter,name,class,pc,first_at,first_pc) VALUES(?,?,?,?,?,?,?)",
                              (time.strftime("%Y-%m-%d %H:%M:%S"), r["id"], r["name"], r["class"], pc, f["voted_at"], f["voted_pc"]))
                return self.send(409, {"error": f"{r['name']} has already voted. මේ අංකයෙන් දැනටමත් ඡන්දය දී ඇත."})
            return self.send(200, {"id": r["id"], "name": r["name"], "class": r["class"]})
        if p == "/api/vote":
            if setting("open") != "1":
                return self.send(403, {"error": "Voting is closed."})
            vid = norm_id(data.get("id"))
            cids = data.get("candidates") or [data.get("candidate")]
            per = int(setting("per_student", "1") or 1)
            with LOCK, db() as c:
                r = c.execute("SELECT voted, pin FROM voters WHERE id=?", (vid,)).fetchone()
                if not r:
                    return self.send(404, {"error": "This number is not on the voters list."})
                if not pin_ok(r, data.get("pin")):
                    return self.send(401, {"error": "Wrong PIN."})
                if r["voted"]:
                    return self.send(409, {"error": "Already voted."})
                try:
                    cids = list(dict.fromkeys(int(x) for x in cids))
                except (TypeError, ValueError):
                    return self.send(400, {"error": "Choose a candidate."})
                if not 1 <= len(cids) <= per:
                    return self.send(400, {"error": f"Choose 1 to {per} candidates."})
                if c.execute(f"SELECT COUNT(*) n FROM candidates WHERE id IN ({','.join('?' * len(cids))})", cids).fetchone()["n"] != len(cids):
                    return self.send(400, {"error": "Choose a candidate."})
                pc = "Internet link" if self.via_internet() else str(data.get("pc") or "Browser").strip()[:20]
                c.execute("UPDATE voters SET voted=1, voted_at=?, voted_pc=? WHERE id=?", (time.strftime("%Y-%m-%d %H:%M:%S"), pc, vid))
                c.executemany("INSERT INTO votes(candidate_id) VALUES(?)", [(x,) for x in cids])
                c.execute("INSERT INTO pcs(name,ip,last_seen,votes) VALUES(?,?,?,1) ON CONFLICT(name) DO UPDATE SET votes=votes+1", (pc, self.client_address[0], time.time() if pc != "Internet link" else 0))
            threading.Thread(target=lambda: (record_turnout(), backup()), daemon=True).start()
            return self.send(200, {"ok": True})

        # ----- closing the program (only on this computer) -----
        if p == "/api/quit":
            if self.via_internet() or self.client_address[0] not in ("127.0.0.1", "::1"):
                return self.send(403, {"error": "Not allowed"})
            if not close_pin_ok(data.get("pin")):
                time.sleep(1)
                return self.send(401, {"error": "Wrong PIN"})
            threading.Timer(0.3, quit_app).start()
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
        if p == "/api/admin/unlock":
            if not self.local():
                return self.send(403, {"error": "Use the admin password on other computers."})
            if not hmac.compare_digest(str(data.get("pin", "")).strip(), MASTER_PIN):
                time.sleep(1)
                return self.send(401, {"error": "Wrong PIN."})
            tok = secrets.token_hex(24)
            SESSIONS[tok] = time.time() + 8 * 3600
            return self.send(200, {"ok": True}, headers={"Set-Cookie": f"tccadmin={tok}; Path=/; HttpOnly; SameSite=Strict"})
        if p == "/api/admin/lock":
            for part in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = part.strip().partition("=")
                if k == "tccadmin":
                    SESSIONS.pop(v, None)
            return self.send(200, {"ok": True}, headers={"Set-Cookie": "tccadmin=; Path=/; Max-Age=0"})
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
            if data.get("open"):
                set_setting("ended", "0")
            record_turnout()
            return self.send(200, {"ok": True})
        if p == "/api/admin/end":
            record_turnout()
            set_setting("open", "0")
            set_setting("ended", "1")
            backup(force=True)
            return self.send(200, {"ok": True})
        if p == "/api/admin/resultswin":
            wv = WINDOWS["webview"]
            if wv is None:
                webbrowser.open(f"http://127.0.0.1:{PORT}/results")
            else:
                wv.create_window("TCC Election · Results", f"http://127.0.0.1:{PORT}/results", fullscreen=True)
            return self.send(200, {"ok": True})
        if p == "/api/admin/savepost":
            os.makedirs(POSTS, exist_ok=True)
            name = re.sub(r"[^A-Za-z0-9_-]", "", str(data.get("name", "post")))[:60] or "post"
            path = os.path.join(POSTS, f"{name}.png")
            with open(path, "wb") as f:
                f.write(base64.b64decode(str(data.get("image", "")).partition(",")[2]))
            if os.name == "nt":
                try:
                    os.startfile(POSTS)
                except OSError:
                    pass
            return self.send(200, {"ok": True, "path": path})
        if p == "/api/admin/title":
            set_setting("title", str(data.get("title", "")).strip() or "Student Parliament Election")
            return self.send(200, {"ok": True})
        if p == "/api/admin/perstudent":
            n = int(data.get("n") or 1)
            if not 1 <= n <= 20:
                raise ValueError("Use a number from 1 to 20.")
            set_setting("per_student", str(n))
            return self.send(200, {"ok": True})
        if p == "/api/admin/alerts/seen":
            with db() as c:
                c.execute("UPDATE alerts SET seen=1")
            return self.send(200, {"ok": True})
        if p == "/api/admin/export":
            os.makedirs(EXPORTS, exist_ok=True)
            path = os.path.join(EXPORTS, time.strftime("students-by-class-%Y%m%d-%H%M.xlsx"))
            export_xlsx(path)
            if os.name == "nt":
                try:
                    os.startfile(EXPORTS)
                except OSError:
                    pass
            return self.send(200, {"ok": True, "path": path})
        if p == "/api/admin/update":
            if not self.local() or not FROZEN:
                raise ValueError("Updates can only be installed from the program on the admin PC.")
            raw = base64.b64decode(str(data.get("file", "")).partition(",")[2])
            if len(raw) < 1_000_000 or raw[:2] != b"MZ":
                raise ValueError("This is not the TCC-Election.exe program file.")
            tmp = sys.executable + ".new"
            with open(tmp, "wb") as f:
                f.write(raw)
            threading.Timer(1.0, lambda: swap_exe(tmp)).start()
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
                c.execute("DELETE FROM pcs")
                c.execute("DELETE FROM turnout")
                c.execute("DELETE FROM alerts")
                c.execute("UPDATE voters SET voted=0, voted_at=NULL")
                if data.get("everything"):
                    c.execute("DELETE FROM voters")
                    c.execute("DELETE FROM candidates")
            set_setting("open", "0")
            set_setting("ended", "0")
            return self.send(200, {"ok": True})
        return self.send(404, {"error": "Not found"})

    def setup_api(self, p, data):
        """First start: choose the admin version (needs the PIN) or a voting PC."""
        if p == "/api/setup/discover":
            return self.send(200, {"found": discover()})
        if p == "/api/setup/admin":
            if not hmac.compare_digest(str(data.get("pin", "")).strip(), MASTER_PIN):
                time.sleep(1)
                return self.send(401, {"error": "PIN අංකය වැරදියි · Wrong PIN"})
            save_conf({"mode": "admin"})
            threading.Thread(target=lambda: (setup_network(), desktop_shortcut("TCC Election Admin")), daemon=True).start()
            SWITCH.set()
            return self.send(200, {"ok": True})
        if p == "/api/setup/voting":
            pc = str(data.get("pc", "")).strip().upper()
            server = str(data.get("server", "")).strip()
            if not re.fullmatch(r"[A-Z0-9-]{1,20}", pc):
                return self.send(400, {"error": "Choose a PC name."})
            if ":" not in server:
                server += f":{PORT}"
            if not server_ok(server):
                return self.send(400, {"error": f"Cannot reach the admin PC at {server}. Make sure the admin PC program is open."})
            save_conf({"mode": "voting", "pc": pc, "server": server})
            threading.Thread(target=lambda: desktop_shortcut(f"TCC Election {pc}"), daemon=True).start()
            SWITCH.set()
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


SWITCH = threading.Event()  # set when the first-start setup has been saved
CLIENT = {"fails": 0, "updating": False}


def client_status():
    conf = CONF["c"]
    if CLIENT["updating"]:
        return {"ok": False, "updating": True, "pc": conf["pc"], "server": conf["server"]}
    if server_ok(conf["server"]):
        CLIENT["fails"] = 0
        try:
            with urllib.request.urlopen(f"http://{conf['server']}/api/version", timeout=3) as r:
                v = json.load(r)
            if FROZEN and v.get("frozen") and v.get("version") not in (None, "dev", VERSION):
                CLIENT["updating"] = True
                threading.Thread(target=update_from_admin, args=(conf["server"],), daemon=True).start()
                return {"ok": False, "updating": True, "pc": conf["pc"], "server": conf["server"]}
        except Exception:
            pass
        return {"ok": True, "url": f"http://{conf['server']}/?pc={conf['pc']}&home=http://127.0.0.1:{LOCAL_PORT}/connect", "pc": conf["pc"], "server": conf["server"]}
    CLIENT["fails"] += 1
    if CLIENT["fails"] % 3 == 0:  # the admin PC may have a new address: look for it on the network
        found = discover(1.0)
        if found:
            conf = dict(conf, server=f"{found[0]['ip']}:{found[0]['port']}")
            save_conf(conf)
    return {"ok": False, "pc": conf["pc"], "server": conf["server"]}


def start_server(host, port):
    srv = ThreadingHTTPServer((host, port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def admin_services():
    start_server("0.0.0.0", PORT)
    threading.Thread(target=backup_loop, daemon=True).start()
    threading.Thread(target=find_responder, daemon=True).start()
    backup(force=True)
    url = f"http://127.0.0.1:{PORT}"
    print("Voting screen:", url, "\nAdmin panel:  ", url + "/admin", *addresses(), sep="\n")
    return url + "/admin"


def main():
    init_db()
    cleanup_old_exe()
    import atexit
    atexit.register(tunnel_stop)
    try:
        import webview  # pywebview: real Windows windows, no browser needed
    except Exception:
        webview = None
    if os.environ.get("TCC_ELECTION_HEADLESS"):
        webview = None
    WINDOWS["webview"] = webview
    conf = CONF["c"]
    local = f"http://127.0.0.1:{LOCAL_PORT}"

    if conf and conf.get("mode") == "admin":
        url = admin_services()
        if not webview:
            webbrowser.open(url)
            return threading.Event().wait()
        guard_close(webview.create_window("TCC Election · Admin", url, width=1280, height=860, min_size=(900, 640), maximized=True, js_api=Api()))
    elif conf and conf.get("mode") == "voting":
        start_server("127.0.0.1", LOCAL_PORT)
        if not webview:
            webbrowser.open(local + "/connect")
            return threading.Event().wait()
        guard_close(webview.create_window(f"TCC Election · {conf['pc']}", local + "/connect", fullscreen=True, js_api=Api()))
    else:
        setup_srv = start_server("127.0.0.1", LOCAL_PORT)
        print("First start: open", local + "/setup")
        win = webview.create_window("TCC Election · Install", local + "/setup", width=980, height=760, js_api=Api()) if webview else None

        def after_setup():
            SWITCH.wait()
            time.sleep(0.6)
            if CONF["c"]["mode"] == "admin":
                setup_srv.shutdown()
                setup_srv.server_close()
                url = admin_services()
                if win:
                    guard_close(win)
                    win.load_url(url)
                    try:
                        win.maximize()
                    except Exception:
                        pass
            elif win:
                guard_close(win)
                win.load_url(local + "/connect")
                win.toggle_fullscreen()
        threading.Thread(target=after_setup, daemon=True).start()
        if not webview:
            webbrowser.open(local + "/setup")
            return threading.Event().wait()
    webview.start()
    tunnel_stop()
    os._exit(0)


if __name__ == "__main__":
    main()
