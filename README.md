# MeraFraud — E-Ticaret KOBİ'leri için Yapay Zeka Destekli Dolandırıcılık Tespiti

Pitch deck'teki vizyonun çalışan bir MVP'ye dönüştürülmüş hali: sentetik ama
gerçekçi işlem verisiyle eğitilmiş bir ML modeli, bunu servis eden bir REST API
ve marka kimliğine uygun bir demo dashboard.

## Proje Yapısı

```
merafraud/
├── data/
│   ├── generate_data.py      # Sentetik işlem verisi üretici
│   └── transactions.csv      # Üretilen veri seti (60.000 satır, ~%3.7 fraud oranı)
├── model/
│   ├── train_model.py        # Model eğitim scripti
│   ├── merafraud_model.pkl   # Eğitilmiş RandomForest modeli
│   └── model_meta.json       # Metrikler, özellik önem dereceleri
├── api/
│   └── app.py                # Flask REST API (modeli servis eder)
├── dashboard/
│   ├── index.html            # Marka kimliğine uygun demo dashboard (harita dahil)
│   └── signup.html           # Self-servis "hesap oluştur / API anahtarı al" sayfası
├── website/
│   ├── styles.css            # Tüm pazarlama sayfalarının ortak stili
│   ├── index.html            # Ana sayfa (homepage)
│   ├── about.html            # Hakkımızda
│   ├── pricing.html          # Fiyatlandırma
│   ├── contact.html          # İletişim
│   ├── privacy.html          # Gizlilik Politikası (taslak — hukuki inceleme gerekir)
│   └── terms.html            # Kullanım Şartları (taslak — hukuki inceleme gerekir)
├── examples/
│   ├── generic_checkout_integration.py   # Herhangi bir backend'den nasıl çağrılır (çalıştırılabilir demo)
│   └── shopify_webhook_integration.py    # Shopify orders webhook'undan MeraFraud'u çağırma şablonu
├── integrations/
│   ├── README.md                         # Hangi platforma hangi entegrasyon yolu?
│   ├── shopify-flow-guide.md             # Shopify için kodsuz kurulum rehberi
│   └── woocommerce-plugin/               # Gerçek, kurulabilir WordPress eklentisi
│       ├── merafraud-woocommerce.php
│       └── README.md
└── requirements.txt
```

## Neden Bu Şekilde Kuruldu?

Elinizde gerçek bir eğitilmiş model veya gerçek müşteri verisi olmadığı için,
tipik e-ticaret dolandırıcılık sinyallerini (ödeme davranışı + kullanıcı
davranışı) simüle eden **sentetik** ama istatistiksel olarak gerçekçi bir veri
seti oluşturdum ve üzerinde bir RandomForest sınıflandırıcı eğittim.

**Bu bir demo/prototip modelidir.** Gerçek pilot müşterilerden veri
topladıkça, `train_model.py`'yi gerçek (anonimleştirilmiş, onaylı) işlem
verisiyle yeniden çalıştırıp modeli güncelleyebilirsiniz — kod bunun için hazır.

**Mevcut model performansı** (test seti üzerinde):
- ROC-AUC: 0.87
- Dolandırıcılık precision: %98 (yanlış alarm çok düşük)
- Dolandırıcılık recall: %74 (gerçek fraud'ların %74'ünü yakalıyor)

## Kurulum ve Çalıştırma

### 1. Bağımlılıkları kurun
```bash
pip install -r requirements.txt
```

### 2. (Opsiyonel) Veriyi ve modeli yeniden üretin
Zaten eğitilmiş model dosyası (`model/merafraud_model.pkl`) dahildir, bu adımı
atlayabilirsiniz. Yeniden üretmek isterseniz:
```bash
python data/generate_data.py
python model/train_model.py
```

### 3. API'yi başlatın
```bash
python api/app.py
```
API `http://localhost:5000` üzerinde çalışmaya başlar.

### 4. Dashboard'u açın
`dashboard/index.html` dosyasını tarayıcınızda açın (çift tıklamanız yeterli).
API çalışıyorsa dashboard otomatik bağlanır ve gerçek model skorlarını
gösterir. API kapalıysa dashboard yerel bir JS simülasyonuna geçer, böylece
her zaman gösterilebilir durumda kalır.

## API Uç Noktaları

Bu API **multi-tenant**'tır: her müşteri (tenant) kendi API anahtarına ve
kendi risk eşiklerine sahiptir. `/api/predict*` ve `/api/stats` çağrıları
`X-API-Key` header'ı ister.

