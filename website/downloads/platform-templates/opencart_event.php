<?php
/**
 * MeraFraud Integration for OpenCart (3.x / 4.x)
 * -------------------------------------------------
 * Confidence: MEDIUM — OpenCart's event system and the exact keys returned
 * by getOrder() changed somewhat between 3.x and 4.x. This targets the
 * `checkout/order.after_add` style events common in 3.x/4.x hybrids.
 * VERIFY the exact event name for your version in admin:
 * System > Maintenance > Event, or your `catalog/model/checkout/order.php`.
 *
 * Every `$order[...]` field below is read with isset()/?? so a missing key
 * on your version degrades to a safe default instead of a fatal error —
 * but you should still confirm the real keys against your own getOrder()
 * output (var_dump it once) rather than trust this blindly.
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

        // --- Item count: confirmed method is getOrderProducts(), not getProducts() ---
        $products = method_exists($this->model_checkout_order, 'getOrderProducts')
            ? $this->model_checkout_order->getOrderProducts($order_id)
            : [];
        $itemCount = is_array($products) && count($products) > 0 ? count($products) : 1;

        // --- Account age: OpenCart's customer model exposes date_added on registration ---
        // customer_id == 0 means a guest checkout — nothing to look up.
        $accountAgeDays = 0;
        if (!empty($order['customer_id'])) {
            $this->load->model('customer/customer');
            if (method_exists($this->model_customer_customer, 'getCustomer')) {
                $customerInfo = $this->model_customer_customer->getCustomer($order['customer_id']);
                if (!empty($customerInfo['date_added'])) {
                    $accountAgeDays = max(0, (int) floor((time() - strtotime($customerInfo['date_added'])) / 86400));
                }
            }
        }

        // --- Order history: LTV, average order size, recency, 24h velocity ---
        // No single documented "get customer's orders" method exists across
        // OpenCart versions for this — querying oc_order directly is the
        // honest approach rather than guessing a method name that may not
        // exist on your version.
        $customerLtv = 0.0;
        $amountRatioToAvg = 1.0;
        $timeSinceLastTxMin = 999999.0;
        $numTxLast24h = 0;
        if (!empty($order['customer_id'])) {
            $query = $this->db->query(
                "SELECT total, date_added FROM `" . DB_PREFIX . "order`
                 WHERE customer_id = '" . (int) $order['customer_id'] . "'
                 AND order_id != '" . (int) $order_id . "'
                 AND order_status_id > 0"
            );
            $nowTs = time();
            $pastAmounts = [];
            $mostRecentTs = null;
            foreach ($query->rows as $row) {
                $pastAmounts[] = (float) $row['total'];
                $createdTs = strtotime($row['date_added']);
                if ($createdTs !== false) {
                    if ($mostRecentTs === null || $createdTs > $mostRecentTs) {
                        $mostRecentTs = $createdTs;
                    }
                    if (($nowTs - $createdTs) <= 86400) {
                        $numTxLast24h++;
                    }
                }
            }
            if (count($pastAmounts) > 0) {
                $customerLtv = array_sum($pastAmounts);
                $avgAmount = $customerLtv / count($pastAmounts);
                $amountRatioToAvg = $avgAmount > 0 ? round(((float) $order['total']) / $avgAmount, 2) : 1.0;
            }
            if ($mostRecentTs !== null) {
                $timeSinceLastTxMin = max(0.0, ($nowTs - $mostRecentTs) / 60);
            }
        }

        $payload = [
            'transaction_amount' => (float) $order['total'],
            'amount_ratio_to_avg' => $amountRatioToAvg,
            'account_age_days' => $accountAgeDays,
            'customer_ltv' => round($customerLtv, 2),
            'time_since_last_tx_min' => round($timeSinceLastTxMin, 1),
            'num_tx_last_24h' => $numTxLast24h,
            'hour_of_day' => isset($order['date_added']) ? (int) date('H', strtotime($order['date_added'])) : (int) date('H'),
            'num_items_in_cart' => $itemCount,
            // OpenCart doesn't track failed-payment attempts or prior login
            // attempts on the order itself — these stay as safe defaults.
            'num_failed_payments_7d' => 0,
            'login_attempts_before_purchase' => 1,
            'billing_shipping_mismatch' => ($order['payment_address_1'] ?? '') !== ($order['shipping_address_1'] ?? '') ? 1 : 0,
            // OpenCart doesn't expose an IP-derived country — only the raw IP
            // itself (below). Sending customer_ip + billing_country lets
            // MeraFraud's own IP intelligence compute this mismatch server-side.
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
            'billing_country' => $order['payment_iso_code_2'] ?? null,
        ];
        $payload = array_filter($payload, static function ($v) {
            return $v !== null;
        });

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
