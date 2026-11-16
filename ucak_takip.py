"""Uçak bileti fiyat takipçisi (20 kg valiz dahil, sadece direkt uçuşlar).

Kalkış: Hannover'dan tek trenle (aktarmasız) gidilebilen şehirlerin havalimanları
Varış:  İstanbul (IST, SAW) ve Adana (Çukurova COV, Şakirpaşa ADA)
Tarih:  19-23 Aralık

Google Flights'tan tüm direkt uçuşları çeker, her uçuş için "1 büyük valiz dahil"
fiyatı bulur ve en ucuz valizli fiyat düştüğünde Windows bildirimi (isteğe bağlı
olarak telefona da) gönderir.

Valizli fiyat nasıl bulunuyor:
  1. Google'ın rezervasyon sayfasında havayolunun "kayıtlı bagaj ücretsiz"
     paketi varsa (Pegasus Saver gibi) onun fiyatı alınır  -> kesin fiyat
  2. Yoksa (AJet gibi) bilet fiyatına aşağıdaki VALIZ_UCRETI eklenir -> tahmini

Not: Tren bileti fiyata dahil DEĞİL. Tren süreleri yaklaşıktır.

Kullanım:
    py ucak_takip.py            -> şimdi bir kez kontrol et
    py ucak_takip.py --gecmis   -> kaydedilen fiyat geçmişini göster
    py ucak_takip.py --kur      -> otomatik kontrolü Windows'a kur (her saat başı)
    py ucak_takip.py --kaldir   -> otomatik kontrolü kaldır
    py ucak_takip.py --test     -> deneme bildirimi gönder
"""

import base64
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from primp import Client
from selectolax.lexbor import LexborHTMLParser

# ---------------- AYARLAR ----------------
# Hannover Hbf'den aktarmasız trenle gidilebilen şehirlerin havalimanları.
# Kod -> (şehir, Hannover'dan yaklaşık tren süresi). İstemediğin satırı silebilir ya da başına # koyabilirsin.
KALKIS = {
    "HAJ": ("Hannover", "S-Bahn 18 dk"),
    "BRE": ("Bremen", "~1 sa"),
    "KSF": ("Kassel", "~1 sa"),
    "HAM": ("Hamburg", "~1 sa 20 dk"),
    "BER": ("Berlin", "~1 sa 40 dk"),
    "DTM": ("Dortmund", "~1 sa 45 dk"),
    "FRA": ("Frankfurt", "~2 sa 20 dk"),
    "DUS": ("Düsseldorf", "~2 sa 30 dk"),
    "CGN": ("Köln", "~2 sa 45 dk"),
}
VARIS = {
    "IST": "İstanbul",
    "SAW": "İstanbul",
    "COV": "Adana",   # Çukurova Havalimanı (Adana uçuşları 2024'ten beri buraya iniyor)
    "ADA": "Adana",   # Şakirpaşa (şu an tarifeli uçuş yok, gelirse otomatik görünür)
}
ASIL_TARIH = "2026-12-21"
GUN_ESNEKLIGI = 2               # Asıl tarihin 2 gün öncesi ve sonrası da aranır (19-23 Aralık)
PARA_BIRIMI = "EUR"
HEDEF_FIYAT = 0                 # Örn. 200 yaparsan valizli fiyat 200'ün altına inince her seferinde haber verir (0 = kapalı)
SADECE_DIREKT = True            # True: sadece aktarmasız uçuşlar
KONTROL_SAAT_ARALIGI = 1        # --kur ile kaç saatte bir kontrol edilsin
HER_KONTROLDE_OZET = True       # True: fiyat değişmese de her kontrolde kısa özet bildirimi gönder

# Google valizli paket fiyatı göstermezse bilete eklenecek tahmini 20 kg valiz ücreti (€, tek yön, online).
# Havayolu kodu -> ücret. Gerçek ücreti öğrenirsen buradan düzeltebilirsin.
VALIZ_UCRETI = {
    "VF": 30,   # AJet / Anadolu Jet (tahmini; kaynaklar 25-35 € diyor)
    "PC": 35,   # Pegasus (genelde Google'da Saver paketi çıkıyor, bu sadece yedek)
}
VARSAYILAN_VALIZ_UCRETI = 50    # Listede olmayan havayolları için

