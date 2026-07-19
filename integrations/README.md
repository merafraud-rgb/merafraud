# MeraFraud — Entegrasyon Seçenekleri

Müşterinize (mağaza sahibine) hangi yolu önereceğinizi burada bulabilirsiniz.

| Platformunuz | Önerilen Yol | Kod Gerekir mi? | Durum |
|---|---|---|---|
| **WooCommerce** (WordPress) | `woocommerce-plugin/` klasöründeki eklenti | ❌ Hayır, zip yükle + API anahtarı yapıştır | ✅ Hazır, kurulabilir |
| **Shopier ile WooCommerce** | Aynı WooCommerce eklentisi (Shopier bağımsız çalışır) | ❌ Hayır | ✅ Hazır |
| **iyzico / PayTR ile WooCommerce** | Aynı WooCommerce eklentisi | ❌ Hayır | ✅ Hazır |
| **Shopify** | `shopify-flow-guide.md` — Shopify Flow ile kodsuz kurulum | ❌ Hayır (ama sınırlı) | ✅ Rehber hazır |
| **Shopify (gelişmiş/tam otomatik)** | `../examples/shopify_webhook_integration.py` — kendi sunucunuz | ✅ Evet, bir geliştirici gerekir | ✅ Şablon hazır |
| **Özel yazılım / diğer platformlar** | `../examples/generic_checkout_integration.py` | ✅ Evet | ✅ Şablon hazır |

## Hangi Müşteriye Ne Söylersiniz?

- **"WordPress/WooCommerce kullanıyorum"** → `woocommerce-plugin/README.md`'yi
  gönderin, 5 dakikada kendisi kurar
- **"Shopify kullanıyorum, kod bilmiyorum"** → `shopify-flow-guide.md`'yi
  gönderin (Shopify Flow planına sahip olmaları gerekir)
- **"Shopify kullanıyorum, bir geliştiricim var / tam otomatik istiyorum"**
  → `../examples/shopify_webhook_integration.py`'yi geliştiricisine iletin
- **"Kendi yazılımım var"** → `../examples/generic_checkout_integration.py`'yi
  geliştiricisine iletin

## Gelecek Planı: Gerçek Shopify App Store Uygulaması

Şu an Shopify tarafında elimizde iki "geçici" çözüm var (Flow ve webhook
şablonu). Gerçek bir SaaS büyümesi için, App Store'da yayınlanmış resmi
bir **"MeraFraud" uygulaması** en profesyonel çözüm olur — mağaza sahibi
tek tıkla kurar, hiçbir ayar yazmaz. Bunun için gereken:

1. Bir Shopify Partner hesabı
2. Uygulamanın barındırılacağı bir sunucu (zaten Render'da olacak)
3. Shopify'ın OAuth/App onay sürecinden geçmek

Bu, ayrı bir geliştirme projesi — hazır olduğunuzda planlayabiliriz.
