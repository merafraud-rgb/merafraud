<?php
/**
 * MeraFraud Integration for PrestaShop
 * -------------------------------------------
 * Confidence: HIGH — PrestaShop's hook system and core Object Model
 * (Order, Customer, Address, Country) are standard and stable.
 * This uses `actionOrderStatusPostUpdate`, which fires whenever an
 * order's status changes (including right after it's placed).
 *
 * INSTALLATION (for a developer):
 *   1. Create a module folder: modules/merafraud/
 *   2. Save this file as: modules/merafraud/merafraud.php
 *   3. Install via PrestaShop admin: Modules > Upload a module
 *
 * This is a minimal, single-file module — a production version would
 * add a proper config page for the API key instead of the constant below.
 */

if (!defined('_PS_VERSION_')) {
    exit;
}

class MeraFraud extends Module
{
    const API_BASE = 'https://your-merafraud-api.onrender.com/api'; // ⚠ replace with your real deploy URL
    const API_KEY = 'sk_live_REPLACE_ME'; // ⚠ better: store via Configuration::updateValue() in a settings page

    public function __construct()
    {
        $this->name = 'merafraud';
        $this->tab = 'administration';
        $this->version = '1.0.0';
        $this->author = 'MeraFraud';
        parent::__construct();
        $this->displayName = 'MeraFraud Fraud Protection';
        $this->description = 'Scores every order against MeraFraud\'s fraud detection API.';
    }

    public function install()
    {
        return parent::install()
            && $this->registerHook('actionOrderStatusPostUpdate')
            && $this->registerHook('actionValidateOrder');
    }

    /**
     * Fires exactly once, when the order is first created — the correct
     * place to report outcome "placed" to MeraFraud's customer history
     * (POST /api/orders/outcome). Unlike actionOrderStatusPostUpdate below
     * (which fires on every later status change too), this can't
     * accidentally double-count an order as "placed" multiple times.
     */
    public function hookActionValidateOrder($params)
    {
        /** @var Order $order */
        $order = $params['order'] ?? null;
        if (!$order) {
            return;
        }
        $customer = new Customer($order->id_customer);
        $invoiceAddress = new Address($order->id_address_invoice);
        $this->reportOutcome(
            $customer->email,
            'placed',
            $order->id,
            trim($customer->firstname . ' ' . $customer->lastname) ?: null,
            Country::getIsoById((int) $invoiceAddress->id_country) ?: null,
            $invoiceAddress->city ?: null
        );
    }

