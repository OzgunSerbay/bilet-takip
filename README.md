# ✈️ Bilet Takip

İstanbul Sabiha Gökçen'den (SAW) **Ordu-Giresun**'a (OGU) giden direkt uçuşları,
**valiz dahil** fiyatla 30 Aralık – 2 Ocak arası her saat takip eder.
(Önceki yolculuk: Hannover çevresi → İstanbul/Adana, 19–23 Aralık; geçmişi git kayıtlarında.)

- **Uygulama:** https://ozgunserbay.github.io/bilet-takip/ (Android'de Chrome → ⋮ → *Uygulamayı yükle*)
- **Bildirimler:** ntfy uygulamasında `ucak-haj-ist-0f8d133f` konusuna abone ol
- **Motor:** `ucak_takip.py`, GitHub Actions ile her saat başı çalışır (`.github/workflows/takip.yml`)

Ayarlar (şehirler, tarihler, valiz ücretleri) `ucak_takip.py` dosyasının başında.
Fiyatlar Google Uçuşlar'dan, TL.
