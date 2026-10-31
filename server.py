"""Сэтгэл зүйн цахим үйлчилгээ — Python стандарт сан л хэрэгтэй (pip шаардлагагүй).
Ажиллуулах: python server.py  ->  http://localhost:8000

Сэтгэл зүйч үүсгэх (нэг удаа, дахин нэмж болно):
  python server.py --add-user         (нэр, нууц үгийг асууна — нууц үг дэлгэцэнд харагдахгүй)
Нууц үг мартсан бол ижил тушаалаар ижил нэрээр дахин үүсгэнэ.

Орчны хувьсагч:
  PAYMENT_PROVIDER=demo|manual|qpay   (анхдагч demo)
  BANK_ACCOUNT, BANK_NAME, ACCOUNT_NAME   (manual үед)
  QPAY_USERNAME, QPAY_PASSWORD, QPAY_INVOICE_CODE, PUBLIC_URL   (qpay үед)
  EMERGENCY_TEXT=...           (яаралтай тусламжийн дугаар — ӨӨРИЙН баталгаажуулсан дугаараа бичнэ)
  COOKIE_SECURE=1              (HTTPS-ээр ажиллуулж байгаа үед заавал)
"""
import base64, getpass, hashlib, hmac, json, mimetypes, os, re, secrets, sqlite3, sys, threading, time, unicodedata
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PORT = int(os.environ.get("PORT", "8000"))
PROVIDER = os.environ.get("PAYMENT_PROVIDER", "demo")
COOKIE_SECURE = os.environ.get("COOKIE_SECURE") == "1"
SESSION_SECONDS = 8 * 3600
EMERGENCY_TEXT = os.environ.get(
    "EMERGENCY_TEXT", "Яаралтай үед нутгийнхаа яаралтай тусламжийн дугаарт залгана уу.")
ROOT = Path(__file__).parent.resolve()
PUBLIC = ROOT / "public"
DB_PATH = ROOT / "data.sqlite3"

PRICE = 3000          # анхдагч үнэ (төгрөг). Сорил бүрийн үнийг самбараас тохируулна

# Анхны сорил. Зөвхөн эхний удаа өгөгдлийн санд орно, дараа нь бүгдийг сэтгэл зүйчийн самбараас засна.
# АНХААР: онооны зэрэглэлийг сорилын эх сурвалжтай тулгаж баталгаажуулна уу.
_PHONE_ITEMS = [
    "## I хэсэг",
    "Өөрийнхөө гар утсанд ирж байгаа мэдээ, мэдээллийг байнга харж чадахгүй бол хэцүү санагддаг.",
    "Гар утасныхаа тусламжтайгаар хүссэн цагтаа мэдээлэл авч чадахгүй бол урам хугардаг.",
    "Өөрийнхөө гар утсанд цаг үеийн мэдээг хүлээн авч чадахгүй бол унтууцаж эхэлдэг.",
    "Хэрэгцээтэй үедээ гар утсаа ашиглаж чадахгүй болвол би уурлана.",
    "Утасны цэнэг дуусахаас айж эмээдэг.",
    "Тооцоолох аргагүйгээр гэнэт дуусах нь намайг бухимдуулдаг.",
    "Сүлжээ түр хугацаанд тасрах юмуу Wi-Fi сүлжээнд нэвтэрч чадахгүй үедээ байнга шалгадаг.",
    "Хэрвээ өөрийнхөө гар утсыг хэрэглэж чадахгүй бол хэцүү бэрхшээлтэй санагддаг.",
    "Утсаа харж чадахгүй цаг үе тохиолдвол, утсаа харах юмсан гэсэн хүсэл байнга төрдөг.",
    "## Хэрвээ би утсаа мартсан бол:",
    "Гэр бүлийнхэн эсвэл найз нөхөдтэйгөө холбогдож чадахгүйдээ сандарч тэвддэг.",
    "Найз нөхөд, гэр бүлийнхэн маань надтай холбогдож чадахгүй тул сандрана.",
    "Дуудлага, мессеж хүлээн авч чадахгүй тул уурлаж унтууцна.",
    "Дэлхий ертөнцтэй тогтоосон байнгын холбоо хэлхээ тасарчих вий гэж түгшиж айна.",
    "Онлайн орчинд байж чадахгүй нь гэдэгтээ уурлаж бухимдана.",
    "Нийгмийн сүлжээнд холбоотой байж чадахгүй учраас тааламжгүй, хэцүү санагдана.",
    "Цахим хаягаа шалгаж чадахгүй болох тул намайг бухимдахад хүргэнэ.",
    "Юу хийхээ мэдэхгүй болох учраас хачин жигтэй санагдана.",
]
DEFAULT_QUIZ = {
    "id": "phone", "title": "Гар утасны хэрэглээний сорил",
    "description": "17 өгүүлбэрт “Тийм” эсвэл “Үгүй” гэж хариулна.",
    "price": PRICE, "status": "published",
    "items": [{"s": t[3:]} if t.startswith("## ") else {"q": t} for t in _PHONE_ITEMS],
    "options": [{"label": "Тийм", "points": 1}, {"label": "Үгүй", "points": 0}],
    "bands": [
        {"max": 5, "label": "Хамааралгүй (гар утасны хамаарал байхгүй)",
         "guidance": "Утастай холбоотой түгшүүр бага байна."},
        {"max": 10, "label": "Дунд зэргийн хамааралтай",
         "guidance": "Зарим нөхцөлд утас таны сэтгэл санаанд нөлөөлдөг байж болзошгүй."},
        {"max": 15, "label": "Нэлээд хамааралтай",
         "guidance": "Утасгүй үед мэдэгдэхүйц эвгүй байдал мэдэрдэг бололтой. Сэтгэл зүйчтэй ярилцах нь тус болно."},
        {"max": 17, "label": "Их хамааралтай (анхаарах шаардлагатай)",
         "guidance": "Утасгүй үед хүчтэй түгшүүр мэдэрдэг бололтой. Сэтгэл зүйчтэй уулзахыг зөвлөж байна."},
    ],
}

