# ✈️ Bilet Takip

Hannover'dan tek trenle gidilebilen havalimanlarından **İstanbul** ve **Adana**'ya giden direkt uçuşları,
**20 kg valiz dahil** fiyatla 19–23 Aralık arası her saat takip eder.

- **Uygulama:** https://ozgunserbay.github.io/bilet-takip/ (Android'de Chrome → ⋮ → *Uygulamayı yükle*)
- **Bildirimler:** ntfy uygulamasında `ucak-haj-ist-0f8d133f` konusuna abone ol
- **Motor:** `ucak_takip.py`, GitHub Actions ile her saat başı çalışır (`.github/workflows/takip.yml`)

Ayarlar (şehirler, tarihler, valiz ücretleri) `ucak_takip.py` dosyasının başında.
Fiyatlar Google Uçuşlar'dan; tren bileti dahil değildir.
