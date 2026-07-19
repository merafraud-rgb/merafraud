# MeraFraud — Shopify Entegrasyonu (Kodsuz, Shopify Flow ile)

Bu rehber, **Shopify App Store'da yayınlanmış resmi bir MeraFraud
uygulaması henüz yok** iken, bugün, kod yazmadan Shopify mağazanızı
MeraFraud'a bağlamanın yolunu anlatıyor.

⚠️ **Önkoşul:** Shopify Flow, **Shopify plan ve üzeri** paketlerde
dahildir (Starter/Basic planda olmayabilir). Mağaza sahibi planını
kontrol etmeli.

## Bu Yöntemin Sınırı

Shopify Flow'un "Send HTTP request" adımı, MeraFraud'dan bir cevap
alabilir ama karmaşık dallanma mantığı (örn. "risk_level bock ise siparişi
iptal et") için ek bir Flow adımı ya da basit bir koşul kurmanız gerekir.
Yani bu yöntem **"sipariş oluşunca MeraFraud'a bildir ve siparişe etiket
ekle"** için mükemmel çalışır; **"checkout'ta müşteriyi tamamen
durdurmak"** için tam otomatik değildir (Shopify'da checkout'u gerçek
zamanlı durdurmak, Shopify Plus'a özel "Checkout UI Extensions" gerektirir).

## Adımlar

### 1. Shopify Flow'u açın
Shopify admin panelinde **Apps → Shopify Flow**'a gidin (yoksa App
Store'dan ücretsiz kurun).

### 2. Yeni bir workflow oluşturun
**Create workflow → Trigger** olarak **"Order created"**'ı seçin.

### 3. HTTP isteği adımı ekleyin
**Add action → Send HTTP request** seçin ve şunları girin:

| Alan | Değer |
|---|---|
| URL | `https://api.merafraud.com/api/predict` (kendi deploy adresiniz) |
| Method | POST |
| Headers | `Content-Type: application/json`, `X-API-Key: [mağazanın MeraFraud anahtarı]` |
| Body (JSON) | Sipariş alanlarını MeraFraud formatına eşleyin — örnek aşağıda |

**Örnek Body:**
```json
{
  "transaction_amount": {{order.total_price}},
  "amount_ratio_to_avg": 1.2,
  "account_age_days": 180,
  "customer_ltv": {{order.customer.total_spent}},
  "time_since_last_tx_min": 120,
  "num_tx_last_24h": {{order.customer.orders_count}},
  "hour_of_day": 12,
  "num_items_in_cart": {{order.line_items.size}},
  "num_failed_payments_7d": 0,
  "login_attempts_before_purchase": 1,
  "billing_shipping_mismatch": 0,
  "ip_billing_country_mismatch": 0,
  "new_device": 0,
  "new_payment_method": 0,
  "free_email_domain": 0,
  "express_shipping": 0,
  "customer_id": "{{order.customer.email}}"
}
```
> Not: Bazı alanlar (hesap yaşı, cihaz bilgisi gibi) Shopify Flow
> değişkenlerinde doğrudan yok — bu alanları sabit/varsayılan değerlerle
> bırakabilir ya da daha gelişmiş bir kurulumda `examples/shopify_webhook_integration.py`
> gibi kendi küçük bir sunucunuzdan zenginleştirebilirsiniz.

### 4. Cevaba göre etiket ekleyin
**Add action → Add order tag** ile, HTTP isteğinin cevabındaki
`risk_level` değerine göre siparişe `merafraud-block`,
`merafraud-review` ya da `merafraud-approve` etiketi ekleyin. Shopify
Flow'un koşullu mantığı (**Condition** adımı) ile bunu otomatikleştirin.

### 5. Etiketlenen siparişleri filtreleyin
Shopify admin'de **Orders** sayfasında `tag:merafraud-block` ile arama
yaparak riskli siparişleri tek bakışta görebilirsiniz.

## Daha Sağlam Bir Kurulum İster misiniz?

Yukarıdaki yöntem hızlı başlangıç için iyi ama sınırlı. Gerçek bir
production kurulumu için önerilen yol, `examples/shopify_webhook_integration.py`
dosyasındaki gibi **kendi küçük sunucunuzu** (Render'da barındırılabilir)
Shopify webhook'larını dinleyecek şekilde kurmaktır — bu, tam otomatik
engelleme, sipariş iptali, ve daha zengin veri gönderimi sağlar.

Uzun vadede, gerçek bir **Shopify App Store uygulaması** (Shopify Partner
hesabı + OAuth + inceleme süreci gerektirir) en profesyonel çözüm olur —
bu ayrı bir geliştirme projesi, istediğinizde bunu da planlayabiliriz.