DB = sqlite3.connect(DB_PATH, check_same_thread=False)
LOCK = threading.Lock()
with LOCK:
    DB.executescript("""
    CREATE TABLE IF NOT EXISTS tests(
      id TEXT PRIMARY KEY, created REAL, age INTEGER, gender TEXT, phone TEXT,
      score INTEGER, result TEXT, invoice_id TEXT, status TEXT, demo_paid INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS bookings(
      id TEXT PRIMARY KEY, created REAL, name TEXT, phone TEXT, service TEXT, slot TEXT);
    CREATE TABLE IF NOT EXISTS users(
      key TEXT PRIMARY KEY, name TEXT, pw TEXT, created REAL);
    CREATE TABLE IF NOT EXISTS articles(
      id TEXT PRIMARY KEY, created REAL, updated REAL, title TEXT, category TEXT, body TEXT, status TEXT);
    CREATE TABLE IF NOT EXISTS quizzes(
      id TEXT PRIMARY KEY, created REAL, updated REAL, title TEXT, description TEXT, price INTEGER,
      status TEXT, items TEXT, options TEXT, bands TEXT);
    CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE IF NOT EXISTS menu(id TEXT PRIMARY KEY, label TEXT, target TEXT, sort INTEGER, visible INTEGER);
    """)
    def _ensure_cols(table, cols):  # хуучин data.sqlite3 файлыг эвдэлгүй шинэчилнэ
        have = {r[1] for r in DB.execute(f"PRAGMA table_info({table})")}
        for c, t in cols.items():
            if c not in have:
                DB.execute(f"ALTER TABLE {table} ADD COLUMN {c} {t}")
    _ensure_cols("tests", {"guardian_name": "TEXT", "guardian_phone": "TEXT",
                           "guardian_consent": "INTEGER DEFAULT 0", "contract_ver": "TEXT", "contract_at": "REAL",
                           "quiz_id": "TEXT", "quiz_title": "TEXT", "max_score": "INTEGER", "guidance": "TEXT",
                           "price": "INTEGER"})
    _ensure_cols("bookings", {"age": "INTEGER", "mode": "TEXT", "guardian_name": "TEXT", "guardian_phone": "TEXT",
                              "guardian_consent": "INTEGER DEFAULT 0", "contract_ver": "TEXT", "contract_at": "REAL"})
    DB.commit()


def q(sql, args=(), one=False, commit=False):
    with LOCK:
        cur = DB.execute(sql, args)
        if commit:
            DB.commit()
        rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


# ---------- Сорил, нийтлэл (самбараас гараар удирдана) ----------
QCOLS = ["id", "created", "updated", "title", "description", "price", "status", "items", "options", "bands"]
ACOLS = ["id", "created", "updated", "title", "category", "body", "status"]
QC, AC = ",".join(QCOLS), ",".join(ACOLS)


def quiz_row(r):
    d = dict(zip(QCOLS, r))
    for k in ("items", "options", "bands"):
        d[k] = json.loads(d[k])
    return d


def get_quiz(qid, published_only=False):
    r = q(f"SELECT {QC} FROM quizzes WHERE id=?" + (" AND status='published'" if published_only else ""),
          (qid,), one=True)
    return quiz_row(r) if r else None


def n_questions(items):
    return sum(1 for i in items if "q" in i)


def top_score(quiz):
    return n_questions(quiz["items"]) * max(o["points"] for o in quiz["options"])


