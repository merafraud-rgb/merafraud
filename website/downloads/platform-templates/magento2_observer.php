<?php
/**
 * MeraFraud Integration for Magento 2
 * -------------------------------------------
 * Confidence: HIGH — Magento 2's observer/event system is stable and has
 * been consistent across versions for years.
 *
 * This uses the `sales_order_place_after` event, which fires right after
 * an order is placed (before/around payment capture depending on your
 * payment method flow).
 *
 * INSTALLATION (for a developer):
 *   1. Create a module: app/code/MeraFraud/FraudCheck/
 *   2. Place this observer at:
 *      app/code/MeraFraud/FraudCheck/Observer/CheckOrderObserver.php
 *   3. Register the observer in:
 *      app/code/MeraFraud/FraudCheck/etc/events.xml
 *      (see snippet at the bottom of this file)
 *   4. Run: bin/magento module:enable MeraFraud_FraudCheck
 *           bin/magento setup:upgrade
 *
 * Set your API key via Magento's Store Config (recommended) or as a
 * constant below for a quick test.
 */

namespace MeraFraud\FraudCheck\Observer;

use Magento\Framework\Event\Observer;
use Magento\Framework\Event\ObserverInterface;
use Magento\Framework\HTTP\Client\Curl;
use Psr\Log\LoggerInterface;

class CheckOrderObserver implements ObserverInterface
{
    private const MERAFRAUD_API_BASE = 'https://your-merafraud-api.onrender.com/api'; // ⚠ replace with your real deploy URL
    private const MERAFRAUD_API_KEY  = 'sk_live_REPLACE_ME'; // ⚠ better: pull from Magento's encrypted config

    private Curl $curl;
    private LoggerInterface $logger;

    public function __construct(Curl $curl, LoggerInterface $logger)
    {
        $this->curl = $curl;
        $this->logger = $logger;
    }

    public function execute(Observer $observer)
    {
        $order = $observer->getEvent()->getOrder();
        if (!$order) {
            return;
        }

        $customer = $order->getCustomerEmail();
        $billingAddress = $order->getBillingAddress();
        $shippingAddress = $order->getShippingAddress();

        $payload = [
            'transaction_amount' => (float) $order->getGrandTotal(),
            'amount_ratio_to_avg' => 1.2, // TODO: look up this customer's historical average order value
            'account_age_days' => 180,     // TODO: look up via $order->getCustomerId() -> customer creation date
            'customer_ltv' => 0,           // TODO: look up customer's lifetime spend
            'time_since_last_tx_min' => 999,
            'num_tx_last_24h' => 0,        // TODO: query recent orders for this customer
            'hour_of_day' => (int) date('H'),
            'num_items_in_cart' => (int) $order->getTotalItemCount(),
            'num_failed_payments_7d' => 0,
            'login_attempts_before_purchase' => 1,
            'billing_shipping_mismatch' => $billingAddress && $shippingAddress
                && $billingAddress->getStreet(1) !== $shippingAddress->getStreet(1) ? 1 : 0,
            'ip_billing_country_mismatch' => 0, // MeraFraud can compute this itself if you also send customer_ip
            'new_device' => 0,
            'new_payment_method' => 0,
            'free_email_domain' => (int) in_array(
                strtolower(substr(strrchr($customer, '@'), 1)),
                ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com']
            ),
            'express_shipping' => 0,
            'customer_id' => $customer,
            'customer_ip' => $order->getRemoteIp(),
        ];

        try {
            $this->curl->addHeader('Content-Type', 'application/json');
            $this->curl->addHeader('X-API-Key', self::MERAFRAUD_API_KEY);
            $this->curl->post(self::MERAFRAUD_API_BASE . '/predict', json_encode($payload));

            $result = json_decode($this->curl->getBody(), true);

            if (isset($result['risk_level']) && $result['risk_level'] === 'block') {
                $order->addCommentToStatusHistory(
                    'MeraFraud flagged this order as HIGH RISK: ' . implode('; ', $result['reasons'] ?? [])
                );
                $order->setStatus('fraud'); // requires a custom "fraud" order status, or use "on_hold"
                $order->save();
            } elseif (isset($result['risk_level']) && $result['risk_level'] === 'review') {
                $order->addCommentToStatusHistory(
                    'MeraFraud: flagged for manual review. Reasons: ' . implode('; ', $result['reasons'] ?? [])
                );
                $order->save();
            }
        } catch (\Exception $e) {
            $this->logger->error('MeraFraud check failed: ' . $e->getMessage());
            // Fail open — never block a sale because of a MeraFraud connectivity issue
        }
    }
}

/*
--- app/code/MeraFraud/FraudCheck/etc/events.xml ---

<?xml version="1.0"?>
<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="urn:magento:framework:Event/etc/events.xsd">
    <event name="sales_order_place_after">
        <observer name="merafraud_check_order" instance="MeraFraud\FraudCheck\Observer\CheckOrderObserver" />
    </event>
</config>
*/
