# MeraFraud — WooCommerce Eklentisi Kurulumu

Bu eklenti WordPress + WooCommerce kullanan **her mağazada** çalışır —
**Shopier, iyzico, PayTR, Stripe, banka havalesi, kapıda ödeme** dahil,
hangi ödeme yöntemini kullanıyorsanız kullanın. Çünkü kontrol, ödeme
sağlayıcısından bağımsız olarak WooCommerce'in sipariş oluşturma
aşamasında yapılıyor.

## Kimin İçin Bu Rehber?

Bu, **mağaza sahibi müşterinize** göndereceğiniz kurulum talimatı. Kod
bilmesine gerek yok, WordPress paneline erişimi olması yeterli.

## Kurulum Adımları

### 1. Eklenti dosyasını zip'leyin
`merafraud-woocommerce` klasörünü (bu README'nin yanındaki) zip'leyin —
`merafraud-woocommerce.zip` olsun.

### 2. WordPress'e yükleyin
1. WordPress admin paneline girin
2. **Eklentiler → Yeni Ekle → Eklenti Yükle**
3. Az önce oluşturduğunuz zip dosyasını seçin, **Şimdi Kur**'a basın
4. **Etkinleştir**'e basın

### 3. API anahtarınızı girin
1. **WooCommerce → Ayarlar** sekmesine gidin
2. Üstte yeni bir **"MeraFraud"** sekmesi göreceksiniz, ona tıklayın
3. MeraFraud kayıt sayfasından aldığınız API anahtarını yapıştırın
4. **Değişiklikleri Kaydet**

**Bu kadar.** Artık her yeni sipariş otomatik olarak MeraFraud'a
gönderilip puanlanacak.

## Ne Değişecek?

- **Sipariş listesinde** yeni bir "Fraud Risk" sütunu göreceksiniz —
  yeşil/sarı/pembe rozet ile her siparişin risk seviyesi
- **Yüksek riskli siparişler** otomatik olarak "Beklemede" (on-hold)
  durumuna alınır, sebebi sipariş notlarında yazar
- **İncelemeye alınan siparişler** normal işlenmeye devam eder ama
  sipariş notuna bir uyarı eklenir, siz karar verirsiniz
- **İptal edilen siparişler** otomatik olarak MeraFraud'a bildirilir —
  böylece "seri iptal eden" müşteriler zamanla tespit edilir

## Ayarlar

| Ayar | Ne işe yarar |
|---|---|
| API Key | MeraFraud hesabınızın anahtarı |
| API Base URL | Varsayılan bırakın (MeraFraud size farklı bir adres vermediyse) |
| Enabled | Kapatırsanız eklenti hiçbir şey yapmaz ama kurulu kalır |
| Block high-risk orders | Kapatırsanız hiçbir sipariş otomatik "beklemede"ye alınmaz, sadece not düşülür |

## Sert Engelleme (Checkout'ta Durdurmak) İster misiniz?

Şu anki davranış: yüksek riskli sipariş **oluşturulur** ama "beklemede"
durumuna alınır — yani ödeme alınmış/alınmamış olabilir, siz manuel karar
verirsiniz. Eğer siparişin **checkout ekranında tamamen engellenmesini**
(müşteri "Siparişi Tamamla"ya bile basamasın) istiyorsanız, bu farklı bir
WooCommerce kancasına (`woocommerce_after_checkout_validation`) taşınmalı
— isterseniz bunu bir sonraki adımda ekleyebiliriz.

## Shopier / iyzico / PayTR Kullanıyorsanız

Hiçbir ek ayar gerekmiyor — bu eklenti onlardan bağımsız çalışır. Ödeme
sağlayıcınız ne olursa olsun, WooCommerce sipariş oluşturduğu an MeraFraud
devreye giriyor.