def validate_quiz(d):
    """(алдаа, цэвэрлэсэн сорил). Самбараас ирсэн өгөгдлийг бүрэн шалгана."""
    title = norm_name(d.get("title"))
    if not 2 <= len(title) <= 120:
        return "Сорилын нэр 2–120 тэмдэгттэй байх ёстой.", None
    try:
        price = int(d.get("price"))
    except (TypeError, ValueError):
        return "Үнийг тоогоор оруулна уу (үнэгүй бол 0).", None
    if not 0 <= price <= 1_000_000:
        return "Үнэ 0–1,000,000₮ хооронд байх ёстой.", None
    if d.get("status") not in ("draft", "published"):
        return "Төлөвийг сонгоно уу.", None
    items = []
    for it in d.get("items") or []:
        if not isinstance(it, dict):
            return "Асуултын жагсаалт буруу байна.", None
        if "s" in it and str(it["s"]).strip():
            items.append({"s": str(it["s"]).strip()[:150]})
        elif "q" in it and str(it["q"]).strip():
            items.append({"q": str(it["q"]).strip()[:500]})
    nq = n_questions(items)
    if not 1 <= nq <= 100:
        return "Асуулт 1–100 хооронд байх ёстой.", None
    opts = []
    for o in d.get("options") or []:
        try:
            label, pts = str(o.get("label", "")).strip()[:60], int(o.get("points"))
        except (TypeError, ValueError, AttributeError):
            return "Хариултын сонголт буруу байна. Жишээ: Тийм = 1", None
        if not label or not 0 <= pts <= 10:
            return "Хариултын сонголт бүр нэртэй, оноо нь 0–10 байх ёстой.", None
        opts.append({"label": label, "points": pts})
    if not 2 <= len(opts) <= 7:
        return "Хариултын сонголт 2–7 байх ёстой.", None
    bands = []
    for b in d.get("bands") or []:
        try:
            mx, label = int(b.get("max")), str(b.get("label", "")).strip()[:200]
        except (TypeError, ValueError, AttributeError):
            return "Онооны зэрэглэл буруу байна. Жишээ: 5 | Хамааралгүй | тайлбар", None
        if not label or mx < 0:
            return "Зэрэглэл бүр дээд оноо болон нэртэй байх ёстой.", None
        bands.append({"max": mx, "label": label, "guidance": str(b.get("guidance", "")).strip()[:600]})
    bands.sort(key=lambda b: b["max"])
    if not bands or len({b["max"] for b in bands}) != len(bands):
        return "Онооны зэрэглэл дор хаяж нэг байх ёстой, дээд оноонууд давхцаж болохгүй.", None
    quiz = {"title": title, "description": str(d.get("description") or "").strip()[:500], "price": price,
            "status": d["status"], "items": items, "options": opts, "bands": bands}
    top = top_score(quiz)
    if bands[-1]["max"] < top:
        return f"Хамгийн сүүлийн зэрэглэлийн дээд оноо {top}-аас багагүй байх ёстой (боломжит дээд оноо: {top}).", None
    return None, quiz


def save_quiz(qid, quiz):
    now = time.time()
    q("INSERT INTO quizzes(id,created,updated,title,description,price,status,items,options,bands) "
      "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated, title=excluded.title, "
      "description=excluded.description, price=excluded.price, status=excluded.status, items=excluded.items, "
      "options=excluded.options, bands=excluded.bands",
      (qid, now, now, quiz["title"], quiz["description"], quiz["price"], quiz["status"],
       json.dumps(quiz["items"], ensure_ascii=False), json.dumps(quiz["options"], ensure_ascii=False),
       json.dumps(quiz["bands"], ensure_ascii=False)), commit=True)


def validate_article(d):
    title = norm_name(d.get("title"))
    body = str(d.get("body") or "").replace("\r\n", "\n").strip()
    if not 2 <= len(title) <= 150:
        return "Гарчиг 2–150 тэмдэгттэй байх ёстой.", None
    if not 1 <= len(body) <= 20000:
        return "Агуулга 1–20,000 тэмдэгттэй байх ёстой.", None
    if d.get("status") not in ("draft", "published"):
        return "Төлөвийг сонгоно уу.", None
    return None, {"title": title, "category": norm_name(d.get("category"))[:40], "body": body, "status": d["status"]}


MCOLS = ["id", "label", "target", "sort", "visible"]
# Цэсийн холбоос: хуудасны хэсэг, нийтлэл, гадны http(s) холбоос, эсвэл нэвтрэх хуудас. javascript: гэх мэтийг хүлээж авахгүй.
MENU_TARGET = re.compile(r"#(?:services|test|news|booking|about|privacy|faq)|article:\w{1,32}|"
                         r"https?://[^\s<>\"']{3,300}|/psychologist-admin")
DEFAULT_MENU = [("Үйлчилгээ", "#services"), ("Сорил", "#test"), ("Мэдээ", "#news"),
                ("Нууцлал", "#privacy"), ("Асуулт", "#faq"), ("Нэвтрэх", "/psychologist-admin")]


def menu_rows():
    rows = q(f"SELECT {','.join(MCOLS)} FROM menu ORDER BY sort, id")
    return [dict(zip(MCOLS, r), visible=bool(r[4])) for r in rows]


def validate_menu(d):
    label = norm_name(d.get("label"))
    target = str(d.get("target") or "").strip()
    if not 1 <= len(label) <= 30:
        return "Цэсийн нэр 1–30 тэмдэгттэй байх ёстой.", None
    if not MENU_TARGET.fullmatch(target):
        return "Холбоос буруу байна.", None
    if target.startswith("article:") and not q("SELECT 1 FROM articles WHERE id=?", (target[8:],), one=True):
        return "Сонгосон нийтлэл олдсонгүй.", None
    return None, {"label": label, "target": target, "visible": 1 if d.get("visible") else 0}


def seed_default_menu():
    if q("SELECT 1 FROM meta WHERE k='menu_seeded'", one=True):
        return
    if not q("SELECT 1 FROM menu LIMIT 1", one=True):
        for i, (label, target) in enumerate(DEFAULT_MENU):
            q("INSERT INTO menu VALUES(?,?,?,?,1)", (secrets.token_hex(4), label, target, i), commit=True)
    q("INSERT OR REPLACE INTO meta VALUES('menu_seeded','1')", commit=True)


