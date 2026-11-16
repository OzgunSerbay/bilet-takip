"""Bilet Takip uygulamasına doğrudan (web push) bildirim gönderme.

Akış:
  1. Telefonda uygulamada "Bildirimleri aç" -> tarayıcı bir abonelik (bildirim adresi) üretir
     ve bunu ntfy.sh üzerindeki gizli KAYIT_KONU'na gönderir.
  2. GitHub'daki saatlik kontrol bu konudaki yeni abonelikleri alır, abonelikler.enc dosyasında
     ŞİFRELİ olarak saklar (depo herkese açık olduğu için).
  3. bildir() çağrıldığında her aboneliğe web push gönderilir.

Gerekenler: VAPID_PRIVATE ortam değişkeni (GitHub secret). Yoksa bu modül sessizce hiçbir şey yapmaz.
"""

import base64
import hashlib
import json
import os
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

KAYIT_KONU = "bilet-takip-kayit-190dd53c21394ec6"
VAPID_PRIVATE = os.environ.get("VAPID_PRIVATE", "").strip()
VAPID_ILETISIM = "https://ozgunserbay.github.io"       # push servisine "gönderen" bilgisi (sadece alan adı)
UYGULAMA_ADRESI = "https://ozgunserbay.github.io/bilet-takip/"
DEPO = Path(__file__).resolve().parent / "abonelikler.enc"
MAKS_ABONE = 20
IZINLI_SUNUCULAR = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com",
                    "notify.windows.com", "push.services.mozilla.com", "android.googleapis.com")


def aktif() -> bool:
    return bool(VAPID_PRIVATE)


def _fernet():
    from cryptography.fernet import Fernet
    anahtar = base64.urlsafe_b64encode(hashlib.sha256(b"bilet-takip-abonelik:" + VAPID_PRIVATE.encode()).digest())
    return Fernet(anahtar)


def _yukle() -> dict:
    if DEPO.exists():
        try:
            return json.loads(_fernet().decrypt(DEPO.read_bytes()))
        except Exception:
            pass
    return {"abonelikler": [], "son_mesaj": None}


def _kaydet(depo: dict) -> None:
    DEPO.write_bytes(_fernet().encrypt(json.dumps(depo).encode()))


def _gecerli(abonelik) -> bool:
    try:
        host = urlparse(abonelik["endpoint"]).hostname or ""
        return (abonelik["endpoint"].startswith("https://")
                and any(host == s or host.endswith("." + s) for s in IZINLI_SUNUCULAR)
                and isinstance(abonelik["keys"]["p256dh"], str) and isinstance(abonelik["keys"]["auth"], str))
    except (KeyError, TypeError):
        return False


def _tek_gonder(abonelik: dict, baslik: str, metin: str, link: str | None) -> str:
    """'ok', 'sil' (abonelik artık geçersiz) ya da 'hata' döndürür."""
    from pywebpush import WebPushException, webpush
    try:
        webpush(
            subscription_info=abonelik,
            data=json.dumps({"baslik": baslik, "metin": metin, "link": link or UYGULAMA_ADRESI}, ensure_ascii=False),
            vapid_private_key=VAPID_PRIVATE,
            vapid_claims={"sub": VAPID_ILETISIM},
            ttl=3 * 3600,
            timeout=15,
        )
        return "ok"
    except WebPushException as e:
        kod = getattr(e.response, "status_code", None)
        return "sil" if kod in (400, 403, 404, 410) else f"hata {kod}"
    except Exception as e:  # ağ hatası vb.
        return f"hata {e!r}"


def abonelikleri_guncelle(log) -> None:
    """ntfy kayıt konusundaki yeni abone ol/çık isteklerini işler."""
    if not aktif():
        return
    depo = _yukle()
    since = depo.get("son_mesaj") or "all"
    try:
        with urllib.request.urlopen(f"https://ntfy.sh/{KAYIT_KONU}/json?poll=1&since={since}", timeout=20) as r:
            satirlar = r.read().decode("utf-8").splitlines()
    except Exception as e:
        log(f"Bildirim abonelikleri alınamadı: {e!r}")
        return
    yeni = []
    for satir in satirlar:
        try:
            m = json.loads(satir)
        except ValueError:
            continue
        if m.get("event") != "message":
            continue
        depo["son_mesaj"] = m["id"]
        try:
            istek = json.loads(m.get("message", ""))
            abonelik = istek["abonelik"]
        except (ValueError, KeyError, TypeError):
            continue
        if not _gecerli(abonelik):
            continue
        abonelik = {"endpoint": abonelik["endpoint"], "keys": {"p256dh": abonelik["keys"]["p256dh"], "auth": abonelik["keys"]["auth"]}}
        depo["abonelikler"] = [a for a in depo["abonelikler"] if a["endpoint"] != abonelik["endpoint"]]
        if istek.get("islem") == "sil":
            log("Bir cihaz bildirimleri kapattı.")
            yeni = [a for a in yeni if a["endpoint"] != abonelik["endpoint"]]
        else:
            depo["abonelikler"].append(abonelik)
            yeni.append(abonelik)
    depo["abonelikler"] = depo["abonelikler"][-MAKS_ABONE:]
    for a in yeni:
        sonuc = _tek_gonder(a, "✅ Bilet Takip bildirimleri hazır", "Kayıt tamamlandı. Fiyat düşünce ve her saat başı buraya bildirim gelecek.", None)
        log(f"Yeni cihaz bildirime abone oldu (hoş geldin bildirimi: {sonuc}).")
        if sonuc == "sil":
            depo["abonelikler"] = [x for x in depo["abonelikler"] if x["endpoint"] != a["endpoint"]]
    _kaydet(depo)
    log(f"Uygulama bildirimi abone sayısı: {len(depo['abonelikler'])}")


def gonder(baslik: str, metin: str, link: str | None, log) -> None:
    if not aktif():
        return
    depo = _yukle()
    if not depo["abonelikler"]:
        return
    kalan = []
    for a in depo["abonelikler"]:
        sonuc = _tek_gonder(a, baslik, metin, link)
        if sonuc == "sil":
            log("Geçersiz bir bildirim aboneliği silindi.")
            continue
        if sonuc != "ok":
            log(f"Uygulama bildirimi gönderilemedi: {sonuc}")
        kalan.append(a)
    if len(kalan) != len(depo["abonelikler"]):
        depo["abonelikler"] = kalan
        _kaydet(depo)
