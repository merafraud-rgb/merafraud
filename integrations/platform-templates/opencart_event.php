<?php
/**
 * MeraFraud Integration for OpenCart (3.x / 4.x)
 * -------------------------------------------------
 * Confidence: MEDIUM — OpenCart's event system changed somewhat between
 * 3.x and 4.x. This targets the `checkout/order.after_add` style events
 * common in 3.x/4.x hybrids. VERIFY the exact event name for your version
 * in admin: System > Maintenance > Event or your `catalog/model/checkout/order.php`.
 *
 * INSTALLATION (for a developer):
 *   1. Register a custom event listener (via an extension, OCMOD, or a
 *      direct event registration) that calls checkMeraFraud() below when
 *      an order is placed.
 *   2. Simplest path: install as an OpenCart extension (Extensions >
 *      Events) pointing to this file's function.
 */

class ModelExtensionEventMerafraud extends Model
{
    const API_BASE = 'https://your-merafraud-api.onrender.com/api'; // ⚠ replace with your real deploy URL
    const API_KEY = 'sk_live_REPLACE_ME';

    public function checkMeraFraud($order_id)
    {
        // Load order data the way OpenCart's own order model does
        $this->load->model('checkout/order');
        $order = $this->model_checkout_order->getOrder($order_id);
        if (!$order) {
            return;
        }

        $payload = [
            'transaction_amount' => (float) $order['total'],
            'amount_ratio_to_avg' => 1.2,  // TODO: compute from customer's order history
            'account_age_days' => 180,      // TODO: look up customer account creation date
            'customer_ltv' => 0,            // TODO: sum of customer's past orders
            'time_since_last_tx_min' => 999,
            'num_tx_last_24h' => 0,
            'hour_of_day' => (int) date('H'),
            'num_items_in_cart' => 1,       // TODO: count $order['products'] if available in your version
            'num_failed_payments_7d' => 0,
            'login_attempts_before_purchase' => 1,
            'billing_shipping_mismatch' => ($order['payment_address_1'] ?? '') !== ($order['shipping_address_1'] ?? '') ? 1 : 0,
            'ip_billing_country_mismatch' => 0,
            'new_device' => 0,
            'new_payment_method' => 0,
            'free_email_domain' => (int) in_array(
                strtolower(substr(strrchr($order['email'] ?? '', '@'), 1)),
                ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com']
            ),
            'express_shipping' => 0,
            'customer_id' => $order['email'] ?? ('guest-' . $order_id),
            'customer_ip' => $order['ip'] ?? '',
        ];

        $ch = curl_init(self::API_BASE . '/predict');
        curl_setopt($ch, CURLOPT_POST, true);
        curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($payload));
        curl_setopt($ch, CURLOPT_HTTPHEADER, [
            'Content-Type: application/json',
            'X-API-Key: ' . self::API_KEY,
        ]);
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_TIMEOUT, 5);
        $response = curl_exec($ch);
        curl_close($ch);

        $result = json_decode($response, true);
        if (!$result) {
            return; // fail open
        }

        // OpenCart order status IDs are store-specific — replace these
        // with your own "On Hold" / "Fraud Review" status IDs.
        if ($result['risk_level'] === 'block') {
            $this->model_checkout_order->addHistory($order_id, /* status_id */ 16, 'MeraFraud: HIGH RISK — ' . implode('; ', $result['reasons']));
        } elseif ($result['risk_level'] === 'review') {
            $this->model_checkout_order->addHistory($order_id, /* status_id */ 16, 'MeraFraud: flagged for review — ' . implode('; ', $result['reasons']));
        }
    }
}