def seed_default_quiz():
    if q("SELECT 1 FROM meta WHERE k='seeded'", one=True):
        return
    if not q("SELECT 1 FROM quizzes LIMIT 1", one=True):
        err, quiz = validate_quiz(DEFAULT_QUIZ)
        assert not err, err
        save_quiz(DEFAULT_QUIZ["id"], quiz)
    q("INSERT OR REPLACE INTO meta VALUES('seeded','1')", commit=True)


# ---------- Сэтгэл зүйчийн нэвтрэлт (нэр + нууц үг, сервер талын session) ----------
PBKDF2_ITERS = 310_000
SESSIONS = {}   # token -> (key, name, expires)
FAILS = {}      # (ip, key) -> [цагууд]
SLOCK = threading.Lock()
MAX_FAILS, FAIL_WINDOW = 5, 600


def norm_name(s):
    return unicodedata.normalize("NFC", str(s or "")).strip()


def hash_pw(password, salt=None, iters=PBKDF2_ITERS):
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", unicodedata.normalize("NFC", password).encode(), salt, iters)
    return f"pbkdf2${iters}${salt.hex()}${dk.hex()}"


def check_pw(password, stored):
    try:
        _, iters, salt, dk = stored.split("$")
        got = hash_pw(password, bytes.fromhex(salt), int(iters)).split("$")[3]
        return hmac.compare_digest(got, dk)
    except Exception:
        return False


DUMMY_HASH = hash_pw("dummy-password-for-timing")


def create_user(name, password):
    name = norm_name(name)
    if not 3 <= len(name) <= 40:
        raise ValueError("Нэр 3–40 тэмдэгттэй байх ёстой")
    if len(password) < 8:
        raise ValueError("Нууц үг дор хаяж 8 тэмдэгттэй байх ёстой")
    q("INSERT INTO users(key,name,pw,created) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET name=excluded.name, pw=excluded.pw",
      (name.casefold(), name, hash_pw(password), time.time()), commit=True)
    with SLOCK:  # нууц үг солигдсон бол хуучин session-үүдийг хүчингүй болгоно
        for t in [t for t, v in SESSIONS.items() if v[0] == name.casefold()]:
            del SESSIONS[t]


def users_exist():
    return bool(q("SELECT 1 FROM users LIMIT 1", one=True))


def new_session(key, name):
    tok = secrets.token_urlsafe(32)
    with SLOCK:
        now = time.time()
        for t in [t for t, v in SESSIONS.items() if v[2] < now]:
            del SESSIONS[t]
        SESSIONS[tok] = (key, name, now + SESSION_SECONDS)
    return tok


def session_user(tok):
    with SLOCK:
        v = SESSIONS.get(tok or "")
        if v and v[2] > time.time():
            return v[0], v[1]
        SESSIONS.pop(tok or "", None)
    return None


seed_default_quiz()
seed_default_menu()


# ---------- Төлбөрийн adapter ----------
class DemoProvider:
    def create_invoice(self, ref, amount):
        return {"invoiceId": "demo-" + ref, "qrText": f"DEMO-PAY:{ref}:{amount}", "deeplinks": []}

    def is_paid(self, row):
        return bool(row["demo_paid"])


BANK_ACCOUNT = os.environ.get("BANK_ACCOUNT", "MN160005005156052954")
BANK_NAME = os.environ.get("BANK_NAME", "")          # жишээ: Хаан банк (заавал биш)
ACCOUNT_NAME = os.environ.get("ACCOUNT_NAME", "")    # данс эзэмшигчийн нэр (заавал биш)


class ManualProvider(DemoProvider):
    """Дансны шилжүүлэг: гүйлгээний утга = утасны дугаар. Сэтгэл зүйч админ хуудаснаас гараар баталгаажуулна."""

    def create_invoice(self, ref, amount):
        return {"invoiceId": "manual-" + ref, "account": BANK_ACCOUNT, "bankName": BANK_NAME,
                "accountName": ACCOUNT_NAME, "deeplinks": []}


class QPayProvider:
    """QPay v2 — endpoint-уудыг өөрийн QPay гэрээний баримттай тулгана уу."""
    base = os.environ.get("QPAY_BASE", "https://merchant.qpay.mn/v2")
    token, exp = None, 0

    def _call(self, path, body=None, basic=None):
        headers = {"Content-Type": "application/json",
                   "Authorization": ("Basic " + basic) if basic else ("Bearer " + self._auth())}
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    def _auth(self):
        if self.token and time.time() < self.exp:
            return self.token
        basic = base64.b64encode(
            f"{os.environ['QPAY_USERNAME']}:{os.environ['QPAY_PASSWORD']}".encode()).decode()
        j = self._call("/auth/token", basic=basic)
        self.token = j["access_token"]
        self.exp = time.time() + max(60, j.get("expires_in", 3600) - 60)
        return self.token

    def create_invoice(self, ref, amount):
        j = self._call("/invoice", {
            "invoice_code": os.environ["QPAY_INVOICE_CODE"], "sender_invoice_no": ref,
            "invoice_receiver_code": "terminal", "invoice_description": f"Сорил {ref}",
            "amount": amount,
            "callback_url": f"{os.environ.get('PUBLIC_URL', '')}/api/qpay/callback?ref={ref}"})
        return {"invoiceId": j["invoice_id"], "qrText": j.get("qr_text"),
                "qrImage": j.get("qr_image"), "deeplinks": j.get("urls", [])}

    def is_paid(self, row):
        try:
            j = self._call("/payment/check", {
                "object_type": "INVOICE", "object_id": row["invoice_id"],
                "offset": {"page_number": 1, "page_limit": 100}})
        except Exception:
            return False
        return (any(p.get("payment_status") == "PAID" for p in j.get("rows", []))
                and j.get("paid_amount", 0) >= (row.get("price") or PRICE))


