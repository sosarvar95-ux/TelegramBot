"""Tarmoqsiz mantiq testi: python test_logic.py"""
import bot


def car(typ, num, free, seats=None):
    c = {"typeShow": typ, "number": num, "freeSeats": free, "tariffs": [{"tariff": 150000}]}
    if seats is not None:
        c["freePlaces"] = seats
    return c


def train(num, cars, typ="Passenger"):
    return {"number": num, "type": typ, "departureDate": "2026-12-05 08:00", "cars": cars}


# 1) joy turi aniqlash
k = bot.seat_kinds(car("Plaskart", 5, 5, "1,2,4,37,38,40-41"))
assert k == {"lower": 1, "upper": 2, "side_lower": 2, "side_upper": 2}, k
assert bot.seat_kinds(car("Kupe", 1, 3)) is None

# 2) vagon o'tirib ketadiganmi
assert bot.is_seated({"typeShow": "Biznes"}, {"type": "Afrosiyob"})
assert bot.is_seated({"typeShow": "2-klass"}, {"type": "Jaloliddin Manguberdi"})
assert not bot.is_seated({"typeShow": "Kupe"}, {"type": "Afrosiyob"})
assert not bot.is_seated({"typeShow": "Plaskart"}, {"type": "Passenger"})

# 3) "bilet topildi" shartlari
hs = lambda free_biz, free_eco: bot.snapshot([train("769", [
    {"typeShow": "Biznes", "number": 1, "freeSeats": free_biz},
    {"typeShow": "Econom", "number": 2, "freeSeats": free_eco}], "Afrosiyob")])

assert bot.qualifying(hs(0, 0), ["sit"], 1) == {}                        # joy yo'q
q = bot.qualifying(hs(0, 2), ["sit"], 2)
assert len(q) == 1 and list(q.values())[0]["total"] == 2                 # aynan 2 ta kerak, 2 ta bor
assert bot.qualifying(hs(0, 2), ["sit"], 3) == {}                        # 3 ta kerak, 2 ta bor
q = bot.qualifying(hs(1, 2), ["sit"], 3)                                 # ikki vagon yig'indisi
assert len(q) == 1 and list(q.values())[0]["total"] == 3
assert bot.qualifying(hs(5, 5), ["lower"], 1) == {}                      # pastki tanlansa Afrosiyob chiqmaydi

pl = bot.snapshot([train("5", [car("Plaskart", 1, 5, [1, 2, 3, 4, 5])])])
assert bot.qualifying(pl, ["sit"], 1) == {}                              # plaskart "o'tirib" emas
assert len(bot.qualifying(pl, ["lower"], 3)) == 1                        # pastki: 1,3,5 = 3 ta
assert bot.qualifying(pl, ["lower"], 4) == {}
assert len(bot.qualifying(pl, ["lower", "upper"], 5)) == 1               # pastki + tepa
assert len(bot.qualifying(pl, ["any"], 5)) == 1

# 4) xabar formati xatosiz ishlaydi
sub = {"id": 7, "dep": "2900000", "arv": "2900700", "day": "2026-12-05", "prefs": "sit", "count": 2}
tkey, qq = next(iter(bot.qualifying(hs(1, 2), ["sit"], 2).items()))
print(bot.fmt_found(sub, tkey, qq))

# 5) bazada eski/yangi holat va takroriy xabar bo'lmasligi
import tempfile, os
bot.db = bot.DB(os.path.join(tempfile.mkdtemp(), "t.db"))
sent = []


class FakeTG:
    def send(self, chat, text, **kw):
        sent.append(text)


bot.tg = FakeTG()
bot.ADMINS = {1}   # test chati (1) tasdiqlangan bo'lishi kerak
state = {"snap": hs(0, 0)}
bot.rw.trains = lambda dep, arv, day: state["raw"]
sid = bot.db.add(1, "2900000", "2900700", "2099-01-01", {"sit"}, 2)


def raw(free_biz, free_eco):
    return [train("769", [{"typeShow": "Biznes", "number": 1, "freeSeats": free_biz},
                          {"typeShow": "Econom", "number": 2, "freeSeats": free_eco}], "Afrosiyob")]


