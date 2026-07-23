<?php
/**
 * MeraFraud Integration for PrestaShop
 * -------------------------------------------
 * Confidence: HIGH — PrestaShop's hook system is standard and stable.
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
        return parent::install() && $this->registerHook('actionOrderStatusPostUpdate');
    }

    public function hookActionOrderStatusPostUpdate($params)
    {
        /** @var Order $order */
        $order = $params['order'] ?? null;
        if (!$order) {
            return;
        }

        $customer = new Customer($order->id_customer);
        $address = new Address($order->id_address_invoice);

        $payload = [
            'transaction_amount' => (float) $order->total_paid,
            'amount_ratio_to_avg' => 1.2, // TODO: compute from customer's order history
            'account_age_days' => (int) ((time() - strtotime($customer->date_add)) / 86400),
            'customer_ltv' => 0, // TODO: sum of customer's past orders
            'time_since_last_tx_min' => 999,
            'num_tx_last_24h' => 0, // TODO: query recent orders for this customer
            'hour_of_day' => (int) date('H'),
            'num_items_in_cart' => (int) $order->getProducts() ? count($order->getProducts()) : 1,
            'num_failed_payments_7d' => 0,
            'login_attempts_before_purchase' => 1,
            'billing_shipping_mismatch' => 0, // TODO: compare invoice vs delivery address IDs
            'ip_billing_country_mismatch' => 0,
            'new_device' => 0,
            'new_payment_method' => 0,
            'free_email_domain' => (int) in_array(
                strtolower(substr(strrchr($customer->email, '@'), 1)),
                ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com']
            ),
            'express_shipping' => 0,
            'customer_id' => $customer->email,
            'customer_ip' => Tools::getRemoteAddr(),
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
            return; // fail open — don't block a sale over a connectivity hiccup
        }

        if ($result['risk_level'] === 'block') {
            $order->addOrderPayment(0, null, null, null, null, null, null); // no-op placeholder
            // TODO: set a custom "Fraud Suspected" order state via OrderHistory
            PrestaShopLogger::addLog('MeraFraud: order #' . $order->id . ' flagged HIGH RISK: ' . implode('; ', $result['reasons']), 3);
        } elseif ($result['risk_level'] === 'review') {
            PrestaShopLogger::addLog('MeraFraud: order #' . $order->id . ' flagged for review: ' . implode('; ', $result['reasons']), 2);
        }
    }
}