| Method | Endpoint | Açıklama | Auth gerekli mi? |
|---|---|---|---|
| GET | `/api/health` | Servis durumu, model bilgisi | Hayır |
| POST | `/api/predict` | Tek işlem için risk skoru | Evet |
| POST | `/api/predict/batch` | Toplu işlem skorlama | Evet |
| GET | `/api/feature-importance` | Modelin genel özellik önem dereceleri | Hayır |
| GET | `/api/stats` | Bu tenant'ın gerçek kullanım istatistikleri | Evet |
| GET | `/api/geo-activity` | Dashboard'daki dünya haritası için coğrafi işlem verisi (demo/sentetik) | Evet |
| POST | `/api/orders/outcome` | Bir siparişin durumunu bildir (placed/cancelled/fulfilled) — seri iptal takibi için | Evet |
| GET | `/api/customers/<customer_id>/history` | Bir müşterinin sipariş/iptal geçmişini görüntüle | Evet |
| GET | `/api/tenants/me` | Giriş yapılan hesabın kendi bilgilerini getir (Settings sayfası kullanır) | Evet |
| POST | `/api/auth/login` | E-posta + parola ile giriş (kaybolan API anahtarını da geri getirir) | Hayır |
| POST | `/api/auth/forgot-password` | Şifre sıfırlama token'ı iste (demo: token doğrudan yanıtta döner) | Hayır |
| POST | `/api/auth/reset-password` | Token ile yeni parola belirle | Hayır |
| POST | `/api/tenants/regenerate-key` | API anahtarını yenile (eskisi anında geçersiz olur) | Evet |
| GET | `/api/reports/transactions.csv` | İşlem geçmişini CSV olarak indir | Evet |
| POST | `/api/tenants` | Yeni müşteri (tenant) oluştur, API anahtarı üretir | Hayır* |
| GET | `/api/tenants` | Tüm tenant'ları listele (anahtarlar maskeli) | Hayır* |
| PUT | `/api/tenants/thresholds` | Kendi risk eşiklerini güncelle | Evet |

*Gerçek üretimde `/api/tenants` bir admin girişi arkasına alınmalıdır —
şu an demo/CLI kullanımı için açık.

