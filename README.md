# UzRailPass kuzatuvchi Telegram bot

Tanlangan yo'nalish, sana va joy turi (o'tirib ketadigan / pastki / tepa / yon pastki / yon tepa) bo'yicha saytni
tekshirib turadi va tanlangan sanada **kerakli sondagi bilet topilganda** Telegramga xabar yuboradi (boshqa hech qachon).

## Ishga tushirish

1. @BotFather orqali bot yarating va tokenni oling.
2. `cp .env.example .env`, `BOT_TOKEN` va `ADMIN_IDS` (o'z chat ID ngiz, @userinfobot dan oling) ni yozing.
3. `pip install -r requirements.txt`
4. `python bot.py`

Doimiy ishlashi uchun serverda (VPS) `systemd` yoki `tmux` ichida qoldiring.
Uzbekiston tashqarisidagi serverdan sayt ochilmasligi mumkin, shunday bo'lsa O'zbekistondagi IP dan ishlating.

## Kirish nazorati va foydalanuvchilarni boshqarish (faqat admin)

Botdan faqat siz (admin) tasdiqlagan foydalanuvchilar foydalana oladi.

- Notanish odam `/start` bossa, unga "so'rov adminga yuborildi" deyiladi, sizga esa
  **✅ Ruxsat berish / 🚫 Rad etish** tugmalari bilan xabar keladi. Tasdiqlanmaguncha unga hech narsa ishlamaydi.
- **👥 Foydalanuvchilar** tugmasi (faqat adminda): hammasining ro'yxati (faol, kutayotgan, chiqarilgan sonlari bilan),
  har kimning yonida nechta kuzatuvi borligi ko'rinadi. Ro'yxat sahifalanadi (6 tadan).
- Foydalanuvchini bossangiz **kartochka** ochiladi: holati, qo'shilgan sana va kuzatuvlari.
  Shu yerdan **🚪 Botdan chiqarib yuborish** (avval "Ishonchingiz komilmi?" deb so'raydi), qayta ruxsat berish
  yoki yozuvni butunlay o'chirish mumkin.
- Chiqarilgan odamning kuzatuvlari o'chadi, unga xabar boradi va u qayta so'rov yubora olmaydi
  (`.env` dagi `ALLOWED_CHAT_IDS` da bo'lsa ham). Yozuv o'chirilsa, u yana so'rov yubora oladi.
- `.env` orqali oldindan kirgan foydalanuvchilar ham ro'yxatda ko'rinadi (birinchi xabaridan keyin).
- Faqat shaxsiy chatlar ishlaydi, guruhlar e'tiborga olinmaydi.

## Bot ko'rinishi

- Yangi kuzatuv 6 bosqichda, har birida `3/6 ▰▰▰▱▱▱` ko'rsatkichi bor.
- "Bilet topildi" xabari kartochka ko'rinishida: yo'nalish, sana va jo'nash vaqti, poyezd, vagonlar, tariflar.
- `bot_avatar.png` bot rasmi, `botfather.txt` ichida nom, tavsif va buyruqlar matnlari tayyor.

## Foydalanish (hammasi tugmalar bilan)

1. `/start` bosing, pastda doimiy tugmalar chiqadi.
2. **➕ Yangi kuzatuv**: qayerdan, qayerga, sana, joy turi, nechta bilet kerakligini va (ixtiyoriy) bir bilet uchun eng ko'p narxni tanlaysiz.
3. Shuncha bilet topilishi bilan bot xabar yuboradi (xabarda "Saytni ochish" va "To'xtatish" tugmalari bor).
4. **📋 Kuzatuvlarim**: faol kuzatuvlar, shu yerdan o'chirish mumkin.

Xabarda har vagonning tariflari (masalan Standart / Talaba) va narxlari ko'rsatiladi.
Narx chegarasi vagonning eng arzon tarifi bo'yicha tekshiriladi.

Bilet soni bir poyezddagi tanlangan turdagi bo'sh joylar yig'indisi bo'yicha hisoblanadi
(joylar bitta vagonda bo'lishi shart emas, xabarda har vagon alohida ko'rsatiladi).
Bilet tugab, keyin yana paydo bo'lsa, qayta xabar keladi; ko'payishi haqida takror xabar kelmaydi.
Texnik tekshiruv uchun yashirin `/debug <id>` buyrug'i saqlangan (saytdan kelgan xom javobni fayl qiladi).

## Ulanish xatolari (ProxyError / Max retries exceeded)

Loglarda `Unable to connect to proxy ... Tunnel connection failed: 403/503` chiqsa, botning o'zida xato yo'q:
u ishlayotgan muhit tashqi saytlarga (Telegram, eticket) proksi orqali chiqadi va proksi ularni o'tkazmayapti.
Bunday muhitlarga bulutli sandbox/CI konteynerlar kiradi, ularda bot ishlamaydi.

- Botni o'zingizning kompyuteringizda yoki VPS da ishga tushiring (internetga to'g'ridan-to'g'ri chiqadigan joyda).
- Tizimda `HTTPS_PROXY` bo'lsa va u kerak bo'lmasa: `.env` ga `IGNORE_PROXY=1` yozing.
- Ishlaydigan proksi kerak bo'lsa: `PROXY_URL=http://host:port`.
- Ishga tushganda log `Telegram bilan aloqa bor: @botnomi` desa, Telegramga ulanish tayyor.
- Saytga (eticket) O'zbekiston tashqarisidagi serverdan chiqib bo'lmasligi mumkin; shunda O'zbekistondagi IP kerak.

## Muhim: sinab ko'rish kerak

Bot saytning ichki API'siga (`POST /api/v3/handbook/trains/list`) tayanadi. Bu tuzilma ochiq
manbadagi namunadan olingan, men saytga to'g'ridan-to'g'ri ulana olmadim, shuning uchun:

1. Kuzatuv qo'shgach, tanlovingiz bilan tasdiq xabari chiqadi; bilet bo'lsa, birinchi tekshiruvdayoq xabar keladi.
2. Agar xabar kelmasa yoki joy turlari (pastki/tepa) chiqmasa, `/debug <id>` yuboring, javob
   maydonlari nomiga qarab `seat_kinds()` funksiyasini moslash mumkin.
3. Joy turi qoidasi: toq raqam = pastki, juft = tepa, 37 dan katta = yon joylar.
   Agar API joy raqamlarini bermasa, bot umumiy bo'sh joy soni bo'yicha xabar beradi.

`stations.json` dagi stansiya kodlaridan faqat Toshkent, Qarshi, Jizzax, Termiz, Guliston, Qo'qon,
Margilon, Pop, Namangan manbalarda tasdiqlangan; qolganlarini (Samarqand, Buxoro, Urganch, Xiva,
Navoiy, Nukus, Andijon) sinab ko'ring va kerak bo'lsa tuzating.

## Joy turlari

- **O'tirib ketadigan**: tezyurar poyezdlar (Afrosiyob, Jaloliddin Manguberdi, Sharq, Nasaf va h.k.) o'rindiqli vagonlari.
  Vagon turi nomidan (Biznes, Econom, VIP...) yoki poyezd nomidan aniqlanadi.
- **Pastki / tepa / yon**: plaskart va kupe vagonlari. Bir nechtasini birga tanlash mumkin
  (masalan, "o'tirib ketadigan" + "pastki").
- Agar bot biror vagonni noto'g'ri tasniflasa, `/debug <id>` faylini yuboring, `BERTH_KW` / `SEATED_KW`
  ro'yxatlarini moslab beraman.

## Eslatma

`POLL_INTERVAL` ni 30 soniyadan kam qilmang, juda tez so'rov sayt tomonidan bloklanishiga olib kelishi mumkin.
Bot faqat bo'sh joylarni kuzatadi, bilet sotib olmaydi.
