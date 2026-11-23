#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UzRailPass kuzatuvchi Telegram bot.

Hammasi tugmalar orqali: yo'nalish -> sana -> joy turi -> nechta bilet kerak.
Bot saytni davriy tekshiradi va faqat bitta holatda xabar yuboradi:
tanlangan sanada, tanlangan turdagi kamida kerakli sondagi bilet TOPILGANDA.

Faqat `requests` kerak. Ishga tushirish:  python bot.py
"""
import html
import json
import logging
import os
import random
import re
import sqlite3
import threading
import time
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent


def load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(HERE / ".env")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
POLL_INTERVAL = max(30, int(os.environ.get("POLL_INTERVAL", "60")))
API_BASES = [b.strip().rstrip("/") for b in os.environ.get(
    "API_BASES", "https://eticket.uzrailpass.uz,https://eticket.railway.uz").split(",") if b.strip()]
API_PATH = os.environ.get("API_PATH", "/api/v3/handbook/trains/list")
# Adminlar (siz): ruxsat so'rovlarini tasdiqlaydi. Chat ID ni @userinfobot dan bilib oling.
ADMINS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
# Oldindan tasdiqlangan foydalanuvchilar (ixtiyoriy). Qolganlar admin tasdig'ini kutadi.
ALLOWED = {int(x) for x in os.environ.get("ALLOWED_CHAT_IDS", "").split(",") if x.strip()}
DB_PATH = os.environ.get("DB_PATH", str(HERE / "bot.db"))
# Tarmoq: tizimdagi HTTP(S)_PROXY ishlamasa IGNORE_PROXY=1 qiling; kerak bo'lsa o'z proksingizni PROXY_URL ga yozing.
IGNORE_PROXY = os.environ.get("IGNORE_PROXY", "0") == "1"
PROXY_URL = os.environ.get("PROXY_URL", "").strip()
SITE_URL = "https://eticket.uzrailpass.uz/uz/home"
MAX_TICKETS = 9

STATIONS = json.loads((HERE / "stations.json").read_text(encoding="utf-8"))
CODE2NAME = {v: k for k, v in STATIONS.items()}

SEAT_LABELS = {
    "sit": "O'tirib ketadigan",
    "lower": "Pastki",
    "upper": "Tepa",
    "side_lower": "Yon pastki",
    "side_upper": "Yon tepa",
    "any": "Istalgan",
}

# Vagon turini aniqlash uchun kalit so'zlar (sayt javobini ko'rgach moslash mumkin)
BERTH_KW = ("plask", "plats", "plack", "kupe", "coupe", "купе", "плацк", "lyuks", "luks",
            "люкс", "yumshoq", "mjag", "мягк", "berth", "спальн")
SEATED_KW = ("o'tir", "o'rin", "sitting", "seat", "sid", "econom", "ekonom", "biznes", "business",
             "vip", "first", "comfort", "standart", "standard", "сидяч", "эконом", "бизнес")
HIGH_SPEED_KW = ("afrosiyob", "afrosiab", "sharq", "talgo", "nasaf", "registon", "manguberdi",
                 "mangu", "jaloliddin", "тальго", "афросиаб", "шарк", "насаф")

BTN_ADD = "➕ Yangi kuzatuv"
BTN_LIST = "📋 Kuzatuvlarim"
BTN_HELP = "ℹ️ Yordam"
BTN_USERS = "👥 Foydalanuvchilar"


def is_admin(chat):
    return chat in ADMINS


def menu_for(chat):
    rows = [[{"text": BTN_ADD}, {"text": BTN_LIST}], [{"text": BTN_HELP}]]
    if is_admin(chat):
        rows[1].append({"text": BTN_USERS})
    return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}

log = logging.getLogger("uzrail")


def make_session():
    s = requests.Session()
    if IGNORE_PROXY:
        s.trust_env = False
    if PROXY_URL:
        s.proxies = {"http": PROXY_URL, "https": PROXY_URL}
    return s


def mask(text):
    """Xato matnlaridan bot tokenini yashiradi (log fayllarga tushib qolmasligi uchun)."""
    text = str(text)
    return text.replace(BOT_TOKEN, "<TOKEN>") if BOT_TOKEN else text


# --------------------------------------------------------------------------
# Vagon va joy turini aniqlash
# --------------------------------------------------------------------------
def _norm(s):
    return str(s or "").lower().replace("’", "'").replace("ʻ", "'").replace("‘", "'").replace("`", "'")


def is_seated(car, train):
    """Vagon o'tirib ketadigan (o'rindiqli) turdami? Tezyurar poyezdlarda odatda shunday."""
    cname = " ".join(_norm(car.get(k)) for k in ("typeShow", "type", "className", "classShow", "name"))
    if re.search(r"\bsv\b", cname) or any(k in cname for k in BERTH_KW):
        return False
    if any(k in cname for k in SEATED_KW):
        return True
    tname = " ".join(_norm(train.get(k)) for k in ("type", "name", "brand", "category"))
    return any(k in tname for k in HIGH_SPEED_KW)


def _numbers(v):
    """Turli ko'rinishdagi joy raqamlarini (list, '1,2,5-7', [{'number':3}]) int ro'yxatiga aylantiradi."""
    out = []
    if v is None or isinstance(v, bool):
        return out
    if isinstance(v, int):
        return [v]
    if isinstance(v, str):
        for part in re.split(r"[,\s;]+", v):
            m = re.fullmatch(r"(\d+)-(\d+)", part)
            if m:
                out += range(int(m[1]), int(m[2]) + 1)
            elif part.isdigit():
                out.append(int(part))
        return out
    if isinstance(v, list):
        for x in v:
            if isinstance(x, dict):
                for key in ("number", "place", "seat", "num", "placeNumber"):
                    if key in x:
                        out += _numbers(x[key])
                        break
            else:
                out += _numbers(x)
    return out


SEAT_KEYS = ("freePlaces", "freePlace", "places", "seats", "placeNumbers",
             "freeSeatNumbers", "seatNumbers", "freeSeatsList")


def seat_kinds(car):
    """
    Bo'sh joylarni turlarga ajratadi (plaskart/kupe):
      toq raqam = pastki, juft = tepa; 37 va undan katta = yon (yo'lak yonidagi) joylar.
    Agar API joy raqamlarini bermasa -> None.
    """
    nums = []
    for k in SEAT_KEYS:
        if isinstance(car.get(k), (list, str)):
            nums = _numbers(car[k])
            if nums:
                break
    if not nums:
        for tf in car.get("tariffs") or []:
            if not isinstance(tf, dict):
                continue
            for k in SEAT_KEYS:
                if isinstance(tf.get(k), (list, str)):
                    nums += _numbers(tf[k])
    if not nums:
        return None
    kinds = {"lower": 0, "upper": 0, "side_lower": 0, "side_upper": 0}
    for n in sorted(set(nums)):
        side = n > 36
        lower = n % 2 == 1
        kinds[("side_" if side else "") + ("lower" if lower else "upper")] += 1
    return kinds


def kinds_text(kinds):
    if not kinds:
        return ""
    return ", ".join(f"{SEAT_LABELS[k].lower()} {v}" for k, v in kinds.items() if v)


TARIFF_NAME_KEYS = ("tariffName", "tariffShow", "name", "title", "typeShow", "type", "description")


def parse_tariffs(car):
    """Vagon tariflari: [{'name': 'Standart', 'price': 150000}, ...] (narx bo'yicha tartiblangan)."""
    out, seen = [], set()
    for tf in car.get("tariffs") or []:
        if not isinstance(tf, dict) or not isinstance(tf.get("tariff"), (int, float)) \
                or isinstance(tf.get("tariff"), bool):
            continue
        name = next((str(tf[k]) for k in TARIFF_NAME_KEYS
                     if isinstance(tf.get(k), str) and tf[k].strip()), "")
        item = (name, tf["tariff"])
        if item not in seen:
            seen.add(item)
            out.append({"name": name, "price": tf["tariff"]})
    return sorted(out, key=lambda x: x["price"])


def money(x):
    return f"{int(x):,}".replace(",", " ") + " so'm"


# --------------------------------------------------------------------------
# Snapshot va "bilet topildimi" mantiqi
# --------------------------------------------------------------------------
def snapshot(trains):
    snap = {}
    for t in trains or []:
        tkey = f"{t.get('number')}|{t.get('departureDate')}"
        cars = {}
        seen = defaultdict(int)
        for c in t.get("cars") or []:
            ctype = str(c.get("typeShow") or c.get("type") or "?")
            ident = c.get("number") or c.get("carNumber") or c.get("id")
            if ident is None:
                ident = seen[ctype]
            seen[ctype] += 1
            tariffs = parse_tariffs(c)
            seated = is_seated(c, t)
            cars[f"{ctype}#{ident}"] = {
                "type": ctype,
                "free": int(c.get("freeSeats") or 0),
                "seated": seated,
                "kinds": None if seated else seat_kinds(c),
                "tariffs": tariffs,
                "price": min((x["price"] for x in tariffs), default=None),
            }
        snap[tkey] = {
            "label": f"{t.get('type', '')} {t.get('number', '')}".strip(),
            "dep": t.get("departureDate"),
            "arr": t.get("arrivalDate"),
            "cars": cars,
        }
    return snap


def relevant(car, prefs, max_price=None):
    """Foydalanuvchi tanlagan turdagi (va narx chegarasiga to'g'ri keladigan) bo'sh joylar soni."""
    if max_price and car.get("price") and car["price"] > max_price:
        return 0
    if "any" in prefs:
        return car["free"]
    if car.get("seated"):
        return car["free"] if "sit" in prefs else 0
    berth = [p for p in prefs if p != "sit"]
    if not berth:
        return 0
    if not car.get("kinds"):  # joy raqamlari yo'q -> turini bilmaymiz, umumiy sonni olamiz
        return car["free"]
    return sum(car["kinds"].get(p, 0) for p in berth)


def qualifying(snap, prefs, count, max_price=None):
    """Kerakli turdagi bo'sh joylar jami `count` dan kam bo'lmagan poyezdlar."""
    out = {}
    for tkey, t in snap.items():
        cars = [(c, relevant(c, prefs, max_price)) for c in t["cars"].values()]
        cars = [(c, r) for c, r in cars if r > 0]
        total = sum(r for _, r in cars)
        if cars and total >= count:
            out[tkey] = {"train": t, "cars": cars, "total": total}
    return out


# --------------------------------------------------------------------------
# Sayt klienti
# --------------------------------------------------------------------------
class Railway:
    def __init__(self):
        self.s = make_session()
        self.base = None
        self.token = str(uuid.uuid4())
        self.lock = threading.Lock()

    @staticmethod
    def _ua():
        return ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

    def _prepare(self, base):
        """Bosh sahifadan cookie olib, XSRF tokenni sozlaydi (topilmasa o'zimiz yaratamiz)."""
        host = base.split("//", 1)[1]
        try:
            self.s.get(base + "/uz/home", timeout=20, headers={"User-Agent": self._ua()})
        except requests.RequestException:
            pass
        tok = self.s.cookies.get("XSRF-TOKEN")
        if tok:
            self.token = tok
        else:
            self.token = str(uuid.uuid4())
            self.s.cookies.set("XSRF-TOKEN", self.token, domain=host)

    def raw(self, dep, arv, day_iso):
        body = {"directions": {"forward": {"date": day_iso, "depStationCode": dep, "arvStationCode": arv}}}
        with self.lock:
            bases = [self.base] if self.base else API_BASES
            last_err = None
            for base in bases:
                for attempt in (1, 2):
                    try:
                        if attempt == 2 or not self.s.cookies.get("XSRF-TOKEN"):
                            self._prepare(base)
                        r = self.s.post(base + API_PATH, json=body, timeout=25, headers={
                            "Accept": "application/json",
                            "Accept-Language": "uz",
                            "Content-Type": "application/json",
                            "Origin": base,
                            "Referer": base + "/uz/home",
                            "User-Agent": self._ua(),
                            "X-XSRF-TOKEN": self.token,
                            "device-type": "BROWSER",
                        })
                        if r.status_code in (401, 403) and attempt == 1:
                            self.s.cookies.clear()
                            continue
                        r.raise_for_status()
                        self.base = base
                        return r.json()
                    except (requests.RequestException, ValueError) as e:
                        last_err = e
                        break
            raise RuntimeError(f"API javob bermadi: {last_err}")

    def trains(self, dep, arv, day_iso):
        data = self.raw(dep, arv, day_iso)
        try:
            return data["data"]["directions"]["forward"]["trains"] or []
        except (KeyError, TypeError):
            raise RuntimeError("Kutilmagan javob tuzilmasi (/debug bilan tekshiring)")


# --------------------------------------------------------------------------
# Ma'lumotlar bazasi
# --------------------------------------------------------------------------
class DB:
    def __init__(self, path):
        self.c = sqlite3.connect(path, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.c.execute("""CREATE TABLE IF NOT EXISTS subs(
                id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
                dep TEXT NOT NULL, arv TEXT NOT NULL, day TEXT NOT NULL,
                prefs TEXT NOT NULL, state TEXT)""")
            self.c.execute("""CREATE TABLE IF NOT EXISTS users(
                chat_id INTEGER PRIMARY KEY, name TEXT, username TEXT,
                status TEXT NOT NULL, created TEXT)""")
            for ddl in ("ALTER TABLE subs ADD COLUMN count INTEGER NOT NULL DEFAULT 1",
                        "ALTER TABLE subs ADD COLUMN max_price INTEGER"):
                try:
                    self.c.execute(ddl)
                except sqlite3.OperationalError:
                    pass
            self.c.commit()

    def add(self, chat_id, dep, arv, day, prefs, count, max_price=None):
        with self.lock:
            cur = self.c.execute(
                "INSERT INTO subs(chat_id,dep,arv,day,prefs,count,max_price) VALUES(?,?,?,?,?,?,?)",
                (chat_id, dep, arv, day, ",".join(sorted(prefs)), count, max_price))
            self.c.commit()
            return cur.lastrowid

    def all(self):
        with self.lock:
            return [dict(r) for r in self.c.execute("SELECT * FROM subs")]

    def of_chat(self, chat_id):
        with self.lock:
            return [dict(r) for r in self.c.execute("SELECT * FROM subs WHERE chat_id=?", (chat_id,))]

    def get(self, sid):
        with self.lock:
            r = self.c.execute("SELECT * FROM subs WHERE id=?", (sid,)).fetchone()
            return dict(r) if r else None

    def remove(self, sid, chat_id=None):
        with self.lock:
            q, a = "DELETE FROM subs WHERE id=?", [sid]
            if chat_id is not None:
                q += " AND chat_id=?"
                a.append(chat_id)
            n = self.c.execute(q, a).rowcount
            self.c.commit()
            return n

    # --- foydalanuvchilar (ruxsat) ---
    def user(self, chat_id):
        with self.lock:
            r = self.c.execute("SELECT * FROM users WHERE chat_id=?", (chat_id,)).fetchone()
            return dict(r) if r else None

    def users(self):
        with self.lock:
            return [dict(r) for r in self.c.execute(
                "SELECT * FROM users ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, created")]

    def add_user(self, chat_id, name, username, status="pending"):
        with self.lock:
            self.c.execute("INSERT OR REPLACE INTO users(chat_id,name,username,status,created) VALUES(?,?,?,?,?)",
                           (chat_id, name, username, status, datetime.now().isoformat(timespec="seconds")))
            self.c.commit()

    def set_status(self, chat_id, status):
        with self.lock:
            self.c.execute("UPDATE users SET status=? WHERE chat_id=?", (status, chat_id))
            self.c.commit()

    def remove_chat(self, chat_id):
        with self.lock:
            self.c.execute("DELETE FROM subs WHERE chat_id=?", (chat_id,))
            self.c.commit()

    def delete_user(self, chat_id):
        with self.lock:
            self.c.execute("DELETE FROM users WHERE chat_id=?", (chat_id,))
            self.c.execute("DELETE FROM subs WHERE chat_id=?", (chat_id,))
            self.c.commit()

    def set_state(self, sid, keys):
        with self.lock:
            self.c.execute("UPDATE subs SET state=? WHERE id=?", (json.dumps(sorted(keys)), sid))
            self.c.commit()


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------
class TG:
    def __init__(self, token):
        self.url = f"https://api.telegram.org/bot{token}/"
        self.s = make_session()

    def call(self, method, _http_timeout=40, **params):
        try:
            r = self.s.post(self.url + method, json=params, timeout=_http_timeout)
            return r.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("Telegram %s xato: %s", method, mask(e))
            return {"ok": False}

    def send(self, chat, text, inline=None, reply=None):
        p = {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if inline:
            p["reply_markup"] = {"inline_keyboard": inline}
        elif reply:
            p["reply_markup"] = reply
        return self.call("sendMessage", **p)

    def edit(self, chat, mid, text, inline=None):
        p = {"chat_id": chat, "message_id": mid, "text": text, "parse_mode": "HTML"}
        if inline is not None:
            p["reply_markup"] = {"inline_keyboard": inline}
        return self.call("editMessageText", **p)

    def send_doc(self, chat, name, content: bytes):
        try:
            self.s.post(self.url + "sendDocument", data={"chat_id": chat},
                        files={"document": (name, content)}, timeout=60)
        except requests.RequestException as e:
            log.warning("sendDocument xato: %s", mask(e))


def esc(x):
    return html.escape(str(x))


def fmt_day(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return iso


def route_name(s):
    return f"{CODE2NAME.get(s['dep'], s['dep'])} → {CODE2NAME.get(s['arv'], s['arv'])}"


def prefs_text(prefs):
    return ", ".join(SEAT_LABELS[p] for p in prefs)


LINE = "━━━━━━━━━━━━━━━━━━"
TOTAL_STEPS = 6
SEAT_EMOJI = {"sit": "💺", "lower": "⬇️", "upper": "⬆️", "side_lower": "↙️", "side_upper": "↗️", "any": "🎯"}


def step(n, title):
    """Bosqich ko'rsatkichi: 3/6 ▰▰▰▱▱▱"""
    return f"<b>{n}/{TOTAL_STEPS}</b>  {'▰' * n}{'▱' * (TOTAL_STEPS - n)}\n{title}"


def dep_time(s):
    m = re.search(r"\b(\d{1,2}:\d{2})\b", s or "")
    return m.group(1) if m else ""


def prefs_pretty(prefs):
    return " · ".join(f"{SEAT_EMOJI.get(p, '')} {SEAT_LABELS[p]}" for p in prefs)


def fmt_car(c, rel):
    icon = "💺" if c.get("seated") else "🛏"
    bits = [f"{icon} {esc(c['type'])} — <b>{rel}</b> ta"]
    kt = kinds_text(c.get("kinds"))
    if kt:
        bits.append(f"({esc(kt)})")
    tf = c.get("tariffs") or []
    if len(tf) > 1 or (tf and tf[0].get("name")):
        shown = [f"{esc(x['name'] + ' ' if x['name'] else '')}{money(x['price'])}" for x in tf[:5]]
        bits.append("\n      💰 " + " · ".join(shown))
    elif c.get("price"):
        bits.append("· " + money(c["price"]))
    return " ".join(bits)


def fmt_found(sub, tkey, q):
    t = q["train"]
    when = f"📅 {esc(fmt_day(sub['day']))}"
    if dep_time(t.get("dep")):
        when += f"  ·  🕗 {dep_time(t.get('dep'))}"
    lines = ["🎉 <b>BILET TOPILDI!</b>", LINE,
             f"🚉 <b>{esc(route_name(sub))}</b>", when,
             f"🚆 {esc(t['label'])}", LINE]
    lines += [fmt_car(c, r) for c, r in q["cars"]]
    lines += [LINE, f"✅ Jami <b>{q['total']}</b> ta joy bor (siz {sub['count']} ta so'ragansiz)",
              "⚡️ Tezroq oling, biletlar tez tugaydi!"]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Monitoring
# --------------------------------------------------------------------------
db = None
tg = None
rw = Railway()


def found_buttons(sid):
    return [[{"text": "🌐 Saytni ochish", "url": SITE_URL}],
            [{"text": "🛑 Kuzatuvni to'xtatish", "callback_data": f"rm:{sid}"}]]


def process_group(dep, arv, day, subs):
    try:
        snap = snapshot(rw.trains(dep, arv, day))
    except Exception as e:  # noqa: BLE001
        log.warning("Tekshiruv xatosi %s->%s %s: %s", dep, arv, day, e)
        return
    for s in subs:
        s = db.get(s["id"])
        if not s or not is_approved(s["chat_id"]):
            continue
        prefs = s["prefs"].split(",")
        try:
            prev = json.loads(s["state"]) if s["state"] else []
        except ValueError:
            prev = []
        prev = set(prev) if isinstance(prev, list) else set()
        cur = qualifying(snap, prefs, s["count"], s.get("max_price"))
        for tkey, q in cur.items():
            if tkey not in prev:  # yangi topilgan (yoki yo'qolib, qayta paydo bo'lgan) bilet
                tg.send(s["chat_id"], fmt_found(s, tkey, q), inline=found_buttons(s["id"]))
        db.set_state(s["id"], cur.keys())


def monitor_loop():
    while True:
        try:
            today = date.today().isoformat()
            groups = defaultdict(list)
            for s in db.all():
                if not is_approved(s["chat_id"]):  # ruxsatsiz egaga tegishli eski kuzatuvlarni o'chiramiz
                    db.remove(s["id"])
                    continue
                if s["day"] < today:
                    db.remove(s["id"])
                    tg.send(s["chat_id"], f"⌛ Kuzatuv tugadi (sana o'tib ketdi): "
                                          f"{esc(route_name(s))} · {esc(fmt_day(s['day']))}")
                    continue
                groups[(s["dep"], s["arv"], s["day"])].append(s)
            for (dep, arv, day), subs in groups.items():
                process_group(dep, arv, day, subs)
                time.sleep(random.uniform(1, 3))
        except Exception:  # noqa: BLE001
            log.exception("monitor_loop")
        time.sleep(POLL_INTERVAL + random.uniform(0, 10))


# --------------------------------------------------------------------------
# Suhbat: yo'nalish -> sana -> joy turi -> nechta bilet
# --------------------------------------------------------------------------
conv = {}
CANCEL_ROW = [{"text": "✖️ Bekor qilish", "callback_data": "x:0"}]
DATE_PAGE = 15


def station_kb(prefix, exclude=None):
    names = [n for n in STATIONS if STATIONS[n] != exclude]
    rows = [[{"text": n, "callback_data": f"{prefix}:{STATIONS[n]}"} for n in names[i:i + 2]]
            for i in range(0, len(names), 2)]
    return rows + [CANCEL_ROW]


def date_kb(page=0):
    start = date.today() + timedelta(days=page * DATE_PAGE)
    days = [start + timedelta(days=i) for i in range(DATE_PAGE)]
    rows = [[{"text": d.strftime("%d.%m"), "callback_data": "d:" + d.isoformat()} for d in days[i:i + 5]]
            for i in range(0, DATE_PAGE, 5)]
    nav = []
    if page > 0:
        nav.append({"text": "◀️ Oldingi", "callback_data": f"dp:{page - 1}"})
    if page < 3:
        nav.append({"text": "Keyingi ▶️", "callback_data": f"dp:{page + 1}"})
    return rows + [nav, CANCEL_ROW]


def seat_kb(selected):
    def lab(k):
        return ("✅ " if k in selected else "") + SEAT_LABELS[k]
    return [
        [{"text": lab("sit") + " (Afrosiyob, Manguberdi…)", "callback_data": "s:sit"}],
        [{"text": lab("lower"), "callback_data": "s:lower"}, {"text": lab("upper"), "callback_data": "s:upper"}],
        [{"text": lab("side_lower"), "callback_data": "s:side_lower"},
         {"text": lab("side_upper"), "callback_data": "s:side_upper"}],
        [{"text": lab("any"), "callback_data": "s:any"}],
        [{"text": "➡️ Davom etish", "callback_data": "s:done"}],
        CANCEL_ROW,
    ]


def count_kb():
    nums = [{"text": str(i), "callback_data": f"n:{i}"} for i in range(1, 7)]
    return [nums[:3], nums[3:], CANCEL_ROW]


HELP = (
    "🚆 <b>Poyezd bilet kuzatuvchi</b>\n"
    "Bilet tugab qoldimi? Saytni men kuzataman, bilet paydo bo'lishi bilan bir zumda xabar beraman. 📡\n\n"
    f"➕ <b>{BTN_ADD}</b> — yo'nalish, sana, joy turi, soni va narxni tanlang\n"
    f"📋 <b>{BTN_LIST}</b> — faol kuzatuvlaringiz\n\n"
    "💺 o'tirib ketadigan (Afrosiyob, Manguberdi…) · ⬇️ pastki · ⬆️ tepa · ↙️ ↗️ yon\n\n"
    "🤫 Ortiqcha shovqin yo'q: faqat kerakli bilet topilganda yozaman."
)


# --------------------------------------------------------------------------
# Kirish nazorati: faqat admin tasdiqlagan foydalanuvchilar
# --------------------------------------------------------------------------
def is_approved(chat):
    if chat in ADMINS:
        return True
    u = db.user(chat)
    if u:  # bazadagi holat ustuvor: chiqarilgan odam .env ro'yxatida bo'lsa ham kira olmaydi
        return u["status"] == "approved"
    return chat in ALLOWED


def display_name(frm):
    name = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x).strip()
    return name or frm.get("username") or str(frm.get("id", ""))


def request_access(chat, frm):
    u = db.user(chat)
    if u and u["status"] == "pending":
        tg.send(chat, "⏳ So'rovingiz yuborilgan. Admin tasdiqlashini kuting.")
        return
    if u:  # rad etilgan yoki bloklangan
        tg.send(chat, "⛔ Sizga ruxsat berilmagan.")
        return
    name, username = display_name(frm), frm.get("username") or ""
    db.add_user(chat, name, username, "pending")
    tg.send(chat, "👋 Bu bot yopiq. Ruxsat so'rovingiz adminga yuborildi, tasdiqlansa shu yerda xabar beraman.")
    for a in ADMINS:
        tg.send(a, f"🔔 <b>Yangi ruxsat so'rovi</b>\n{esc(name)}"
                   f"{' @' + esc(username) if username else ''}\nID: <code>{chat}</code>",
                inline=[[{"text": "✅ Ruxsat berish", "callback_data": f"ap:{chat}"},
                         {"text": "🚫 Rad etish", "callback_data": f"rj:{chat}"}]])


STATUS_ICON = {"approved": "✅", "pending": "⏳", "rejected": "🚫", "blocked": "🚪"}
STATUS_TEXT = {"approved": "Faol", "pending": "Tasdiq kutmoqda", "rejected": "Rad etilgan",
               "blocked": "Botdan chiqarilgan"}
USERS_PER_PAGE = 6
ADMIN_KINDS = {"ap", "rj", "rv", "ul", "uv", "uk", "ukc", "ud"}


def user_label(u):
    return f"{esc(u['name'] or '?')}{' @' + esc(u['username']) if u['username'] else ''} · <code>{u['chat_id']}</code>"


def users_view(page=0):
    """Foydalanuvchilar ro'yxati (sahifalangan): matn va tugmalar."""
    users = db.users()
    counts = defaultdict(int)
    for u in users:
        counts[u["status"]] += 1
    watches = defaultdict(int)
    for sub in db.all():
        watches[sub["chat_id"]] += 1
    pages = max(1, -(-len(users) // USERS_PER_PAGE))
    page = min(max(page, 0), pages - 1)
    head = ["👥 <b>Foydalanuvchilar</b>", LINE,
            f"✅ Faol: <b>{counts['approved']}</b>   ⏳ Kutmoqda: <b>{counts['pending']}</b>",
            f"🚪 Chiqarilgan: <b>{counts['blocked']}</b>   🚫 Rad: <b>{counts['rejected']}</b>",
            f"👑 Adminlar: <b>{len(ADMINS)}</b>"]
    if not users:
        head.append("\nHali hech kim ruxsat so'ramagan.")
    else:
        head.append("\nBirini tanlang 👇")
    kb = []
    for u in users[page * USERS_PER_PAGE:(page + 1) * USERS_PER_PAGE]:
        label = f"{STATUS_ICON.get(u['status'], '•')} {(u['name'] or '?')[:22]} · 🔔{watches[u['chat_id']]}"
        kb.append([{"text": label, "callback_data": f"uv:{u['chat_id']}"}])
    nav = []
    if page > 0:
        nav.append({"text": "◀️", "callback_data": f"ul:{page - 1}"})
    if pages > 1:
        nav.append({"text": f"{page + 1}/{pages}", "callback_data": f"ul:{page}"})
    if page < pages - 1:
        nav.append({"text": "▶️", "callback_data": f"ul:{page + 1}"})
    if nav:
        kb.append(nav)
    return "\n".join(head), kb


def user_card(target):
    """Bitta foydalanuvchi kartochkasi (holati, kuzatuvlari, amallar)."""
    u = db.user(target)
    if not u:
        return "Foydalanuvchi topilmadi.", [[{"text": "◀️ Ro'yxat", "callback_data": "ul:0"}]]
    subs = db.of_chat(target)
    lines = [f"{STATUS_ICON.get(u['status'], '•')} <b>{esc(u['name'] or '?')}</b>"
             f"{' @' + esc(u['username']) if u['username'] else ''}",
             f"ID: <code>{u['chat_id']}</code>",
             f"Holat: {STATUS_TEXT.get(u['status'], u['status'])}",
             f"Qo'shilgan: {esc((u.get('created') or '')[:10])}", LINE,
             f"🔔 Kuzatuvlar: <b>{len(subs)}</b>"]
    for sub in subs[:8]:
        lines.append(f"• {esc(route_name(sub))} · {esc(fmt_day(sub['day']))} · {sub['count']} ta")
    st = u["status"]
    if st == "approved":
        kb = [[{"text": "🚪 Botdan chiqarib yuborish", "callback_data": f"uk:{target}"}]]
    elif st == "pending":
        kb = [[{"text": "✅ Ruxsat berish", "callback_data": f"ap:{target}"},
               {"text": "🚫 Rad etish", "callback_data": f"rj:{target}"}]]
    else:
        kb = [[{"text": "✅ Qayta ruxsat berish", "callback_data": f"ap:{target}"}],
              [{"text": "🗑 Yozuvni o'chirish", "callback_data": f"ud:{target}"}]]
    kb.append([{"text": "◀️ Ro'yxat", "callback_data": "ul:0"}])
    return "\n".join(lines), kb


def show_users(chat):
    text, kb = users_view(0)
    tg.send(chat, text, inline=kb)


def kick(target):
    """Foydalanuvchini botdan chiqarish: ruxsat olinadi, kuzatuvlari o'chadi."""
    db.set_status(target, "blocked")
    db.remove_chat(target)
    conv.pop(target, None)
    tg.send(target, "🚪 Siz botdan chiqarildingiz. Kuzatuvlaringiz o'chirildi.",
            reply={"remove_keyboard": True})


def handle_admin_action(chat, mid, kind, target):
    u = db.user(target)
    if not u:
        tg.edit(chat, mid, "Foydalanuvchi topilmadi.", inline=[])
        return
    label = user_label(u)
    if kind == "ap":
        db.set_status(target, "approved")
        tg.edit(chat, mid, f"✅ Ruxsat berildi\n{label}", inline=[
            [{"text": "👤 Kartochka", "callback_data": f"uv:{target}"}]])
        tg.send(target, "🎉 Sizga ruxsat berildi! Boshlash uchun pastdagi tugmalardan foydalaning.",
                reply=menu_for(target))
    elif kind == "rj":
        db.set_status(target, "rejected")
        tg.edit(chat, mid, f"🚫 Rad etildi\n{label}", inline=[
            [{"text": "✅ Ruxsat berish", "callback_data": f"ap:{target}"}]])
        tg.send(target, "⛔ So'rovingiz rad etildi.")
    elif kind == "rv":
        kick(target)
        tg.edit(chat, mid, f"🚪 Botdan chiqarildi\n{label}", inline=[
            [{"text": "✅ Qayta ruxsat berish", "callback_data": f"ap:{target}"}]])


def handle_admin_callback(chat, mid, kind, val):
    n = int(val)
    if kind == "ul":
        text, kb = users_view(n)
        tg.edit(chat, mid, text, inline=kb)
    elif kind == "uv":
        text, kb = user_card(n)
        tg.edit(chat, mid, text, inline=kb)
    elif kind == "uk":
        u = db.user(n)
        if not u or u["status"] != "approved":
            text, kb = user_card(n)
            tg.edit(chat, mid, text, inline=kb)
            return
        tg.edit(chat, mid,
                f"🚪 <b>{esc(u['name'] or '?')}</b> botdan chiqarilsinmi?\n"
                f"Uning {len(db.of_chat(n))} ta kuzatuvi ham o'chadi.",
                inline=[[{"text": "✅ Ha, chiqarish", "callback_data": f"ukc:{n}"}],
                        [{"text": "↩️ Yo'q, qoldirish", "callback_data": f"uv:{n}"}]])
    elif kind == "ukc":
        u = db.user(n)
        if u and u["status"] == "approved":
            kick(n)
        text, kb = user_card(n)
        tg.edit(chat, mid, text, inline=kb)
    elif kind == "ud":
        u = db.user(n)
        if u and u["status"] in ("rejected", "blocked"):
            db.delete_user(n)  # qayta so'rov yuborishi mumkin bo'ladi
        text, kb = users_view(0)
        tg.edit(chat, mid, text, inline=kb)
    else:
        handle_admin_action(chat, mid, kind, n)


def parse_date(text):
    text = text.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m", "%d/%m/%Y"):
        try:
            d = datetime.strptime(text, fmt).date()
            if fmt == "%d.%m":
                d = d.replace(year=date.today().year)
                if d < date.today():
                    d = d.replace(year=d.year + 1)
            return d
        except ValueError:
            continue
    return None


def start_add(chat):
    conv[chat] = {"step": "from", "prefs": set()}
    tg.send(chat, step(1, "🚉 <b>Qayerdan</b> jo'naysiz?"), inline=station_kb("f"))


def ask_seats(chat, st):
    st["step"] = "seats"
    tg.send(chat, step(4, "💺 <b>Qaysi joy</b> kerak? (bir nechtasini tanlash mumkin)"),
            inline=seat_kb(st["prefs"]))


def ask_count(chat, st):
    st["step"] = "count"
    tg.send(chat, step(5, f"🎟 <b>Nechta bilet</b> kerak? (tugmani bosing yoki 1–{MAX_TICKETS} raqam yozing)"),
            inline=count_kb())


def ask_price(chat, st):
    st["step"] = "price"
    tg.send(chat, step(6, "💰 Bir bilet uchun <b>eng ko'p narx</b> qancha? So'mda yozing (masalan 250000) "
                          "yoki cheklovsiz davom eting."),
            inline=[[{"text": "♾ Cheklovsiz", "callback_data": "pr:0"}], CANCEL_ROW])


def finish_add(chat, max_price=None):
    st = conv.pop(chat)
    count = st["count"]
    sid = db.add(chat, st["from"], st["to"], st["date"], st["prefs"], count, max_price)
    sub = db.get(sid)
    price_line = f"💰 Har bir bilet {money(max_price)}gacha" if max_price else "💰 Narx: cheklovsiz"
    tg.send(chat,
            "📡 <b>Qidiruv boshlandi!</b>\n" + LINE + "\n"
            f"🚉 <b>{esc(route_name(sub))}</b>\n"
            f"📅 {esc(fmt_day(sub['day']))}\n"
            f"{esc(prefs_pretty(sorted(st['prefs'])))}\n"
            f"🎟 Kerak: {count} ta bilet\n"
            f"{price_line}\n" + LINE + "\n"
            f"🔔 Shuncha bilet topilishi bilan zudlik bilan yozaman (har ~{POLL_INTERVAL} soniyada tekshiraman).",
            reply=menu_for(chat))
    threading.Thread(target=process_group, args=(sub["dep"], sub["arv"], sub["day"], [sub]),
                     daemon=True).start()


def show_list(chat):
    subs = db.of_chat(chat)
    if not subs:
        tg.send(chat, f"🔭 Hozircha kuzatuv yo'q.\n«{BTN_ADD}» tugmasini bosib birinchisini qo'shing.",
                reply=menu_for(chat))
        return
    tg.send(chat, f"🔭 <b>Faol kuzatuvlaringiz: {len(subs)} ta</b>", reply=menu_for(chat))
    for sub in subs:
        try:
            keys = json.loads(sub["state"]) if sub["state"] else []
        except ValueError:
            keys = []
        status = "🟢 Hozir bilet bor!" if isinstance(keys, list) and keys else "🔎 Qidirilmoqda…"
        price = f"💰 {money(sub['max_price'])}gacha" if sub.get("max_price") else "💰 narx cheklovsiz"
        tg.send(chat, f"<b>{esc(route_name(sub))}</b>\n📅 {esc(fmt_day(sub['day']))} · "
                      f"🎟 {sub['count']} ta\n{esc(prefs_pretty(sub['prefs'].split(',')))}\n{price}\n{status}",
                inline=[[{"text": "🛑 O'chirish", "callback_data": f"rm:{sub['id']}"}]])


def on_message(m):
    if m["chat"].get("type") != "private":
        return
    chat = m["chat"]["id"]
    text = (m.get("text") or "").strip()
    frm = m.get("from") or {"id": chat}
    if not is_approved(chat):
        request_access(chat, frm)
        return
    if not is_admin(chat) and not db.user(chat):  # .env orqali kirganlar ham ro'yxatda ko'rinsin
        db.add_user(chat, display_name(frm), frm.get("username") or "", "approved")
    low = text.lower()
    if low.startswith("/debug"):
        arg = text.partition(" ")[2].strip()
        s = db.get(int(arg)) if arg.isdigit() else None
        if not s or s["chat_id"] != chat:
            tg.send(chat, "Topilmadi. Kuzatuv raqami kerak: /debug 1")
            return
        try:
            data = rw.raw(s["dep"], s["arv"], s["day"])
            tg.send_doc(chat, f"response_{s['id']}.json",
                        json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
        except Exception as e:  # noqa: BLE001
            tg.send(chat, f"Xato: {esc(e)}")
        return
    if low.startswith("/start") or low.startswith("/help") or text == BTN_HELP:
        conv.pop(chat, None)
        greet = f"👋 Salom, <b>{esc(display_name(frm))}</b>!\n\n" if low.startswith("/start") else ""
        tg.send(chat, greet + HELP, reply=menu_for(chat))
        return
    if text == BTN_USERS and is_admin(chat):
        conv.pop(chat, None)
        show_users(chat)
        return
    if text == BTN_ADD:
        start_add(chat)
        return
    if text == BTN_LIST:
        conv.pop(chat, None)
        show_list(chat)
        return
    st = conv.get(chat)
    if st and st["step"] == "date":
        d = parse_date(text)
        if not d or d < date.today():
            tg.send(chat, "Sana noto'g'ri. Masalan: 25.12.2026")
            return
        st["date"] = d.isoformat()
        ask_seats(chat, st)
    elif st and st["step"] == "count":
        if text.isdigit() and 1 <= int(text) <= MAX_TICKETS:
            st["count"] = int(text)
            ask_price(chat, st)
        else:
            tg.send(chat, f"1 dan {MAX_TICKETS} gacha raqam yozing yoki tugmani bosing.")
    elif st and st["step"] == "price":
        digits = re.sub(r"[\s,.']", "", text.lower().replace("so'm", "").replace("som", ""))
        if digits.isdigit() and int(digits) > 0:
            finish_add(chat, int(digits))
        else:
            tg.send(chat, "Narxni raqam bilan yozing (masalan 250000) yoki «Cheklovsiz» tugmasini bosing.")
    else:
        tg.send(chat, "Tugmalardan foydalaning 👇", reply=menu_for(chat))


def on_callback(cb):
    chat = cb["message"]["chat"]["id"]
    mid = cb["message"]["message_id"]
    data = cb.get("data", "")
    tg.call("answerCallbackQuery", callback_query_id=cb["id"])
    kind, _, val = data.partition(":")
    if kind in ADMIN_KINDS:  # faqat admin
        if is_admin(chat) and val.lstrip("-").isdigit():
            handle_admin_callback(chat, mid, kind, val)
        return
    if not is_approved(chat):
        return
    if kind == "rm" and val.isdigit():
        ok = db.remove(int(val), chat)
        tg.send(chat, "🛑 Kuzatuv o'chirildi." if ok else "Topilmadi.", reply=menu_for(chat))
        return
    if kind == "x":
        conv.pop(chat, None)
        tg.edit(chat, mid, "Bekor qilindi.", inline=[])
        return
    st = conv.get(chat)
    if not st:
        tg.send(chat, f"Avval «{BTN_ADD}» tugmasini bosing.", reply=menu_for(chat))
        return
    if kind == "f" and st["step"] == "from":
        st["from"], st["step"] = val, "to"
        tg.send(chat, step(2, "🏁 <b>Qayerga</b> borasiz?"), inline=station_kb("t", exclude=val))
    elif kind == "t" and st["step"] == "to":
        st["to"], st["step"] = val, "date"
        tg.send(chat, step(3, "📅 <b>Qachon</b> ketasiz? Tugmani bosing yoki sanani yozing (masalan 25.12.2026):"),
                inline=date_kb())
    elif kind == "dp" and st["step"] == "date" and val.isdigit():
        tg.call("editMessageReplyMarkup", chat_id=chat, message_id=mid,
                reply_markup={"inline_keyboard": date_kb(int(val))})
    elif kind == "d" and st["step"] == "date":
        st["date"] = val
        ask_seats(chat, st)
    elif kind == "s" and st["step"] == "seats":
        if val == "done":
            if not st["prefs"]:
                tg.send(chat, "Kamida bitta joy turini tanlang.")
                return
            ask_count(chat, st)
            return
        if val == "any":
            st["prefs"] = {"any"}
        else:
            st["prefs"].discard("any")
            st["prefs"] ^= {val}
        tg.call("editMessageReplyMarkup", chat_id=chat, message_id=mid,
                reply_markup={"inline_keyboard": seat_kb(st["prefs"])})
    elif kind == "n" and st["step"] == "count" and val.isdigit():
        st["count"] = int(val)
        ask_price(chat, st)
    elif kind == "pr" and st["step"] == "price":
        finish_add(chat, None)


def poll_loop():
    offset, fails = 0, 0
    while True:
        res = tg.call("getUpdates", _http_timeout=45, offset=offset, timeout=30)
        if not res.get("ok"):
            fails += 1
            time.sleep(min(60, 5 * fails))
            continue
        fails = 0
        for u in res["result"]:
            offset = u["update_id"] + 1
            try:
                if "message" in u:
                    on_message(u["message"])
                elif "callback_query" in u:
                    on_callback(u["callback_query"])
            except Exception:  # noqa: BLE001
                log.exception("update xatosi")


def main():
    global db, tg
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN topilmadi (.env faylga yozing).")
    if not ADMINS:
        raise SystemExit("ADMIN_IDS topilmadi: .env ga o'zingizning Telegram chat ID ngizni yozing "
                         "(ID ni @userinfobot dan bilib oling).")
    db = DB(DB_PATH)
    tg = TG(BOT_TOKEN)
    me = tg.call("getMe", _http_timeout=20)
    if me.get("ok"):
        log.info("Telegram bilan aloqa bor: @%s", me["result"].get("username"))
    else:
        log.error("Telegram'ga ulanib bo'lmadi. Tekshiring: (1) token to'g'rimi; (2) kompyuter/serverda internet "
                  "va Telegram ochiqmi; (3) tizimda HTTPS_PROXY o'rnatilgan bo'lsa, .env ga IGNORE_PROXY=1 yozing "
                  "yoki ishlaydigan proksini PROXY_URL=http://host:port ko'rinishida bering. "
                  "Telegram bloklangan hududda VPS yoki proksi kerak. Bot urinishda davom etadi.")
    threading.Thread(target=monitor_loop, daemon=True).start()
    log.info("Bot ishga tushdi")
    poll_loop()


if __name__ == "__main__":
    main()
