<?php
/**
 * MeraFraud Integration for WHMCS
 * -------------------------------------
 * Confidence: MEDIUM — WHMCS's hook system and `tblorders`/`tblclients`
 * schema are stable, but exact data available in `$vars` for a given hook
 * point can vary slightly by WHMCS version. Test against your specific
 * version before relying on it.
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

    $accountAgeDays = (int) max(0, floor((time() - strtotime($client->datecreated)) / 86400));
    $freeEmailDomains = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com'];
    $emailDomain = strtolower(substr(strrchr($client->email, '@'), 1));

    // --- Order history: LTV, average order size, recency, 24h velocity ---
    $pastOrders = Capsule::table('tblorders')
        ->where('userid', $client->id)
        ->where('id', '!=', $orderId)
        ->get(['amount', 'date']);
    $nowTs = time();
    $pastAmounts = [];
    $mostRecentTs = null;
    $numTxLast24h = 0;
    foreach ($pastOrders as $pastOrder) {
        $pastAmounts[] = (float) $pastOrder->amount;
        $createdTs = strtotime($pastOrder->date);
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
        $amountRatioToAvg = $avgAmount > 0 ? round(((float) $order->amount) / $avgAmount, 2) : 1.0;
    }
    $timeSinceLastTxMin = $mostRecentTs !== null ? max(0.0, ($nowTs - $mostRecentTs) / 60) : 999999.0;

    // --- Recent cancelled/refunded invoices (closest WHMCS equivalent to
    // "failed payments" — WHMCS doesn't log failed payment *attempts* on
    // an invoice, only its final status, so this is a proxy, not a literal
    // failed-attempt count) ---
    $recentTroubleInvoices = Capsule::table('tblinvoices')
        ->where('userid', $client->id)
        ->whereIn('status', ['Cancelled', 'Refunded'])
        ->where('date', '>=', date('Y-m-d', strtotime('-7 days')))
        ->count();

    $payload = [
        'transaction_amount' => (float) $order->amount,
        'amount_ratio_to_avg' => $amountRatioToAvg,
        'account_age_days' => $accountAgeDays,
        'customer_ltv' => round($customerLtv, 2),
        'time_since_last_tx_min' => round($timeSinceLastTxMin, 1),
        'num_tx_last_24h' => $numTxLast24h,
        'hour_of_day' => (int) date('H', strtotime($order->date)),
        'num_items_in_cart' => 1, // WHMCS orders are usually single-service; adjust if you sell bundles
        'num_failed_payments_7d' => $recentTroubleInvoices, // see note above — proxy, not exact
        'login_attempts_before_purchase' => 1,
        'billing_shipping_mismatch' => 0, // WHMCS is mostly digital services — often not applicable
        // WHMCS doesn't expose an IP-derived country — only the raw IP
        // itself (below). Sending customer_ip + billing_country lets
        // MeraFraud's own IP intelligence compute this mismatch server-side.
        'ip_billing_country_mismatch' => 0,
        'new_device' => 0,
        'new_payment_method' => 0,
        'free_email_domain' => (int) in_array($emailDomain, $freeEmailDomains),
        'express_shipping' => 0,
        'customer_id' => $client->email,
        'customer_ip' => $order->ipaddress ?? '',
        'billing_country' => $client->country ?? null, // tblclients.country is already ISO-2
    ];
    $payload = array_filter($payload, function ($v) {
        return $v !== null;
    });

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
        // Suspend the order pending manual review instead of auto-provisioning.
        // 'Fraud' must exactly match a configured Order Status title in
        // Configuration > System Settings > Order Statuses — WHMCS ships
        // with 'Fraud' as a default status, but confirm it exists on your
        // install (or create it) before relying on this in production.
        logActivity('MeraFraud: Order #' . $orderId . ' flagged HIGH RISK — ' . implode('; ', $result['reasons']));
        localAPI('UpdateOrderStatus', ['orderid' => $orderId, 'orderstatus' => 'Fraud']);
    } elseif ($result['risk_level'] === 'review') {
        logActivity('MeraFraud: Order #' . $orderId . ' flagged for review — ' . implode('; ', $result['reasons']));
    }
});