# Telefona bildirim (isteğe bağlı): telefona "ntfy" uygulamasını kur,
# uygulamada aşağıdaki konuya abone ol. Boş bırakırsan sadece bilgisayara bildirim gelir.
NTFY_KONU = os.environ.get("NTFY_KONU") or "ucak-haj-ist-0f8d133f"
ULKE = "DE"                     # Google'a hangi ülkeden bilet alınacağını söyler (arkadaşın Almanya'da)
# -----------------------------------------

BULUT = os.environ.get("GITHUB_ACTIONS") == "true"   # GitHub'da çalışırken Windows'a özel şeyler kapalı

KLASOR = Path(__file__).resolve().parent
GECMIS_DOSYASI = KLASOR / "fiyat_gecmisi.json"
LOG_DOSYASI = KLASOR / "takip.log"
VERI_DOSYASI = KLASOR / "docs" / "veri.json"   # Telefon uygulamasının okuduğu dosya
MAKS_KAYIT = 1500                               # Geçmişte tutulacak en fazla kontrol sayısı
GOREV_ADI = "UcakFiyatTakip_HAJ_IST"
AYLAR = ["", "Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]
MAKS_REZERVASYON_SAYFASI = 45   # Bir kontrolde en fazla kaç uçuşun valizli fiyatına bakılsın
GRUP_BOYUTU = 7                 # Google bir aramada en fazla 7 kalkış havalimanı kabul ediyor
GECMIS_SURUMU = 3


def tarihler() -> list[str]:
    merkez = dt.date.fromisoformat(ASIL_TARIH)
    return [(merkez + dt.timedelta(days=d)).isoformat() for d in range(-GUN_ESNEKLIGI, GUN_ESNEKLIGI + 1)]


def tarih_kisa(t: str) -> str:
    d = dt.date.fromisoformat(t)
    return f"{d.day} {AYLAR[d.month]}"


def log(mesaj: str) -> None:
    satir = f"[{dt.datetime.now():%Y-%m-%d %H:%M}] {mesaj}"
    try:
        print(satir)
    except Exception:
        pass  # pythonw ile konsol yok
    with LOG_DOSYASI.open("a", encoding="utf-8") as f:
        f.write(satir + "\n")


def gecmis_oku() -> dict:
    if GECMIS_DOSYASI.exists():
        g = json.loads(GECMIS_DOSYASI.read_text(encoding="utf-8"))
        if g.get("surum") == GECMIS_SURUMU:
            return g
    return {"surum": GECMIS_SURUMU, "en_dusuk": None, "son": None, "kayitlar": []}


def gecmis_yaz(g: dict) -> None:
    GECMIS_DOSYASI.write_text(json.dumps(g, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- Google Flights adresleri (protobuf) ----------

def _varint(n: int) -> bytes:
    if n < 0:
        n += 1 << 64
    out = b""
    while True:
        b, n = n & 0x7F, n >> 7
        if n:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def _int(no: int, v: int) -> bytes:
    return _varint(no << 3) + _varint(v)


def _bytes(no: int, b) -> bytes:
    b = b.encode() if isinstance(b, str) else b
    return _varint((no << 3) | 2) + _varint(len(b)) + b


def _tfs(ucus_verisi: bytes) -> str:
    info = (_int(1, 28) + _int(2, 2) + _bytes(3, ucus_verisi) + _int(8, 1) + _int(9, 1) + _int(14, 1)
            + _bytes(16, _int(1, -1)) + _int(19, 2))
    return base64.urlsafe_b64encode(info).decode().rstrip("=")


def arama_tfs(tarih: str, kalkislar: list[str], varislar: list[str]) -> str:
    fd = _bytes(2, tarih)
    if SADECE_DIREKT:
        fd += _int(5, 0)
    fd += b"".join(_bytes(13, _int(1, 1) + _bytes(2, k)) for k in kalkislar)
    fd += b"".join(_bytes(14, _int(1, 1) + _bytes(2, v)) for v in varislar)
    return _tfs(fd)


def rezervasyon_linki(u: dict) -> str:
    bacaklar = b"".join(
        _bytes(4, _bytes(1, a) + _bytes(2, t) + _bytes(3, v) + _bytes(5, kod) + _bytes(6, no))
        for a, t, v, kod, no in u["bacaklar"]
    )
    fd = (_bytes(2, u["tarih"]) + bacaklar
          + _bytes(13, _int(1, 1) + _bytes(2, u["nereden"])) + _bytes(14, _int(1, 1) + _bytes(2, u["nereye"])))
    return f"https://www.google.com/travel/flights/booking?tfs={_tfs(fd)}&hl=tr&gl={ULKE}&curr={PARA_BIRIMI}"


# ---------- Google Flights: uçuş listesi ----------

_istemci = None


def istemci() -> Client:
    global _istemci
    if _istemci is None:
        _istemci = Client(impersonate="chrome_145", impersonate_os="macos", referer=True, cookie_store=True)
    return _istemci


def saat(t) -> str:
    t = [*(t or []), None, None]
    return f"{t[0] or 0:02d}:{t[1] or 0:02d}"


def grup_ara(tarih: str, kalkislar: list[str], varislar: list[str]) -> tuple[list[dict], bool]:
    """Bir havalimanı grubunu arar; Google hata verirse grubu ikiye bölüp tekrar dener.
    (uçuşlar, eksik_mi) döndürür."""
    try:
        sonuc = ucuslari_getir(tarih, kalkislar, varislar)
    except Exception as e:
        log(f"{tarih} {','.join(kalkislar)} için fiyat alınamadı: {e!r}")
        sonuc = None
    if sonuc is not None:
        return sonuc, False
    if len(kalkislar) == 1:
        log(f"{tarih} {kalkislar[0]} için Google hata verdi, bu kontrolde atlandı")
        return [], True
    yari = len(kalkislar) // 2
    a, eksik_a = grup_ara(tarih, kalkislar[:yari], varislar)
    b, eksik_b = grup_ara(tarih, kalkislar[yari:], varislar)
    return a + b, eksik_a or eksik_b


def ucuslari_getir(tarih: str, kalkislar: list[str], varislar: list[str]) -> list[dict] | None:
    r = istemci().get("https://www.google.com/travel/flights/search",
                      params={"tfs": arama_tfs(tarih, kalkislar, varislar), "hl": "tr", "gl": ULKE, "curr": PARA_BIRIMI})
    script = LexborHTMLParser(r.text).css_first(r"script.ds\:1")
    if script is None:
        raise RuntimeError(f"Google sayfasında uçuş verisi yok (HTTP {r.status_code}, {str(r.url)[:80]})")
    veri = script.text().split("data:", 1)[1].rsplit(",", 1)[0]
    if veri.endswith("errorHasStatus: true"):
        return None  # Google hata döndü (grup bölünüp tekrar denenir)
    payload = json.loads(veri)
    tekil = {}
    for grup in (payload[2], payload[3]):  # "en iyi" + "diğer" uçuşlar
        if not grup or not grup[0]:
            continue
        for k in grup[0]:
            try:
                ucus, fiyat = k[0], k[1][0][1]
                seg = ucus[2]
                bacaklar = [(s[3], "%04d-%02d-%02d" % tuple(s[20]), s[6], s[22][0], s[22][1]) for s in seg]
            except (IndexError, TypeError, KeyError):
                continue
            if not fiyat or (SADECE_DIREKT and len(seg) > 1):
                continue
            u = {
                "tarih": tarih,
                "fiyat": fiyat,
                "havayolu": ", ".join(ucus[1]),
                "kod": seg[0][22][0],
                "ucus_no": " + ".join(f"{b[3]}{b[4]}" for b in bacaklar),
                "nereden": seg[0][3],
                "nereye": seg[-1][6],
                "kalkis": saat(seg[0][8]),
                "varis": saat(seg[-1][10]),
                "aktarma": len(seg) - 1,
                "bacaklar": bacaklar,
            }
            anahtar = (u["tarih"], u["ucus_no"])
            if anahtar not in tekil or fiyat < tekil[anahtar]["fiyat"]:
                tekil[anahtar] = u
    return list(tekil.values())


# ---------- Google Flights: rezervasyon sayfası (valizli paketler) ----------

def _fiyat(metin: str) -> int | None:
    m = re.search(r"€\s?([\d.]+)", metin)
    return int(m.group(1).replace(".", "")) if m else None


def valizli_fiyat_sayfadan(sayfa, u: dict) -> tuple[int | None, str]:
    """Rezervasyon sayfasından valiz dahil en ucuz paketi bulur. (fiyat, paket adı)"""
    for deneme in range(2):  # Google bazen "Maalesef bir sorun oluştu" diyor: bir kez daha dene
        sayfa.goto(rezervasyon_linki(u), wait_until="networkidle", timeout=45000)
        try:
            sayfa.wait_for_selector("text=Rezervasyon seçenekleri", timeout=15000)
            break
        except Exception:
            if deneme == 0:
                sayfa.wait_for_timeout(3000)
                continue
            ozet_metin = " ".join(sayfa.inner_text("body").split())[:160]
            log(f"{u['ucus_no']} ({u['tarih']}) valizli paketler okunamadı, tahmini ücret kullanılacak | {ozet_metin}")
            return None, ""
    metin = sayfa.inner_text("body")
    bas = metin.find("Rezervasyon seçenekleri")
    son = metin.find("Fiyatlara 1 yetişkin", bas)
    ust = metin[:bas]
    secenekler = metin[bas:son if son > 0 else None]

    en_iyi, paket = None, ""
    # Seçenek blokları "Devam" ile bitiyor
    for blok in secenekler.split("Devam"):
        if "kayıtlı bagaj ücretsiz" in blok or "kayıtlı bagaj dahil" in blok:
            f = _fiyat(blok)
            if f and (en_iyi is None or f < en_iyi):
                satirlar = [s.strip() for s in blok.strip().splitlines() if s.strip()]
                ad = next((satirlar[i - 1] for i, s in enumerate(satirlar) if s.startswith("€") and i > 0), "")
                en_iyi, paket = f, ad
    # Paket yok ama uçuşun kendisi zaten valizli ise (örn. THY bazı tarifeler)
    if en_iyi is None and ("kayıtlı bagaj ücretsiz" in ust or "kayıtlı bagaj dahil" in ust):
        en_iyi, paket = u["fiyat"], "valiz dahil"
    return en_iyi, paket


def tahmini_valizli(u: dict) -> int:
    return u["fiyat"] + VALIZ_UCRETI.get(u["kod"], VARSAYILAN_VALIZ_UCRETI)


def varis_sehri(u: dict) -> str:
    return VARIS.get(u["nereye"], u["nereye"])


def valizli_fiyatlari_hesapla(ucuslar: list[dict]) -> None:
    """Her uçuşa 'valizli' ve 'valiz_kaynak' ekler. Ucuzdan pahalıya bakar, gereksizleri atlar."""
    ucuslar.sort(key=lambda x: x["fiyat"])
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sync_playwright = None
        log("playwright yüklü değil, tüm valiz ücretleri tahmini hesaplanacak")

    def tahmini_doldur(liste):
        for u in liste:
            u.setdefault("valizli", tahmini_valizli(u))
            u.setdefault("valiz_kaynak", "tahmini")

    if sync_playwright is None:
        tahmini_doldur(ucuslar)
        return

    en_iyi = {}  # (tarih, varış şehri) -> en ucuz valizli fiyat
    bakilan = 0
    with sync_playwright() as p:
        tarayici = None
        for kanal in ("msedge", "chrome", None):
            try:
                tarayici = p.chromium.launch(channel=kanal, headless=True) if kanal else p.chromium.launch(headless=True)
                break
            except Exception:
                continue
        if tarayici is None:
            log("Tarayıcı açılamadı, tüm valiz ücretleri tahmini hesaplanacak")
            tahmini_doldur(ucuslar)
            return
        sayfa = tarayici.new_page(locale="tr-TR")
        for u in ucuslar:
            anahtar = (u["tarih"], varis_sehri(u))
            # Valizli fiyat bilet fiyatından düşük olamaz: bilet fiyatı o gün/şehrin en iyisinden pahalıysa bakmaya gerek yok
            if anahtar in en_iyi and u["fiyat"] >= en_iyi[anahtar]:
                continue
            if bakilan >= MAKS_REZERVASYON_SAYFASI:
                break
            bakilan += 1
            try:
                f, paket = valizli_fiyat_sayfadan(sayfa, u)
            except Exception as e:
                log(f"{u['ucus_no']} rezervasyon sayfası okunamadı: {e!r}")
                f, paket = None, ""
            if f is not None:
                u["valizli"], u["valiz_kaynak"] = f, f"Google: {paket}".strip()
            else:
                u["valizli"], u["valiz_kaynak"] = tahmini_valizli(u), "tahmini"
            if anahtar not in en_iyi or u["valizli"] < en_iyi[anahtar]:
                en_iyi[anahtar] = u["valizli"]
        tarayici.close()
    tahmini_doldur(ucuslar)


# ---------- Bildirimler ----------

def windows_bildirimi(baslik: str, metin: str, link: str | None = None) -> None:
    def esc(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                 .replace('"', "&quot;").replace("'", "&apos;")
                 .replace("‘", "&apos;").replace("’", "&apos;"))

    eylem = f'<actions><action content="Bileti gör" activationType="protocol" arguments="{esc(link)}"/></actions>' if link else ""
    tik = f' activationType="protocol" launch="{esc(link)}"' if link else ""
    xml = (f'<toast{tik}><visual><binding template="ToastGeneric">'
           f"<text>{esc(baslik)}</text><text>{esc(metin)}</text>"
           f"</binding></visual>{eylem}</toast>")
    ps = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml('{xml}')
$app = '{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($x))
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def telefon_bildirimi(baslik: str, metin: str, link: str | None = None) -> None:
    if not NTFY_KONU:
        return
    try:
        istek = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_KONU}",
            data=metin.encode("utf-8"),
            headers={"Title": baslik.encode("utf-8"), "Tags": "airplane", **({"Click": link} if link else {})},
            method="POST",
        )
        urllib.request.urlopen(istek, timeout=15)
    except Exception as e:
        log(f"Telefon bildirimi gönderilemedi: {e!r}")


def bildir(baslik: str, metin: str, link: str | None = None) -> None:
    if not BULUT:
        windows_bildirimi(baslik, metin, link)
    telefon_bildirimi(baslik, metin, link)


# ---------- Ana kontrol ----------

def kalkis_sehri(u: dict) -> str:
    return KALKIS.get(u["nereden"], (u["nereden"], ""))[0]


def ozet(u: dict) -> str:
    sehir, tren = KALKIS.get(u["nereden"], (u["nereden"], "?"))
    kaynak = "tahmini valiz ücretiyle" if u["valiz_kaynak"] == "tahmini" else u["valiz_kaynak"].replace("Google: ", "") + " paketi"
    return (f"{tarih_kisa(u['tarih'])} · {u['havayolu']} · {sehir} ({u['nereden']}) → {varis_sehri(u)} ({u['nereye']}) · "
            f"{u['kalkis']}-{u['varis']} · 🚆 Hannover'dan {tren} · {kaynak} (valizsiz {u['fiyat']} €)")


def kisa(u: dict) -> str:
    return f"{u['valizli']} € {tarih_kisa(u['tarih'])} {kalkis_sehri(u)}→{u['nereye']} {u['havayolu']} {u['kalkis']}"


def uygulama_verisi_yaz(ucuslar: list[dict], g: dict) -> None:
    """Telefon uygulaması için docs/veri.json dosyasını yazar."""
    def sade(u: dict) -> dict:
        sehir, tren = KALKIS.get(u["nereden"], (u["nereden"], ""))
        return {
            "tarih": u["tarih"], "nereden": u["nereden"], "nereden_sehir": sehir, "tren": tren,
            "nereye": u["nereye"], "varis_sehir": varis_sehri(u), "havayolu": u["havayolu"],
            "ucus_no": u["ucus_no"], "kalkis": u["kalkis"], "varis": u["varis"],
            "fiyat": u["fiyat"], "valizli": u["valizli"],
            "kesin": u["valiz_kaynak"] != "tahmini",
            "paket": u["valiz_kaynak"].replace("Google: ", "") if u["valiz_kaynak"] != "tahmini" else "",
            "link": rezervasyon_linki(u),
        }

    gunler: dict = {}
    sehirler: dict = {}
    for u in ucuslar:  # ucuzdan pahalıya sıralı
        gunler.setdefault(u["tarih"], {}).setdefault(varis_sehri(u), sade(u))
        sehirler.setdefault(varis_sehri(u), sade(u))
    veri = {
        "guncelleme": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "tarihler": tarihler(),
        "asil_tarih": ASIL_TARIH,
        "kalkis": {k: list(v) for k, v in KALKIS.items()},
        "taranan": len(ucuslar),
        "en_ucuz": sade(ucuslar[0]),
        "en_dusuk_gorulen": g["en_dusuk"],
        "sehirler": sehirler,
        "gunler": gunler,
        "ucuslar": [sade(u) for u in ucuslar[:40]],
        "gecmis": [{"t": k["zaman"], "f": k["fiyat"], "s": k.get("sehir_fiyat", {})} for k in g["kayitlar"]],
        "ntfy": NTFY_KONU,
    }
    VERI_DOSYASI.parent.mkdir(exist_ok=True)
    VERI_DOSYASI.write_text(json.dumps(veri, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def kontrol() -> None:
    bugun = dt.date.today()
    gunler = [t for t in tarihler() if dt.date.fromisoformat(t) >= bugun]
    if not gunler:
        log("Tüm uçuş tarihleri geçti, otomatik kontrol kaldırılıyor.")
        kaldir()
        return

    kodlar = list(KALKIS)
    gruplar = [kodlar[i:i + GRUP_BOYUTU] for i in range(0, len(kodlar), GRUP_BOYUTU)]
    ucuslar = []
    eksik = False
    for t in gunler:
        for grup in gruplar:
            yeni, grup_eksik = grup_ara(t, grup, list(VARIS))
            eksik = eksik or grup_eksik
            ucuslar += yeni
    if not ucuslar:
        log("Hiç uçuş/fiyat bulunamadı (Google cevap vermemiş olabilir), sonraki kontrolde tekrar denenecek.")
        return

    valizli_fiyatlari_hesapla(ucuslar)
    ucuslar.sort(key=lambda x: x["valizli"])
    en_ucuz = ucuslar[0]
    fiyat = en_ucuz["valizli"]
    link = rezervasyon_linki(en_ucuz)

    gun_bazli, sehir_bazli = {}, {}
    for u in ucuslar:  # zaten ucuzdan pahalıya sıralı
        gun_bazli.setdefault(u["tarih"], u["valizli"])
        sehir_bazli.setdefault(varis_sehri(u), u)

    g = gecmis_oku()
    onceki_en_dusuk, onceki_son = g["en_dusuk"], g["son"]
    if eksik and (onceki_son is None or fiyat >= onceki_son):
        # Bazı şehirler taranamadı: bulunan fiyat gerçek en ucuz olmayabilir, yanlış "arttı" demeyelim.
        # (Daha ucuz bir fiyat bulunduysa o gerçektir, aşağıda normal şekilde bildirilir.)
        log(f"Tarama eksik kaldı (bulunan en ucuz {fiyat} €), kayıt ve bildirim bu sefer atlandı.")
        return
    g["kayitlar"].append({
        "zaman": dt.datetime.now().isoformat(timespec="minutes"),
        "fiyat": fiyat,
        "ucus": ozet(en_ucuz),
        "gunler": {tarih_kisa(t): f for t, f in sorted(gun_bazli.items())},
        "sehirler": {s: kisa(u) for s, u in sehir_bazli.items()},
        "sehir_fiyat": {s: u["valizli"] for s, u in sehir_bazli.items()},
    })
    g["kayitlar"] = g["kayitlar"][-MAKS_KAYIT:]
    g["son"] = fiyat
    if onceki_en_dusuk is None or fiyat < onceki_en_dusuk:
        g["en_dusuk"] = fiyat
    gecmis_yaz(g)
    uygulama_verisi_yaz(ucuslar, g)

    gun_yazi = " | ".join(f"{tarih_kisa(t)}: {f}€" for t, f in sorted(gun_bazli.items()))
    sehir_yazi = " · ".join(f"{s}: {kisa(u)}" for s, u in sehir_bazli.items())
    log(f"En ucuz valizli: {fiyat} {PARA_BIRIMI} — {ozet(en_ucuz)} (önceki: {onceki_son}, en düşük: {onceki_en_dusuk})")
    log(f"Varış şehrine göre: {sehir_yazi}")
    log(f"Günlere göre: {gun_yazi}  ({len(ucuslar)} direkt uçuş tarandı)")

    if onceki_en_dusuk is None:
        bildir(f"✈️ Takip başladı: valizli {fiyat} €", f"{ozet(en_ucuz)}\n{sehir_yazi}", link)
    elif fiyat < onceki_en_dusuk:
        bildir(f"🔥 Fiyat düştü! {onceki_en_dusuk} € → {fiyat} € (valizli)",
               f"Şimdiye kadarki en ucuz. {ozet(en_ucuz)}", link)
    elif onceki_son is not None and fiyat < onceki_son:
        bildir(f"⬇️ Fiyat indi: {onceki_son} € → {fiyat} € (valizli)",
               f"(En düşük görülen: {onceki_en_dusuk} €) {ozet(en_ucuz)}", link)
    elif HEDEF_FIYAT and fiyat <= HEDEF_FIYAT:
        bildir(f"🎯 Hedefin altında: valizli {fiyat} €", ozet(en_ucuz), link)
    elif HER_KONTROLDE_OZET:
        if onceki_son is not None and fiyat > onceki_son:
            durum = f"⬆️ arttı ({onceki_son} € → {fiyat} €)"
        else:
            durum = "değişiklik yok"
        bildir(f"🕐 {dt.datetime.now():%H:%M} kontrolü: valizli en ucuz {fiyat} € ({durum})", sehir_yazi, link)


def gecmisi_goster() -> None:
    g = gecmis_oku()
    if not g["kayitlar"]:
        print("Henüz kayıt yok.")
        return
    for k in g["kayitlar"]:
        print(f"{k['zaman']}  {k['fiyat']:>5} {PARA_BIRIMI}  {k['ucus']}")
        for s, y in k.get("sehirler", {}).items():
            print(f"                  {s}: {y}")
        print("                  Günler: " + " | ".join(f"{t}: {f}€" for t, f in k["gunler"].items()))
    print(f"\nEn düşük görülen valizli fiyat: {g['en_dusuk']} {PARA_BIRIMI}")


def kur() -> None:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    calistir = pythonw if pythonw.exists() else Path(sys.executable)
    betik = Path(__file__).resolve()
    ps = f"""
$a = New-ScheduledTaskAction -Execute '{calistir}' -Argument '"{betik}"' -WorkingDirectory '{betik.parent}'
$t = New-ScheduledTaskTrigger -Once -At (Get-Date -Minute 0 -Second 0).AddHours(1) -RepetitionInterval (New-TimeSpan -Hours {KONTROL_SAAT_ARALIGI})
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName '{GOREV_ADI}' -Action $a -Trigger $t -Settings $s -Force | Out-Null
"Kuruldu: her {KONTROL_SAAT_ARALIGI} saatte bir kontrol edilecek. Sonraki: " + (Get-ScheduledTaskInfo -TaskName '{GOREV_ADI}').NextRunTime
"""
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       capture_output=True, text=True)
    print(r.stdout or r.stderr)


def kaldir() -> None:
    if BULUT:
        return
    subprocess.run(["schtasks", "/Delete", "/F", "/TN", GOREV_ADI], capture_output=True)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--gecmis":
        gecmisi_goster()
    elif arg == "--kur":
        kur()
    elif arg == "--kaldir":
        kaldir()
        print("Otomatik kontrol kaldırıldı.")
    elif arg == "--test":
        bildir("✈️ Deneme bildirimi", "Bildirimler çalışıyor!", "https://www.google.com/travel/flights")
    else:
        try:
            kontrol()
        except Exception as e:
            log(f"Hata: {e!r}")
            raise