def tick():
    s = bot.db.get(sid)
    bot.process_group(s["dep"], s["arv"], s["day"], [s])


state["raw"] = raw(0, 0); tick(); assert sent == []                      # joy yo'q -> jim
state["raw"] = raw(0, 1); tick(); assert sent == []                      # 1 ta, 2 ta kerak -> jim
state["raw"] = raw(1, 1); tick(); assert len(sent) == 1                  # 2 ta bo'ldi -> xabar
state["raw"] = raw(1, 4); tick(); assert len(sent) == 1                  # ko'paysa ham qayta xabar yo'q
state["raw"] = raw(0, 0); tick(); assert len(sent) == 1                  # tugadi
state["raw"] = raw(2, 2); tick(); assert len(sent) == 2                  # qaytarildi -> yana xabar

# 6) tariflar va narx chegarasi
c = {"typeShow": "Plaskart", "number": 1, "freeSeats": 3, "freePlaces": [1, 3, 5],
     "tariffs": [{"tariff": 180000, "tariffName": "Standart"}, {"tariff": 120000, "tariffName": "Talaba"},
                 {"tariff": 120000, "tariffName": "Talaba"}, {"tariff": "x"}]}
tf = bot.parse_tariffs(c)
assert tf == [{"name": "Talaba", "price": 120000}, {"name": "Standart", "price": 180000}], tf
snap = bot.snapshot([train("9", [c])])
assert list(snap.values())[0]["cars"]["Plaskart#1"]["price"] == 120000
assert len(bot.qualifying(snap, ["lower"], 3, 150000)) == 1
assert bot.qualifying(snap, ["lower"], 3, 100000) == {}
assert len(bot.qualifying(snap, ["lower"], 3, None)) == 1
tkey, qq = next(iter(bot.qualifying(snap, ["lower"], 3).items()))
msg = bot.fmt_found({"id": 1, "dep": "2900000", "arv": "2900700", "day": "2026-12-05", "count": 3}, tkey, qq)
assert "Talaba 120 000 so'm" in msg and "Standart 180 000 so'm" in msg, msg
print(msg)
print("OK")

# 7) kirish nazorati
import importlib
bot.ADMINS = {100}
bot.ALLOWED = {300}
bot.db = bot.DB(os.path.join(tempfile.mkdtemp(), "u.db"))


class Rec:
    def __init__(self): self.out = []
    def send(self, chat, text, inline=None, reply=None): self.out.append((chat, text, inline, reply))
    def edit(self, chat, mid, text, inline=None): self.out.append((chat, "EDIT " + text, inline, None))
    def call(self, *a, **k): return {"ok": True}


bot.tg = r = Rec()
msg = lambda chat, text, typ="private": {"chat": {"id": chat, "type": typ}, "text": text,
                                          "from": {"id": chat, "first_name": "Ali", "username": "ali"}}
cbq = lambda chat, data: {"id": "1", "data": data, "message": {"chat": {"id": chat}, "message_id": 5}}

assert bot.is_approved(100) and bot.is_approved(300) and not bot.is_approved(200)

# notanish foydalanuvchi: so'rov adminga ketadi, o'ziga hech qanday funksiya ochilmaydi
bot.on_message(msg(200, "/start"))
assert bot.db.user(200)["status"] == "pending"
assert any(c == 100 and "ruxsat so'rovi" in t and "ap:200" in str(i) for c, t, i, _ in r.out)
n = len(r.out)
bot.on_message(msg(200, bot.BTN_ADD))                       # tasdiqlanmaguncha "Yangi kuzatuv" ishlamaydi
assert 200 not in bot.conv and len(r.out) == n + 1 and "kuting" in r.out[-1][1]
bot.on_callback(cbq(200, "s:sit"))                          # tugmalar ham ishlamaydi
bot.on_callback(cbq(200, "ap:200"))                         # o'zini o'zi tasdiqlay olmaydi
assert bot.db.user(200)["status"] == "pending"
assert not any("Ruxsat berildi" in t for _, t, _, _ in r.out)