**Demo API anahtarı** (dashboard'un kullandığı): `sk_demo_merafraud_dashboard`

### Yeni bir müşteri (KOBİ) ekleme
```bash
curl -X POST http://localhost:5000/api/tenants \
  -H "Content-Type: application/json" \
  -d '{"name": "Anadolu Butik E-Ticaret", "thresholds": {"block": 0.6, "review": 0.25}}'
```
Yanıtta dönen `api_key` alanını saklayın — bu müşteri artık kendi
anahtarıyla `/api/predict` çağırabilir.

### Örnek istek — `/api/predict`
```bash
curl -X POST http://localhost:5000/api/predict \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk_demo_merafraud_dashboard" \
  -d '{
    "account_age_days": 2,
    "customer_ltv": 5,
    "transaction_amount": 890,
    "amount_ratio_to_avg": 4.2,
    "hour_of_day": 3,
    "num_tx_last_24h": 6,
    "num_failed_payments_7d": 3,
    "login_attempts_before_purchase": 5,
    "time_since_last_tx_min": 4,
    "billing_shipping_mismatch": 1,
    "ip_billing_country_mismatch": 1,
    "new_device": 1,
    "new_payment_method": 1,
    "free_email_domain": 1,
    "num_items_in_cart": 1,
    "express_shipping": 1
  }'
```

Yanıt:
```json
{
  "risk_score": 0.9989,
  "risk_level": "block",
  "reasons": [
    "İşlemler arası süre çok kısa",
    "Düşük müşteri yaşam boyu değeri",
    "Tutar, müşterinin ortalamasının çok üzerinde"
  ],
  "thresholds": {"block": 0.75, "review": 0.35}
}
```

## Bir E-Ticaret Sitesine Nasıl Entegre Edilir?

Gerçek dünyada bir mağaza, ödeme onayından hemen önce checkout akışında bu
API'ye bir çağrı yapar:

1. Müşteri "Siparişi Tamamla"ya basar
2. Mağaza backend'i işlem verisini `/api/predict`'e gönderir
3. Yanıta göre: `approve` → sipariş işlenir, `review` → manuel inceleme
   kuyruğuna alınır, `block` → işlem reddedilir / ek doğrulama istenir

## ✅ Zaten Eklenmiş Olanlar

- **Multi-tenant mimari** — her KOBİ kendi API anahtarına, kendi risk
  eşiklerine (`thresholds`) ve kendi kullanım sayaçlarına sahip
  (`api/tenants.py`)
- **Üretime hazır container** — `Dockerfile` + `docker-compose.yml` ile
  Gunicorn üzerinden çalıştırılabilir
- **Deploy rehberi** — `DEPLOY.md` dosyasında Render.com üzerinden
  sıfırdan, tıklama tıklama internete yayınlama adımları

## Seri Sipariş/İptal Takibi (Order Abuse Detection)

Ödeme dolandırıcılığından ayrı bir problem: gerçek, doğrulanmış bir müşteri
sürekli sipariş verip iptal ederek kargo/işlem maliyetine yol açabilir. Bunu
takip etmek için:

1. Mağazanız her sipariş durumu değiştiğinde `POST /api/orders/outcome`'u
   çağırır (`placed`, `cancelled`, `fulfilled`)
2. `/api/predict` çağrısına `customer_id` (e-posta ya da kendi müşteri
   ID'niz) eklerseniz, MeraFraud o müşterinin geçmiş iptal oranını kontrol
   eder
3. Müşteri son siparişlerinin **%40'ından fazlasını** iptal etmişse (en az
   3 sipariş şartıyla) risk skoru otomatik olarak yükseltilir ve gerekçede
   açıkça belirtilir

Bu, ML modelinin öğrendiği bir şey değil — üzerine eklenmiş, şeffaf ve
`api/customer_history.py` içinde ayarlanabilir bir iş kuralıdır (eşikler
`SERIAL_CANCELLER_*` sabitleriyle değiştirilebilir).

## 🚀 Canlıya Geçiş Kontrol Listesi (TEK YERDEN)

Artık her ayarlanabilir şey **tek bir dosyada**: `.env`. Önce kopyalayın:
```bash
cp .env.example .env
```
Sonra `.env` içini doldurun. Hiçbir şey doldurmazsanız sistem **demo modda**
çalışmaya devam eder (gerçek e-posta/ödeme gönderilmez, ama hiçbir şey
çökmez) — yani istediğiniz an, istediğiniz parçayı aktif edebilirsiniz.

| Ne | Nerede alınır | Doldurulmazsa ne olur |
|---|---|---|
| **SMTP bilgileri** (e-posta) | Gmail "Uygulama Şifresi" / SendGrid / Postmark | Şifre sıfırlama token'ı ekranda gösterilir (e-posta gitmez) |
| **WHATSAPP_NUMBER** | Kendi WhatsApp Business numaranız | Destek widget'ı placeholder numarayı kullanır |
| **IYZICO_API_KEY / SECRET** | merchant.iyzipay.com → Ayarlar → API | Checkout sayfası demo modda kalır, gerçek ödeme almaz |
| **Render deploy adresi** | Render'a deploy ettikten sonra | Sistem `localhost:5000`'de kalır, sadece sizin bilgisayarınızda çalışır |

**Deploy (Render) hâlâ tek manuel adım** — bunu ben sizin yerinize
yapamam çünkü kendi GitHub/Render hesabınızı gerektiriyor. `DEPLOY.md`'yi
takip edin, **bir kere** yapılır, sonrasında güncelleme `git push` ile olur
— bir daha zip indirip yeniden kurmanıza gerek kalmaz.

## SaaS'a Dönüştürmek İçin Sıradaki Adımlar

✅ Tamamlanan: parola/giriş/anahtar kurtarma, e-posta gönderim altyapısı
(SMTP), iyzico ödeme entegrasyon iskeleti, CSV rapor, WooCommerce eklentisi,
Shopify Flow rehberi, seri iptal takibi.

Kalanlar:
1. **Kalıcı veritabanı** — şu an tenant verisi `data/tenants.json`'da; gerçek
   üretimde bunun yerine PostgreSQL kullanılmalı (dosya tabanlı depolama,
   çoklu sunucu/redeploy senaryolarında veri kaybına açık)
2. **Admin kimlik doğrulaması** — `/api/tenants` (liste/oluştur) uç noktaları
   şu an açık; gerçek üründe bir admin girişi arkasına alınmalı
3. **Model yeniden eğitimi hattı** — pilot müşterilerden gelen etiketli
   verilerle periyodik yeniden eğitim
4. **iyzico canlı testi** — `api/payments.py` yazıldı ama gerçek sandbox
   anahtarlarıyla hiç test edilmedi; gerçek ödeme almadan önce mutlaka test
   edin
5. **Gerçek Shopify App Store uygulaması** — şu an Flow (kodsuz) ve webhook
   şablonu var; tam otomatik "tek tıkla kur" için ayrı bir geliştirme projesi
   gerekiyor

## Önemli Not

Bu proje bir **prototip/demo**'dur; sentetik veriyle eğitilmiştir. Gerçek
parayı etkileyen kararlar (işlem engelleme vb.) için üretime almadan önce
gerçek veri üzerinde doğrulama ve hukuki/uyumluluk (KVKK/GDPR, PCI-DSS)
incelemesi yapılmalıdır.