    /**
     * POSTs to /api/orders/outcome so MeraFraud actually learns whether an
     * order was cancelled/refunded or delivered — without this, the
     * "serial canceller" detection in customer_history.py has nothing to
     * work with beyond "placed". Fails silently: a history-reporting hiccup
     * shouldn't disrupt the store's order status update.
     */
    private function reportOutcome($customerId, $outcome, $orderId, $customerName = null, $billingCountry = null, $billingCity = null)
    {
        $body = array_filter([
            'customer_id' => $customerId,
            'outcome' => $outcome,
            'order_id' => (string) $orderId,
            'customer_name' => $customerName,
            'billing_country' => $billingCountry,
            'billing_city' => $billingCity,
        ], static function ($v) { return $v !== null; });

        $ch = curl_init(self::API_BASE . '/orders/outcome');
        curl_setopt($ch, CURLOPT_POST, true);
        curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($body));
        curl_setopt($ch, CURLOPT_HTTPHEADER, ['Content-Type: application/json', 'X-API-Key: ' . self::API_KEY]);
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_TIMEOUT, 5);
        curl_exec($ch);
        curl_close($ch);
    }

    public function hookActionOrderStatusPostUpdate($params)
    {
        /** @var Order $order */
        $order = $params['order'] ?? null;
        if (!$order) {
            return;
        }

        $customer = new Customer($order->id_customer);
        $invoiceAddress = new Address($order->id_address_invoice);
        $deliveryAddress = $order->id_address_delivery ? new Address($order->id_address_delivery) : null;

        // --- Order history: LTV, average order size, recency, 24h velocity ---
        // Order::getCustomerOrders() returns every order this customer has
        // placed, each row carrying 'total_paid' and 'date_add'.
        $pastOrders = Order::getCustomerOrders((int) $customer->id);
        $pastAmounts = [];
        $mostRecentTs = null;
        $numTxLast24h = 0;
        $nowTs = time();
        foreach ($pastOrders as $pastOrder) {
            if ((int) $pastOrder['id_order'] === (int) $order->id) {
                continue; // exclude the order that triggered this hook
            }
            $pastAmounts[] = (float) $pastOrder['total_paid'];
            $createdTs = strtotime($pastOrder['date_add']);
            if ($createdTs !== false) {
                if ($mostRecentTs === null || $createdTs > $mostRecentTs) {
                    $mostRecentTs = $createdTs;
                }
                if (($nowTs - $createdTs) <= 86400) {
                    $numTxLast24h++;
                }
            }
        }
        $customerLtv = array_sum($pastAmounts);
        $amountRatioToAvg = 1.0;
        if (count($pastAmounts) > 0) {
            $avgAmount = $customerLtv / count($pastAmounts);
            $amountRatioToAvg = $avgAmount > 0 ? round(((float) $order->total_paid) / $avgAmount, 2) : 1.0;
        }
        $timeSinceLastTxMin = $mostRecentTs !== null ? max(0.0, ($nowTs - $mostRecentTs) / 60) : 999999.0;

        // --- Billing vs. shipping mismatch (compare country + postcode, not just the address ID) ---
        $billingShippingMismatch = 0;
        if ($deliveryAddress && $invoiceAddress->id !== $deliveryAddress->id) {
            $billingShippingMismatch = (
                $invoiceAddress->id_country !== $deliveryAddress->id_country
                || $invoiceAddress->postcode !== $deliveryAddress->postcode
            ) ? 1 : 0;
        }

        $products = $order->getProducts();
        $itemCount = is_array($products) ? count($products) : 1;

        $payload = [
            'transaction_amount' => (float) $order->total_paid,
            'amount_ratio_to_avg' => $amountRatioToAvg,
            'account_age_days' => (int) max(0, floor((time() - strtotime($customer->date_add)) / 86400)),
            'customer_ltv' => round($customerLtv, 2),
            'time_since_last_tx_min' => round($timeSinceLastTxMin, 1),
            'num_tx_last_24h' => $numTxLast24h,
            'hour_of_day' => (int) date('H'),
            'num_items_in_cart' => $itemCount,
            // PrestaShop doesn't track failed-payment attempts or prior login
            // attempts on the order itself — these stay as safe defaults.
            'num_failed_payments_7d' => 0,
            'login_attempts_before_purchase' => 1,
            'billing_shipping_mismatch' => $billingShippingMismatch,
            // PrestaShop's Order object doesn't expose an IP-derived country —
            // only the raw IP itself (below). Sending customer_ip + billing_country
            // lets MeraFraud's own IP intelligence compute this mismatch server-side.
            'ip_billing_country_mismatch' => 0,
            'new_device' => 0,
            'new_payment_method' => 0,
            'free_email_domain' => (int) in_array(
                strtolower(substr(strrchr($customer->email, '@'), 1)),
                ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com']
            ),
            'express_shipping' => 0,
            'customer_id' => $customer->email,
            'customer_name' => trim($customer->firstname . ' ' . $customer->lastname) ?: null,
            'customer_ip' => Tools::getRemoteAddr(),
            'billing_country' => Country::getIsoById((int) $invoiceAddress->id_country),
            'billing_city' => $invoiceAddress->city ?: null,
        ];
        $payload = array_filter($payload, static function ($v) { return $v !== null; });

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
            return; // fail open — don't block a sale over a connectivity hiccup
        }

        if ($result['risk_level'] === 'block') {
            // TODO: set a custom "Fraud Suspected" order state via OrderHistory
            // (requires creating that state in Statuses > Statuses first):
            //   $history = new OrderHistory();
            //   $history->id_order = $order->id;
            //   $history->changeIdOrderState((int) Configuration::get('MERAFRAUD_FRAUD_STATE_ID'), $order);
            //   $history->addWithemail();
            PrestaShopLogger::addLog('MeraFraud: order #' . $order->id . ' flagged HIGH RISK: ' . implode('; ', $result['reasons']), 3);
        } elseif ($result['risk_level'] === 'review') {
            PrestaShopLogger::addLog('MeraFraud: order #' . $order->id . ' flagged for review: ' . implode('; ', $result['reasons']), 2);
        }

        // --- Feed cancelled/refunded/delivered outcomes back into MeraFraud ---
        // Configuration::get('PS_OS_...') are PrestaShop's own stable IDs for
        // its built-in order states (same ones used throughout core code),
        // safer than hardcoding numeric state IDs which aren't guaranteed
        // identical across every install. Without this, MeraFraud only ever
        // sees "placed" (from hookActionValidateOrder above) and the serial-
        // canceller detection in customer_history.py never has cancellation
        // data to flag on.
        $currentStateId = (int) $order->getCurrentState();
        $outcome = null;
        if (in_array($currentStateId, [(int) Configuration::get('PS_OS_CANCELED'), (int) Configuration::get('PS_OS_REFUND')], true)) {
            $outcome = 'cancelled';
        } elseif ($currentStateId === (int) Configuration::get('PS_OS_DELIVERED')) {
            $outcome = 'fulfilled';
        }
        if ($outcome) {
            $this->reportOutcome(
                $customer->email,
                $outcome,
                $order->id,
                trim($customer->firstname . ' ' . $customer->lastname) ?: null,
                Country::getIsoById((int) $invoiceAddress->id_country) ?: null,
                $invoiceAddress->city ?: null
            );
        }
    }
}
