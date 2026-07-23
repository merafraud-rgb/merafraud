<?php
/**
 * MeraFraud Integration for WHMCS
 * -------------------------------------
 * Confidence: MEDIUM — WHMCS's hook system is stable, but exact data
 * available in `$vars` for a given hook point can vary slightly by
 * WHMCS version. Test against your specific version before relying on it.
 *
 * INSTALLATION:
 *   Save this file as: whmcs-root/includes/hooks/merafraud.php
 *   WHMCS automatically loads all files in includes/hooks/.
 *
 * This uses the `OrderPaid` hook point — fires after an order/invoice is
 * marked paid, which is when you'd typically want a final fraud check
 * before provisioning a service.
 */

use WHMCS\Database\Capsule;

add_hook('OrderPaid', 1, function ($vars) {
    $orderId = $vars['orderid'] ?? null;
    if (!$orderId) {
        return;
    }

    $order = Capsule::table('tblorders')->where('id', $orderId)->first();
    $client = Capsule::table('tblclients')->where('id', $order->userid)->first();
    if (!$order || !$client) {
        return;
    }

    $apiBase = 'https://your-merafraud-api.onrender.com/api'; // ⚠ replace with your real deploy URL
    $apiKey = 'sk_live_REPLACE_ME';

    $accountAgeDays = (int) ((time() - strtotime($client->datecreated)) / 86400);
    $freeEmailDomains = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com'];
    $emailDomain = strtolower(substr(strrchr($client->email, '@'), 1));

    $payload = [
        'transaction_amount' => (float) $order->amount,
        'amount_ratio_to_avg' => 1.2, // TODO: compute from client's past orders (tblorders where userid = client->id)
        'account_age_days' => $accountAgeDays,
        'customer_ltv' => 0,           // TODO: sum of client's past paid invoices
        'time_since_last_tx_min' => 999,
        'num_tx_last_24h' => 0,        // TODO: count recent tblorders for this client
        'hour_of_day' => (int) date('H'),
        'num_items_in_cart' => 1,      // WHMCS orders are usually single-service; adjust if you sell bundles
        'num_failed_payments_7d' => 0, // TODO: query tblinvoices for recent failed payments
        'login_attempts_before_purchase' => 1,
        'billing_shipping_mismatch' => 0, // WHMCS is mostly digital services — often not applicable
        'ip_billing_country_mismatch' => 0,
        'new_device' => 0,
        'new_payment_method' => 0,
        'free_email_domain' => (int) in_array($emailDomain, $freeEmailDomains),
        'express_shipping' => 0,
        'customer_id' => $client->email,
        'customer_ip' => $order->ipaddress ?? '',
    ];

    $ch = curl_init($apiBase . '/predict');
    curl_setopt($ch, CURLOPT_POST, true);
    curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($payload));
    curl_setopt($ch, CURLOPT_HTTPHEADER, [
        'Content-Type: application/json',
        'X-API-Key: ' . $apiKey,
    ]);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_TIMEOUT, 5);
    $response = curl_exec($ch);
    curl_close($ch);

    $result = json_decode($response, true);
    if (!$result) {
        return; // fail open — never block provisioning over a connectivity hiccup
    }

    if ($result['risk_level'] === 'block') {
        // Suspend the order/service pending manual review instead of auto-provisioning
        logActivity('MeraFraud: Order #' . $orderId . ' flagged HIGH RISK — ' . implode('; ', $result['reasons']));
        // TODO: call localAPI('UpdateOrderStatus', ['orderid' => $orderId, 'status' => 'Fraud']);
    } elseif ($result['risk_level'] === 'review') {
        logActivity('MeraFraud: Order #' . $orderId . ' flagged for review — ' . implode('; ', $result['reasons']));
    }
});
