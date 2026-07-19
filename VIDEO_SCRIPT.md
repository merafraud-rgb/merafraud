# MeraFraud — Tanıtım Videosu Senaryosu

Bu, ekran kaydı (OBS, Loom, Windows Xbox Game Bar — hepsi ücretsiz) ile
2-3 dakikalık bir tanıtım videosu çekmek için hazır bir senaryo. Her sahnede
ne gösterileceği ve ne söyleneceği (Türkçe/İngilizce, tercihinize göre) var.

**Toplam süre hedefi:** 2:30 - 3:00 dakika
**Kayıt sırası:** Aşağıdaki sırayla ekranları açıp gezinerek kaydedin, sonra
istersen CapCut/DaVinci Resolve (ikisi de ücretsiz) ile kesip birleştirin.

---

## Sahne 1 — Açılış (0:00–0:20)
**Ekran:** `website/index.html` (ana sayfa), yavaşça aşağı kaydırın
**Söylenecek:**
> "E-ticaret işletmeleri her gün dolandırıcılıkla mücadele ediyor —
> özellikle küçük ve orta ölçekli işletmelerin bunun için ne bütçesi ne de
> ekibi var. MeraFraud tam bunun için var: yapay zeka destekli, gerçek
> zamanlı bir dolandırıcılık tespit sistemi."

## Sahne 2 — Kayıt akışı (0:20–0:50)
**Ekran:** `website/pricing.html` → "Start Free Trial" veya `dashboard/signup.html`
**Yapılacak:** Mağaza adı yazın, bir risk profili seçin, "Create Account"a basın
**Söylenecek:**
> "Kayıt olmak saniyeler sürüyor. Mağaza adınızı yazıyorsunuz, risk
> toleransınızı seçiyorsunuz — Lenient, Standard ya da Strict — ve API
> anahtarınız anında oluşuyor. Kredi kartı yok, bekleme yok."

## Sahne 3 — API anahtarı ve ilk istek (0:50–1:10)
**Ekran:** Kayıt sonrası ekran, `curl` örneği görünen kutu
**Söylenecek:**
> "İşte bu — API anahtarınız hazır. Bu anahtarla, kendi mağazanızın
> checkout akışından tek bir istekle her işlemi anlık olarak
> puanlayabilirsiniz."

## Sahne 4 — Dashboard turu (1:10–1:50)
**Ekran:** `dashboard/index.html`
**Yapılacak:** Sırayla: üst istatistik kartları → dünya haritası → "İşlem Test Et" formunu doldurup "Riski Hesapla"ya basın
**Söylenecek:**
> "Dashboard'da her şeyi canlı görüyorsunuz: toplam işlem sayısı,
> engellenen dolandırıcılıklar, tahmini önlenen kayıp. Aşağıda, son
> işaretlenen işlemlerin dünya haritası var. Ve burada — gerçek bir
> işlem senaryosu kurup modelin ne karar verdiğini anında test
> edebilirsiniz."
**Vurgulanacak an:** Risk skorunun %99'a çıkıp "BLOCK" rozetinin
belirmesi — bu görsel olarak en etkileyici an, yavaşça durup gösterin.

## Sahne 5 — Açıklanabilirlik (1:50–2:10)
**Ekran:** Aynı sonuç kutusundaki gerekçe listesi ("İşlemler arası süre
çok kısa" gibi maddeler)
**Söylenecek:**
> "Ve en önemlisi: MeraFraud size sadece bir sayı vermiyor, *neden*
> riskli olduğunu da söylüyor. Ekibiniz her kararın arkasındaki mantığı
> görüyor — kara kutu değil."

## Sahne 6 — Risk ayarları (2:10–2:30)
**Ekran:** `dashboard/settings.html`, kaydırıcıları hareket ettirin
**Söylenecek:**
> "Her işletmenin risk toleransı farklıdır. Bu yüzden eşikleri
> istediğiniz zaman kendiniz ayarlayabilirsiniz — daha toleranslı ya da
> daha sıkı, tamamen sizin kontrolünüzde."

## Sahne 7 — Kapanış (2:30–2:45)
**Ekran:** `website/index.html`, "Get Your API Key — Free" butonuna
yaklaşın (tıklamadan)
**Söylenecek:**
> "MeraFraud — KOBİ'ler için yapay zeka destekli dolandırıcılık koruması.
> Ücretsiz başlayın, iki dakikada kurulun."

---

## Teknik notlar
- Kayıttan önce `python api\app.py`'yi çalıştırın, dashboard'un "API bağlı" gösterdiğinden emin olun
- Fare imlecini yavaş hareket ettirin, tıklamadan önce 1 saniye bekleyin (izleyici gözünü yetiştirsin)
- Tarayıcıyı tam ekran yapın, yer imleri çubuğunu gizleyin (Ctrl+Shift+B - Chrome)
- Ekran çözünürlüğünü en az 1920x1080 yapın
- Ses kaydı ayrı yapılıp sonradan eklenirse (voiceover), daha temiz olur — ekran kaydı sırasında konuşma zorunlu değil

## Ücretsiz araçlar
- **Ekran kaydı:** OBS Studio (Windows/Mac), ya da Windows'ta Win+G (Xbox Game Bar)
- **Kurgu/kesme:** CapCut (ücretsiz, kolay), DaVinci Resolve (daha profesyonel, ücretsiz)
- **Altyazı eklemek isterseniz:** CapCut otomatik altyazı çıkarabiliyor