# guruhdagi xabarlar e'tiborga olinmaydi
k = len(r.out); bot.on_message(msg(555, "/start", typ="group")); assert len(r.out) == k and bot.db.user(555) is None

# admin tasdiqlaydi
bot.on_callback(cbq(100, "ap:200"))
assert bot.db.user(200)["status"] == "approved" and bot.is_approved(200)
assert any(c == 200 and "ruxsat berildi" in t.lower() for c, t, _, _ in r.out)
bot.on_message(msg(200, bot.BTN_ADD)); assert 200 in bot.conv  # endi ishlaydi
# oddiy foydalanuvchi admin menyusini ko'rmaydi / ochmaydi
assert bot.BTN_USERS not in str(bot.menu_for(200)) and bot.BTN_USERS in str(bot.menu_for(100))
k = len(r.out); bot.on_message(msg(200, bot.BTN_USERS)); assert not any("Ruxsat berilgan" in t for _, t, _, _ in r.out[k:])

# ruxsat bekor qilinsa kuzatuvlari o'chadi va qayta kira olmaydi
sid = bot.db.add(200, "2900000", "2900700", "2099-01-01", {"sit"}, 1)
bot.on_callback(cbq(200, "rv:200"))                          # admin bo'lmaganning bekor qilishi ishlamaydi
assert bot.db.user(200)["status"] == "approved"
bot.on_callback(cbq(100, "rv:200"))
assert bot.db.user(200)["status"] == "blocked" and bot.db.get(sid) is None and not bot.is_approved(200)
k = len(r.out); bot.on_message(msg(200, "/start")); assert "ruxsat berilmagan" in r.out[k][1].lower()
# admin ro'yxatida hamma ko'rinadi, rad etilgan keyin qayta tasdiqlanishi mumkin
bot.on_message(msg(100, bot.BTN_USERS)); assert "Foydalanuvchilar" in r.out[-1][1] and "uv:200" in str(r.out[-1][2])
bot.on_callback(cbq(100, "ap:200")); assert bot.is_approved(200)
print("OK (kirish nazorati)")

# 8) ruxsatsiz egaga tegishli eski kuzatuvlar xabar yubormaydi va o'chiriladi
bot.ADMINS, bot.ALLOWED = {100}, set()
bot.db = bot.DB(os.path.join(tempfile.mkdtemp(), "old.db"))
sent.clear()
bot.tg = type("T", (), {"send": lambda self, chat, text, **kw: sent.append((chat, text))})()
old = bot.db.add(999, "2900000", "2900700", "2099-01-01", {"sit"}, 1)   # ruxsatsiz, eski kuzatuv
ok = bot.db.add(100, "2900000", "2900700", "2099-01-01", {"sit"}, 1)    # admin
state["raw"] = raw(5, 5)
for s in bot.db.all():
    bot.process_group(s["dep"], s["arv"], s["day"], [s])
assert [c for c, _ in sent] == [100], sent                                # faqat adminga xabar
bot.db.add(999, "2900000", "2900700", "2099-01-02", {"sit"}, 1)
for s in bot.db.all():
    if not bot.is_approved(s["chat_id"]):
        bot.db.remove(s["id"])
assert [s["chat_id"] for s in bot.db.all()] == [100]
print("OK (eski kuzatuvlar)")

# 9) foydalanuvchilar paneli: ro'yxat, kartochka, chiqarib yuborish
bot.ADMINS, bot.ALLOWED = {100}, {300}
bot.db = bot.DB(os.path.join(tempfile.mkdtemp(), "panel.db"))
bot.tg = r = Rec()

bot.on_message(msg(100, bot.BTN_ADD)); assert "1/6" in r.out[-1][1]          # bosqich ko'rsatkichi
bot.conv.clear()
bot.on_message(msg(300, "/start"))                                            # .env orqali kirgan -> ro'yxatga tushadi
assert bot.db.user(300)["status"] == "approved"
assert any(c == 300 and "Salom" in t for c, t, _, _ in r.out)
bot.db.add_user(400, "Vali", "vali", "pending")
bot.db.add(300, "2900000", "2900700", "2099-01-01", {"sit"}, 2)