provider = {"qpay": QPayProvider, "manual": ManualProvider}.get(PROVIDER, DemoProvider)()
COLS = ["id", "created", "age", "gender", "phone", "score", "result", "invoice_id", "status", "demo_paid",
        "guardian_name", "guardian_phone", "guardian_consent", "contract_ver", "contract_at",
        "quiz_id", "quiz_title", "max_score", "guidance", "price"]
BCOLS = ["id", "created", "name", "phone", "service", "slot", "age", "mode",
         "guardian_name", "guardian_phone", "guardian_consent", "contract_ver", "contract_at"]
CONTRACT_VERSION = "2026-10"   # гэрээний текст өөрчлөгдөх бүрд шинэчилнэ
MINOR_MAX = 18                 # 0–18 нас: асран хамгаалагчийн зөвшөөрөл заавал


def guardian_info(d, age):
    """(алдаа, нэр, утас, зөвшөөрөл). 18 ба түүнээс доош насанд асран хамгаалагч шаардана."""
    if age > MINOR_MAX:
        return None, None, None, 0
    gname = norm_name(d.get("guardianName"))[:80]
    gphone = str(d.get("guardianPhone") or "")
    if len(gname) < 2 or not re.fullmatch(r"\d{8}", gphone) or d.get("guardianConsent") is not True:
        return ("18 ба түүнээс доош насны хүний хувьд асран хамгаалагчийн нэр, 8 оронтой утас болон "
                "зөвшөөрөл шаардлагатай"), None, None, 0
    return None, gname, gphone, 1


def get_test(tid):
    r = q(f"SELECT {','.join(COLS)} FROM tests WHERE id=?", (tid,), one=True)
    return dict(zip(COLS, r)) if r else None


