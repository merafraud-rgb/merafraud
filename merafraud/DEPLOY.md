# MeraFraud'u İnternete Yayınlama Rehberi (Deploy)

Bu rehber, hiç deploy deneyimi olmayan biri için, MeraFraud API'sini
**Render.com**'un ücretsiz katmanında herkesin erişebileceği gerçek bir
internet adresine (`https://merafraud-api.onrender.com` gibi) taşımayı
anlatır. Her adımda ne yaptığınızı ve neden yaptığınızı açıklıyorum.

**Toplam süre:** ~20-30 dakika (çoğu bekleme süresi)
**Maliyet:** €0 (ücretsiz katman)

---

## Neden GitHub Gerekiyor?

Render, kodunuzu doğrudan bilgisayarınızdan almaz — bir GitHub deposundan
(repository) çeker. Yani önce kodu GitHub'a yükleyeceğiz, sonra Render'a
"bu depodaki kodu çalıştır" diyeceğiz.

---

## BÖLÜM 1 — GitHub'a Kodu Yükleme

### 1.1 GitHub hesabı oluşturun (yoksa)
[github.com](https://github.com) adresine gidin, **Sign up** ile ücretsiz
hesap açın.

### 1.2 GitHub Desktop'ı indirin
Komut satırı kullanmadan (git komutları yazmadan) dosya yüklemenin en
kolay yolu budur.
- [desktop.github.com](https://desktop.github.com) adresinden indirip kurun
- Açtığınızda GitHub hesabınızla giriş yapın

### 1.3 Yeni bir depo (repository) oluşturun
1. GitHub Desktop'ta **File > New Repository**
2. **Name**: `merafraud` yazın
3. **Local Path**: `merafraud` klasörünüzün **bir üst dizinini** seçin
   (örneğin klasörünüz `Desktop\merafraud yeni\merafraud_mvp\merafraud`
   ise, `Local Path` olarak `Desktop\merafraud yeni\merafraud_mvp` seçin
   — GitHub Desktop içindeki `merafraud` klasörünü otomatik depo yapacak)
4. **Create Repository**

> Eğer klasör zaten var diye hata alırsanız: File > Add Local Repository
> ile mevcut `merafraud` klasörünü doğrudan seçin.

### 1.4 Kodu GitHub'a gönderin (push)
1. GitHub Desktop'ta sol tarafta değişen dosyaların listesini göreceksiniz
2. Alt kısımdaki **Summary** kutusuna kısa bir açıklama yazın: `İlk yükleme`
3. **Commit to main** butonuna basın
4. Üstteki **Publish repository** butonuna basın
   - "Keep this code private" kutusunu işaretli bırakabilirsiniz (ücretsiz)
5. Birkaç saniye içinde kodunuz GitHub'da olacak

✅ Kontrol: [github.com](https://github.com) adresinde giriş yapıp
profilinizden `merafraud` deposunu görebiliyorsanız bu adım tamam.

---

## BÖLÜM 2 — Render'da Deploy

### 2.1 Render hesabı oluşturun
[render.com](https://render.com) → **Get Started** → GitHub hesabınızla
giriş yapın (en kolayı bu, otomatik bağlanır).

### 2.2 Yeni Web Service oluşturun
1. Render Dashboard'da **New +** → **Web Service**
2. GitHub hesabınız bağlıysa `merafraud` deponuzu listede göreceksiniz,
   **Connect**'e basın
3. Şu ayarları girin:

| Alan | Değer |
|---|---|
| **Name** | `merafraud-api` (ya da istediğiniz isim) |
| **Region** | Frankfurt (Avrupa'ya en yakın, düşük gecikme) |
| **Branch** | `main` |
| **Root Directory** | *boş bırakın* |
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `gunicorn --chdir api app:app` |
| **Instance Type** | Free |

4. En altta **Create Web Service**'e basın

### 2.3 Bekleyin
Render otomatik olarak kodunuzu indirip kuracak. Bu 2-5 dakika sürer.
Ekranda akan log satırlarını izleyebilirsiniz. En sonda:
```
==> Your service is live 🎉
```
görürseniz tamamdır. Sayfanın üstünde `https://merafraud-api.onrender.com`
gibi bir adres göreceksiniz — işte bu, artık herkesin erişebileceği canlı
API adresiniz.

### 2.4 Test edin
Tarayıcınızda şu adresi açın (kendi adresinizle değiştirin):
```
https://merafraud-api-XXXX.onrender.com/api/health
```
`{"status": "ok", ...}` gibi bir JSON görürseniz API canlıdır! 🎉

---

## BÖLÜM 3 — Dashboard'u Canlı API'ye Bağlama

Şu an dashboard hâlâ `http://localhost:5000`'e bakıyor. Onu canlı adresi
gösterecek şekilde güncelleyelim.

`dashboard/index.html` dosyasını bir metin düzenleyicide (Not Defteri
yeterli) açın, şu satırı bulun:
```js
const API_BASE = "http://localhost:5000/api";
```
ve `localhost:5000` yerine Render'ın verdiği adresi yazın:
```js
const API_BASE = "https://merafraud-api-XXXX.onrender.com/api";
```
Kaydedin. Artık `index.html`'i açtığınızda (veya bir arkadaşınıza
gönderdiğinizde) dashboard doğrudan internetteki canlı modele bağlanacak
— sizin bilgisayarınızın açık olmasına gerek kalmadan.

> İsterseniz bu `index.html`'i de Render'da (Static Site olarak) ya da
> Netlify/Vercel gibi ücretsiz bir statik barındırmada yayınlayarak ona
> da gerçek bir web adresi verebilirsiniz — isterseniz bir sonraki adımda
> bunu da yapabiliriz.

---

## Bilmeniz Gereken Ücretsiz Katman Sınırlamaları

1. **Uyku modu**: 15 dakika trafik gelmezse servis "uyur", bir sonraki
   istek geldiğinde ~60 saniye uyanma süresi olur. Demo için sorun değil;
   gerçek müşteriler için ücretli plana ($7/ay) geçmek gerekir.
2. **Kalıcı olmayan disk**: `data/tenants.json` dosyası her yeniden
   başlamada (redeploy, uyku sonrası uyanma) **sıfırlanabilir**. Yani
   `/api/tenants` ile oluşturduğunuz yeni müşteriler kaybolabilir. Gerçek
   üretimde bu dosya yerine bir veritabanı (Render'ın ücretsiz PostgreSQL'i)
   kullanmalısınız — istediğinizde bu geçişi birlikte yaparız.
3. **Demo tenant her zaman geri gelir**: `sk_demo_merafraud_dashboard`
   anahtarı kodun içinde otomatik oluştuğu için (`seed_demo_tenant`)
   yeniden başlasa bile dashboard çalışmaya devam eder.

---

## Özet — Neyi Nereden Kontrol Edeceksiniz

| Ne | Nerede |
|---|---|
| API canlı mı? | `https://[render-adresiniz]/api/health` |
| Kod değişikliği yaptım, nasıl güncellerim? | GitHub Desktop'ta commit + push yapın, Render otomatik yeniden deploy eder |
| Deploy loglarını nerede görürüm? | Render Dashboard → servisiniz → **Logs** sekmesi |
| Yeni müşteri (tenant) nasıl eklerim? | `POST /api/tenants` — bkz. README.md |