text, kb = bot.users_view(0)
assert "Foydalanuvchilar" in text and "uv:300" in str(kb) and "uv:400" in str(kb), (text, kb)
assert "🔔1" in str(kb)                                                       # kuzatuvlar soni ko'rinadi

r.out.clear(); bot.on_callback(cbq(100, "uv:300"))
assert "Kuzatuvlar: <b>1</b>" in r.out[-1][1] and "uk:300" in str(r.out[-1][2])
r.out.clear(); bot.on_callback(cbq(300, "uv:300")); assert r.out == []         # oddiy foydalanuvchi panelga kira olmaydi
bot.on_callback(cbq(300, "ukc:300")); assert bot.is_approved(300)              # o'zini chiqara olmaydi

bot.on_callback(cbq(100, "uk:300"))                                            # avval tasdiq so'raydi
assert "chiqarilsinmi" in r.out[-1][1] and bot.is_approved(300)
bot.on_callback(cbq(100, "ukc:300"))                                           # tasdiqlangach chiqaradi
assert not bot.is_approved(300) and bot.db.of_chat(300) == []
assert any(c == 300 and "chiqarildingiz" in t for c, t, _, _ in r.out)
bot.on_message(msg(300, "/start"))                                             # .env da bo'lsa ham qayta kira olmaydi
assert "ruxsat berilmagan" in r.out[-1][1].lower()

bot.on_callback(cbq(100, "rj:400")); bot.on_callback(cbq(100, "ud:400"))      # rad etilgan yozuvni o'chirish
assert bot.db.user(400) is None
bot.on_message(msg(400, "/start")); assert bot.db.user(400)["status"] == "pending"   # qayta so'rashi mumkin
bot.on_callback(cbq(100, "ud:400")); assert bot.db.user(400)["status"] == "pending"  # kutayotganni o'chirib bo'lmaydi

for i in range(10):
    bot.db.add_user(1000 + i, f"User{i}", "", "approved")
text, kb = bot.users_view(1)
assert "ul:0" in str(kb) and "ul:2" not in str(kb) and "2/2" in str(kb), kb   # 12 kishi = 2 sahifa
assert "ul:1" in str(bot.users_view(0)[1]) and "uv:400" in str(bot.users_view(0)[1])  # 1-sahifada kutayotgan birinchi

# 10) yangi ko'rinish
sub = {"id": 1, "dep": "2900000", "arv": "2900700", "day": "2026-12-05", "count": 2, "prefs": "sit"}
q = list(bot.qualifying(hs(1, 2), ["sit"], 2).values())[0]
card = bot.fmt_found(sub, "k", q)
assert "BILET TOPILDI" in card and "🕗 08:00" in card and "Jami <b>3</b>" in card, card
print(card)
print("OK (panel va ko'rinish)")

# 11) xato loglarida bot tokeni ko'rinmasligi
import logging, io
bot.BOT_TOKEN = "123456:SECRET-TOKEN"
t = bot.TG(bot.BOT_TOKEN)
def boom(*a, **k):
    raise bot.requests.ConnectionError(f"Max retries exceeded with url: /bot{bot.BOT_TOKEN}/getUpdates")
t.s.post = boom
buf = io.StringIO(); h = logging.StreamHandler(buf); bot.log.addHandler(h); bot.log.setLevel(logging.WARNING)
assert t.call("getUpdates") == {"ok": False}
bot.log.removeHandler(h)
assert "SECRET-TOKEN" not in buf.getvalue() and "<TOKEN>" in buf.getvalue(), buf.getvalue()
# proksi sozlamalari
bot.IGNORE_PROXY, bot.PROXY_URL = True, "http://127.0.0.1:9"
ms = bot.make_session(); assert ms.trust_env is False and ms.proxies["https"] == "http://127.0.0.1:9"
print("OK (token maskalash, proksi)")