# ---------- HTTP ----------
class Handler(BaseHTTPRequestHandler):
    server_version = "psy/1.0"

    def log_message(self, fmt, *a):
        print("%s %s" % (self.command, urlparse(self.path).path))  # query/phone-ийг лог руу бичихгүй

    def send_json(self, code, obj, headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self, limit=20_000):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def cookie_token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "psy_session":
                return v
        return None

    def is_admin(self):
        """Нэвтэрсэн сэтгэл зүйчийн нэрийг буцаана, үгүй бол None.
        POST хүсэлт дээр CSRF-ээс сэргийлж X-Requested-With толгой шаардана."""
        if self.command == "POST" and self.headers.get("X-Requested-With") != "psy":
            return None
        u = session_user(self.cookie_token())
        return u[1] if u else None

    def cookie_header(self, token, max_age):
        c = f"psy_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}"
        return {"Set-Cookie": c + ("; Secure" if COOKIE_SECURE else "")}

    def admin_login(self):
        d = self.body()
        name, pw = norm_name(d.get("username")), str(d.get("password") or "")
        key = name.casefold()
        ip = self.client_address[0]
        now = time.time()
        with SLOCK:
            recent = [t for t in FAILS.get((ip, key), []) if now - t < FAIL_WINDOW]
            FAILS[(ip, key)] = recent
            if len(recent) >= MAX_FAILS:
                return self.send_json(429, {"error": "Хэт олон удаа буруу оруулсан. 10 минутын дараа дахин оролдоно уу."})
        row = q("SELECT name, pw FROM users WHERE key=?", (key,), one=True)
        ok = check_pw(pw, row[1] if row else DUMMY_HASH) and row is not None
        if not ok:
            with SLOCK:
                FAILS.setdefault((ip, key), []).append(now)
            return self.send_json(401, {"error": "Нэр эсвэл нууц үг буруу байна."})
        with SLOCK:
            FAILS.pop((ip, key), None)
        tok = new_session(key, row[0])
        self.send_json(200, {"ok": True, "name": row[0]}, self.cookie_header(tok, SESSION_SECONDS))

    def admin_password(self, user):
        d = self.body()
        old, new = str(d.get("old") or ""), str(d.get("new") or "")
        key = session_user(self.cookie_token())[0]
        row = q("SELECT pw FROM users WHERE key=?", (key,), one=True)
        if not row or not check_pw(old, row[0]):
            return self.send_json(400, {"error": "Одоогийн нууц үг буруу байна."})
        try:
            create_user(user, new)
        except ValueError as e:
            return self.send_json(400, {"error": str(e)})
        tok = new_session(key, user)
        self.send_json(200, {"ok": True}, self.cookie_header(tok, SESSION_SECONDS))

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/config":
                return self.send_json(200, {"price": PRICE, "provider": PROVIDER,
                                            "emergency": EMERGENCY_TEXT})
            m = re.fullmatch(r"/api/test/(\w+)", path)
            if m:
                return self.get_result(m.group(1))
            if path == "/api/qpay/callback":
                return self.send_json(200, {"ok": True})  # төлөвийг result дээр дахин шалгадаг
            if path == "/api/admin/me":
                u = self.is_admin()
                return self.send_json(200, {"name": u, "setup": not users_exist()})
            if path == "/api/admin/data":
                if not self.is_admin():
                    return self.send_json(401, {"error": "Нэвтрэх эрхгүй"})
                tests = [dict(zip(COLS, r)) for r in q(
                    f"SELECT {','.join(COLS)} FROM tests WHERE status='paid' ORDER BY created DESC LIMIT 500")]
                for t in tests:
                    t.pop("demo_paid", None)
                book = [dict(zip(BCOLS, r)) for r in q(
                    f"SELECT {','.join(BCOLS)} FROM bookings ORDER BY created DESC LIMIT 500")]
                pending = [dict(zip(COLS, r)) for r in q(
                    f"SELECT {','.join(COLS)} FROM tests WHERE status='pending' ORDER BY created DESC LIMIT 200")]
                pending = [{"id": t["id"], "ref": t["phone"], "created": t["created"], "age": t["age"],
                            "gender": t["gender"], "phone": t["phone"], "quiz_title": t["quiz_title"],
                            "price": t["price"] or PRICE,
                            "guardian_name": t["guardian_name"], "guardian_phone": t["guardian_phone"]} for t in pending]
                articles = [dict(zip(ACOLS, r)) for r in q(f"SELECT {AC} FROM articles ORDER BY created DESC")]
                quizzes = [quiz_row(r) for r in q(f"SELECT {QC} FROM quizzes ORDER BY created")]
                return self.send_json(200, {"tests": tests, "bookings": book, "pending": pending,
                                            "articles": articles, "quizzes": quizzes, "menu": menu_rows(),
                                            "manual": PROVIDER == "manual", "price": PRICE})
            if path == "/api/menu":
                live = {r[0] for r in q("SELECT id FROM articles WHERE status='published'")}
                items = [{"id": m["id"], "label": m["label"], "target": m["target"]} for m in menu_rows()
                         if m["visible"] and (not m["target"].startswith("article:") or m["target"][8:] in live)]
                return self.send_json(200, {"menu": items})
            if path == "/api/quizzes":
                rows = [quiz_row(r) for r in q(f"SELECT {QC} FROM quizzes WHERE status='published' ORDER BY created")]
                return self.send_json(200, {"quizzes": [
                    {"id": x["id"], "title": x["title"], "description": x["description"], "price": x["price"],
                     "count": n_questions(x["items"])} for x in rows]})
            m = re.fullmatch(r"/api/quiz/(\w+)", path)
            if m:
                x = get_quiz(m.group(1), published_only=True)
                if not x:
                    return self.send_json(404, {"error": "Сорил олдсонгүй"})
                return self.send_json(200, {"id": x["id"], "title": x["title"], "description": x["description"],
                                            "price": x["price"], "items": x["items"],
                                            "options": [o["label"] for o in x["options"]]})  # оноог илгээхгүй
            if path == "/api/articles":
                rows = [dict(zip(ACOLS, r)) for r in q(
                    f"SELECT {AC} FROM articles WHERE status='published' ORDER BY created DESC LIMIT 100")]
                return self.send_json(200, {"articles": [
                    {"id": a["id"], "title": a["title"], "category": a["category"], "created": a["created"],
                     "excerpt": a["body"][:160]} for a in rows]})
            m = re.fullmatch(r"/api/article/(\w+)", path)
            if m:
                r = q(f"SELECT {AC} FROM articles WHERE id=? AND status='published'", (m.group(1),), one=True)
                if not r:
                    return self.send_json(404, {"error": "Нийтлэл олдсонгүй"})
                return self.send_json(200, dict(zip(ACOLS, r)))
            self.static(path)
        except Exception as e:
            print("ERR", repr(e))
            self.send_json(500, {"error": "Серверийн алдаа"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/test":
                return self.create_test()
            if path == "/api/book":
                return self.create_booking()
            if path == "/api/admin/login":
                if self.headers.get("X-Requested-With") != "psy":
                    return self.send_json(400, {"error": "Буруу хүсэлт"})
                return self.admin_login()
            if path == "/api/admin/logout":
                tok = self.cookie_token()
                with SLOCK:
                    SESSIONS.pop(tok or "", None)
                return self.send_json(200, {"ok": True}, self.cookie_header("", 0))
            if path == "/api/admin/password":
                u = self.is_admin()
                if not u:
                    return self.send_json(401, {"error": "Нэвтрэх эрхгүй"})
                return self.admin_password(u)
            if path == "/api/admin/delete":  # мэдээлэл устгуулах эрх (нууцлал)
                if not self.is_admin():
                    return self.send_json(401, {"error": "Нэвтрэх эрхгүй"})
                d = self.body()
                table = {"test": "tests", "booking": "bookings", "article": "articles",
                         "quiz": "quizzes", "menu": "menu"}.get(d.get("kind"))
                rid = str(d.get("id") or "")
                if not table or not re.fullmatch(r"\w+", rid):
                    return self.send_json(400, {"error": "Буруу хүсэлт"})
                q(f"DELETE FROM {table} WHERE id=?", (rid,), commit=True)
                return self.send_json(200, {"ok": True})
            if path == "/api/admin/menu/save":
                if not self.is_admin():
                    return self.send_json(401, {"error": "Нэвтрэх эрхгүй"})
                d = self.body()
                err, clean = validate_menu(d)
                if err:
                    return self.send_json(400, {"error": err})
                rid = str(d.get("id") or "")
                if rid and not re.fullmatch(r"\w+", rid):
                    return self.send_json(400, {"error": "Буруу хүсэлт"})
                if rid and q("SELECT 1 FROM menu WHERE id=?", (rid,), one=True):
                    q("UPDATE menu SET label=?, target=?, visible=? WHERE id=?",
                      (clean["label"], clean["target"], clean["visible"], rid), commit=True)
                else:
                    last = q("SELECT COALESCE(MAX(sort),-1) FROM menu", one=True)[0]
                    rid = secrets.token_hex(4)
                    q("INSERT INTO menu VALUES(?,?,?,?,?)",
                      (rid, clean["label"], clean["target"], last + 1, clean["visible"]), commit=True)
                return self.send_json(200, {"ok": True, "id": rid})
            if path == "/api/admin/menu/move":
                if not self.is_admin():
                    return self.send_json(401, {"error": "Нэвтрэх эрхгүй"})
                d = self.body()
                ids = [m["id"] for m in menu_rows()]
                rid, step = str(d.get("id") or ""), (-1 if d.get("dir") == "up" else 1)
                if rid not in ids:
                    return self.send_json(404, {"error": "Олдсонгүй"})
                i = ids.index(rid); j = i + step
                if 0 <= j < len(ids):
                    ids[i], ids[j] = ids[j], ids[i]
                    for pos, mid in enumerate(ids):
                        q("UPDATE menu SET sort=? WHERE id=?", (pos, mid))
                    q("SELECT 1", commit=True)
                return self.send_json(200, {"ok": True})
            if path in ("/api/admin/article/save", "/api/admin/quiz/save"):
                if not self.is_admin():
                    return self.send_json(401, {"error": "Нэвтрэх эрхгүй"})
                d = self.body(limit=400_000)
                rid = str(d.get("id") or "")
                if rid and not re.fullmatch(r"\w+", rid):
                    return self.send_json(400, {"error": "Буруу хүсэлт"})
                is_quiz = path.endswith("/quiz/save")
                err, clean = (validate_quiz if is_quiz else validate_article)(d)
                if err:
                    return self.send_json(400, {"error": err})
                rid = rid or secrets.token_hex(4)
                if is_quiz:
                    save_quiz(rid, clean)
                else:
                    now = time.time()
                    q("INSERT INTO articles(id,created,updated,title,category,body,status) VALUES(?,?,?,?,?,?,?) "
                      "ON CONFLICT(id) DO UPDATE SET updated=excluded.updated, title=excluded.title, "
                      "category=excluded.category, body=excluded.body, status=excluded.status",
                      (rid, now, now, clean["title"], clean["category"], clean["body"], clean["status"]), commit=True)
                return self.send_json(200, {"ok": True, "id": rid})
            if path == "/api/admin/confirm":
                if not self.is_admin() or PROVIDER != "manual":
                    return self.send_json(401, {"error": "Нэвтрэх эрхгүй"})
                tid = str(self.body().get("id") or "")
                if not re.fullmatch(r"\w+", tid) or not get_test(tid):
                    return self.send_json(404, {"error": "Олдсонгүй"})
                q("UPDATE tests SET demo_paid=1 WHERE id=?", (tid,), commit=True)
                return self.send_json(200, {"ok": True})
            m = re.fullmatch(r"/api/test/(\w+)/demo-pay", path)
            if m:
                if PROVIDER != "demo":
                    return self.send_json(403, {"error": "Зөвхөн demo горимд"})
                if not get_test(m.group(1)):
                    return self.send_json(404, {"error": "Олдсонгүй"})
                q("UPDATE tests SET demo_paid=1 WHERE id=?", (m.group(1),), commit=True)
                return self.send_json(200, {"ok": True})
            self.send_json(404, {"error": "Олдсонгүй"})
        except ValueError:
            self.send_json(400, {"error": "Буруу хүсэлт"})
        except Exception as e:
            print("ERR", repr(e))
            self.send_json(500, {"error": "Серверийн алдаа"})

    def create_test(self):
        d = self.body()
        ans = d.get("answers")
        if d.get("consent") is not True:
            return self.send_json(400, {"error": "Мэдээлэл боловсруулахыг зөвшөөрнө үү"})
        quiz = get_quiz(str(d.get("quizId") or ""), published_only=True)
        if not quiz:
            return self.send_json(404, {"error": "Сорил олдсонгүй. Хуудсаа шинэчилнэ үү."})
        nq, nopt = n_questions(quiz["items"]), len(quiz["options"])
        if not (isinstance(ans, list) and len(ans) == nq and all(type(a) is int and 0 <= a < nopt for a in ans)):
            return self.send_json(400, {"error": "Бүх асуултад хариулна уу"})
        try:
            age = int(d.get("age"))
        except (TypeError, ValueError):
            age = 0
        phone = str(d.get("phone") or "")
        gender = d.get("gender")
        if not 5 <= age <= 110:
            return self.send_json(400, {"error": "Насаа зөв оруулна уу"})
        if gender not in ("Эрэгтэй", "Эмэгтэй", "Бусад"):
            return self.send_json(400, {"error": "Хүйсээ сонгоно уу"})
        if not re.fullmatch(r"\d{8}", phone):
            return self.send_json(400, {"error": "Утасны дугаар 8 оронтой байх ёстой"})
        err, gname, gphone, gcons = guardian_info(d, age)
        if err:
            return self.send_json(400, {"error": err})
        score = sum(quiz["options"][a]["points"] for a in ans)  # оноог СЕРВЕР тооцно
        bnd = next((b for b in quiz["bands"] if score <= b["max"]), quiz["bands"][-1])
        price = quiz["price"]
        tid = secrets.token_urlsafe(12).replace("-", "a").replace("_", "b")
        free = price == 0
        inv = {"invoiceId": "", "deeplinks": []} if free else provider.create_invoice(tid, price)
        if PROVIDER == "manual" and not free:
            inv["reference"] = phone  # гүйлгээний утга = утасны дугаар
        now = time.time()
        q("INSERT INTO tests(id,created,age,gender,phone,score,result,invoice_id,status,"
          "guardian_name,guardian_phone,guardian_consent,contract_ver,contract_at,"
          "quiz_id,quiz_title,max_score,guidance,price) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (tid, now, age, gender, phone, score, bnd["label"], inv["invoiceId"], "paid" if free else "pending",
           gname, gphone, gcons, CONTRACT_VERSION, now,
           quiz["id"], quiz["title"], top_score(quiz), bnd["guidance"], price), commit=True)
        self.send_json(201, {"id": tid, "amount": price, "free": free, "invoice": inv})

    def get_result(self, tid):
        t = get_test(tid)
        if not t:
            return self.send_json(404, {"error": "Олдсонгүй"})
        if t["status"] == "pending" and provider.is_paid(t):
            q("UPDATE tests SET status='paid' WHERE id=?", (tid,), commit=True)
            t["status"] = "paid"
        if t["status"] != "paid":
            return self.send_json(200, {"status": "pending"})
        self.send_json(200, {"status": "paid", "score": t["score"], "max": t["max_score"] or 17,
                             "result": t["result"], "guidance": t["guidance"] or ""})

    def create_booking(self):
        d = self.body()
        name = str(d.get("name") or "").strip()[:80]
        phone = str(d.get("phone") or "")
        service = d.get("service")
        mode = d.get("mode")
        slot = str(d.get("date") or "")
        try:
            age = int(d.get("age"))
        except (TypeError, ValueError):
            age = 0
        if not name or not re.fullmatch(r"\d{8}", phone) or service not in ("individual", "family") \
                or mode not in ("video", "audio", "inperson") or not 1 <= age <= 110 \
                or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d", slot):
            return self.send_json(400, {"error": "Мэдээллээ зөв бөглөнө үү"})
        if d.get("contract") is not True:
            return self.send_json(400, {"error": "Үйлчилгээний нөхцөл, нууцлалын гэрээг зөвшөөрнө үү"})
        err, gname, gphone, gcons = guardian_info(d, age)
        if err:
            return self.send_json(400, {"error": err})
        now = time.time()
        q("INSERT INTO bookings(id,created,name,phone,service,slot,age,mode,guardian_name,guardian_phone,"
          "guardian_consent,contract_ver,contract_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
          (secrets.token_hex(4), now, name, phone, service, slot, age, mode, gname, gphone, gcons,
           CONTRACT_VERSION, now), commit=True)
        self.send_json(201, {"ok": True})

    def static(self, path):
        rel = {"/": "index.html", "/psychologist-admin": "admin.html"}.get(path, path.lstrip("/"))
        f = (PUBLIC / rel).resolve()
        if PUBLIC not in f.parents or not f.is_file():
            self.send_response(404)
            self.end_headers()
            return self.wfile.write(b"Not found")
        data = f.read_bytes()
        ct = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ct + ("; charset=utf-8" if ct.startswith("text") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def cli_add_user():
    print("Сэтгэл зүйчийн эрх үүсгэх")
    name = input("Нэр: ")
    pw = getpass.getpass("Нууц үг (8+ тэмдэгт, дэлгэцэнд харагдахгүй): ")
    if pw != getpass.getpass("Нууц үгээ давтана уу: "):
        sys.exit("Нууц үг таарсангүй.")
    try:
        create_user(name, pw)
    except ValueError as e:
        sys.exit(str(e))
    print(f"✓ '{norm_name(name)}' хадгалагдлаа. Одоо python server.py гэж сервер асаана уу.")


if __name__ == "__main__":
    if "--add-user" in sys.argv:
        cli_add_user()
        sys.exit(0)
    if PROVIDER == "manual":
        print("Дансны шилжүүлгийн горим, данс:", BANK_ACCOUNT)
    print(f"http://localhost:{PORT}  төлбөр={PROVIDER}  " +
          ("сэтгэл зүйч: бүртгэлтэй" if users_exist() else "АНХААР: сэтгэл зүйч үүсгээгүй байна → python server.py --add-user"))
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
