# MeraFraud — Platform Entegrasyon Şablonları

Bu klasördeki her dosya, ilgili platformun **belgelenmiş (dokümante edilmiş)
genişletme mekanizmasına** göre yazılmış bir başlangıç şablonudur.

⚠️ **Önemli dürüstlük notu:** Bu dosyaların hiçbiri gerçek bir canlı hesapta
test edilmedi (o platformlarda gerçek bir mağazam/hesabım yok). Aşağıdaki
"Güven Seviyesi" sütunu, her platform için ne kadar emin olduğumu gösteriyor
— bu bilinçli bir şeffaflık, körlemesine "hepsi çalışır" demek yerine.

## Güven Seviyesi Tablosu

| Platform | Dosya | Güven Seviyesi | Neden |
|---|---|---|---|
| **Magento 2** | `magento2_observer.php` | 🟢 Yüksek | Observer/event sistemi çok stabil ve iyi belgelenmiş, yıllardır değişmiyor |
| **PrestaShop** | `prestashop_module.php` | 🟢 Yüksek | Hook sistemi (`actionOrderStatusPostUpdate`) standart ve iyi belgelenmiş |
| **OpenCart** | `opencart_event.php` | 🟡 Orta | Event sistemi sürüm 3.x ve 4.x arasında değişebiliyor, sürümünüzü kontrol edin |
| **BigCommerce** | `bigcommerce_webhook.py` | 🟢 Yüksek | Webhook API'si REST tabanlı, iyi belgelenmiş, küresel standart |
| **WHMCS** | `whmcs_hook.php` | 🟡 Orta | Hook sistemi var ama sürüme göre bazı fonksiyon imzaları değişebilir |
| **Ticimax, İkas, T-Soft, PlatinMarket** | `generic_webhook_receiver.py` | 🔴 Düşük (genel şablon) | Bu platformların kesin API/webhook alan adlarına dair güncel, doğrulanmış bilgim yok — genel bir webhook alıcısı sağlıyorum, **alan adlarını platformun kendi dokümantasyonuyla eşleştirmeniz gerekir** |
| **Ecwid, Shopware, Sylius, AbanteCart, ThirtyBees, CS-Cart** | `generic_webhook_receiver.py` | 🔴 Düşük (genel şablon) | Aynı şekilde — hepsi genellikle webhook/REST API sunuyor ama alan eşleştirmesi platforma özel araştırma gerektiriyor |

## Nasıl Kullanılır

Yüksek/orta güvenli dosyalar (Magento, PrestaShop, OpenCart, BigCommerce,
WHMCS) doğrudan bir geliştiriciye verilebilir, küçük düzeltmelerle çalışması
beklenir.

Düşük güvenli platformlar için `generic_webhook_receiver.py`, **herhangi
bir platformun webhook'unu dinleyebilen** esnek bir Flask sunucusu —
geliştiriciniz, o platformun gönderdiği JSON'daki alan isimlerini
(örn. `order.total` mi yoksa `siparis.tutar` mı) `map_platform_fields()`
fonksiyonunda güncellemesi yeterli.

## Ortak Kural

Hepsi aynı temel mantığı izliyor:
1. Platformdan bir "sipariş oluşturuldu" bildirimi/webhook'u al
2. Sipariş verisini MeraFraud'un beklediği alanlara eşle
3. `POST /api/predict` çağır
4. Sonuca göre: etiketle / beklet / işleme devam et
