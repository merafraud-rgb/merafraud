<?php
/**
 * Plugin Name: MeraFraud Fraud Protection
 * Plugin URI: https://merafraud.com
 * Description: Scores every WooCommerce order in real time with MeraFraud's fraud-detection API. Works automatically with any WooCommerce payment gateway — Shopier, iyzico, PayTR, Stripe, bank transfer, etc. — since it runs at the WooCommerce checkout step, not inside the payment gateway itself.
 * Version: 1.0.0
 * Author: MeraFraud
 * Text Domain: merafraud
 * Requires Plugins: woocommerce
 *
 * ─────────────────────────────────────────────────────────────────────────
 * HOW A STORE OWNER INSTALLS THIS (no coding required):
 *   1. Zip this whole "merafraud-woocommerce" folder
 *   2. WordPress admin → Plugins → Add New → Upload Plugin → choose the zip
 *   3. Activate the plugin
 *   4. WooCommerce → Settings → MeraFraud tab → paste your API key → Save
 *   That's it. Every new order is now scored automatically.
 * ─────────────────────────────────────────────────────────────────────────
 */

if (!defined('ABSPATH')) exit; // no direct access

define('MERAFRAUD_VERSION', '1.0.0');

/**
 * ── Settings page ──────────────────────────────────────────────────────
 * Adds a "MeraFraud" tab under WooCommerce → Settings, so the store owner
 * can paste their API key without touching any code.
 */
add_filter('woocommerce_settings_tabs_array', function ($tabs) {
    $tabs['merafraud'] = __('MeraFraud', 'merafraud');
    return $tabs;
}, 50);

add_action('woocommerce_settings_tabs_merafraud', function () {
    woocommerce_admin_fields(merafraud_get_settings());
});

add_action('woocommerce_update_options_merafraud', function () {
    woocommerce_update_options(merafraud_get_settings());
});

function merafraud_get_settings() {
    return [
        ['title' => __('MeraFraud Settings', 'merafraud'), 'type' => 'title',
         'desc' => __('Get your API key from your MeraFraud dashboard signup page.', 'merafraud'),
         'id' => 'merafraud_section_title'],

        ['title' => __('API Key', 'merafraud'), 'type' => 'text',
         'desc' => __('Your MeraFraud API key (starts with sk_live_...)', 'merafraud'),
         'id' => 'merafraud_api_key', 'css' => 'min-width:350px;'],

        ['title' => __('API Base URL', 'merafraud'), 'type' => 'text',
         'desc' => __('Leave as default unless MeraFraud gave you a different URL.', 'merafraud'),
         'id' => 'merafraud_api_base', 'css' => 'min-width:350px;',
         'default' => 'https://api.merafraud.com/api'],

        ['title' => __('Enabled', 'merafraud'), 'type' => 'checkbox',
         'desc' => __('Actively check new orders against MeraFraud', 'merafraud'),
         'id' => 'merafraud_enabled', 'default' => 'yes'],

        ['title' => __('Block high-risk orders', 'merafraud'), 'type' => 'checkbox',
         'desc' => __('If unchecked, high-risk orders are only flagged for review, never auto-blocked', 'merafraud'),
         'id' => 'merafraud_auto_block', 'default' => 'yes'],

        ['type' => 'sectionend', 'id' => 'merafraud_section_end'],
    ];
}

/**
 * ── The actual fraud check, run at checkout ────────────────────────────
 * Hooks into WooCommerce right after an order is created but before it's
 * fully processed — this runs regardless of which payment gateway
 * (Shopier, iyzico, PayTR, Stripe, COD, bank transfer...) is being used,
 * because it's a WooCommerce-level hook, not a payment-gateway-level one.
 */
add_action('woocommerce_checkout_order_processed', 'merafraud_check_order', 10, 3);

function merafraud_check_order($order_id, $posted_data, $order) {
    if (get_option('merafraud_enabled', 'yes') !== 'yes') return;
    $api_key = get_option('merafraud_api_key');
    if (empty($api_key)) return; // not configured yet — don't break checkout

    $order = $order ?: wc_get_order($order_id);
    if (!$order) return;

    $payload = merafraud_build_payload($order);
    $result = merafraud_call_api('/predict', $api_key, $payload);

    if (is_wp_error($result) || empty($result['risk_level'])) {
        // MeraFraud unreachable — fail OPEN (never block a sale because of
        // a network hiccup). Log it for the store owner to notice.
        $order->add_order_note(__('MeraFraud: could not reach the fraud check API. Order was not blocked.', 'merafraud'));
        return;
    }

    $level = $result['risk_level'];
    $score = isset($result['risk_score']) ? round($result['risk_score'] * 100) : null;
    $reasons = isset($result['reasons']) ? implode('; ', $result['reasons']) : '';

    $order->update_meta_data('_merafraud_risk_score', $score);
    $order->update_meta_data('_merafraud_risk_level', $level);
    $order->save();

    if ($level === 'block' && get_option('merafraud_auto_block', 'yes') === 'yes') {
        $order->update_status('on-hold', sprintf(
            __('MeraFraud flagged this order as high risk (%d%%). Reasons: %s', 'merafraud'), $score, $reasons
        ));
        // Optional: also throw a checkout error so the customer sees a
        // message instead of a silent "on hold" order. Uncomment to enable
        // hard blocking at checkout time (requires calling this hook
        // earlier — see README section "Hard blocking vs hold for review").
    } elseif ($level === 'review') {
        $order->add_order_note(sprintf(
            __('⚠ MeraFraud: flagged for manual review (%d%% risk). Reasons: %s', 'merafraud'), $score, $reasons
        ));
    } else {
        $order->add_order_note(sprintf(__('✓ MeraFraud: approved (%d%% risk).', 'merafraud'), $score));
    }

    // Report this as a "placed" order for serial-canceller tracking
    merafraud_call_api('/orders/outcome', $api_key, [
        'customer_id' => merafraud_customer_id($order),
        'outcome' => 'placed',
        'order_id' => (string) $order_id,
    ]);
}

/** Report cancellations back to MeraFraud so it can spot serial cancellers */
add_action('woocommerce_order_status_cancelled', function ($order_id) {
    $api_key = get_option('merafraud_api_key');
    if (empty($api_key)) return;
    $order = wc_get_order($order_id);
    if (!$order) return;

    merafraud_call_api('/orders/outcome', $api_key, [
        'customer_id' => merafraud_customer_id($order),
        'outcome' => 'cancelled',
        'order_id' => (string) $order_id,
    ]);
});

/**
 * ── Helpers ─────────────────────────────────────────────────────────────
 */
function merafraud_customer_id($order) {
    $email = $order->get_billing_email();
    return $email ?: ('guest-' . $order->get_customer_id());
}

function merafraud_build_payload($order) {
    $customer_id = $order->get_customer_id();
    $email = $order->get_billing_email();
    $free_domains = ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com'];
    $email_domain = strtolower(substr(strrchr($email, '@'), 1));

    // Look up simple customer history from WooCommerce's own order data.
    // A more advanced version could cache this instead of querying live.
    $account_age_days = 0;
    $customer_ltv = 0;
    $orders_last_24h = 0;
    if ($customer_id) {
        $user = get_userdata($customer_id);
        if ($user) $account_age_days = (time() - strtotime($user->user_registered)) / 86400;
        $customer_ltv = (float) wc_get_customer_total_spent($customer_id);
        $orders_last_24h = count(wc_get_orders([
            'customer_id' => $customer_id,
            'date_created' => '>' . (time() - 86400),
            'limit' => -1, 'return' => 'ids',
        ]));
    }

    $billing = $order->get_address('billing');
    $shipping = $order->get_address('shipping');
    $shipping_methods = $order->get_shipping_methods();
    $is_express = false;
    foreach ($shipping_methods as $sm) {
        if (stripos($sm->get_name(), 'express') !== false || stripos($sm->get_name(), 'hızlı') !== false) {
            $is_express = true;
        }
    }

    return [
        'transaction_amount' => (float) $order->get_total(),
        'amount_ratio_to_avg' => $customer_ltv > 0 ? round($order->get_total() / max($customer_ltv / max($orders_last_24h, 1), 1), 2) : 1.5,
        'account_age_days' => round($account_age_days),
        'customer_ltv' => $customer_ltv,
        'time_since_last_tx_min' => 999, // WooCommerce doesn't track this out of the box
        'num_tx_last_24h' => $orders_last_24h,
        'hour_of_day' => (int) current_time('H'),
        'num_items_in_cart' => $order->get_item_count(),
        'num_failed_payments_7d' => 0, // hook into woocommerce_order_status_failed to track this over time
        'login_attempts_before_purchase' => 1,
        'billing_shipping_mismatch' => ($billing['address_1'] !== $shipping['address_1']) ? 1 : 0,
        'ip_billing_country_mismatch' => 0, // requires an IP-geolocation lookup — plug in a geo service here
        'new_device' => 0,
        'new_payment_method' => 0,
        'free_email_domain' => in_array($email_domain, $free_domains, true) ? 1 : 0,
        'express_shipping' => $is_express ? 1 : 0,
        'customer_id' => merafraud_customer_id($order),
    ];
}

function merafraud_call_api($endpoint, $api_key, $payload) {
    $base = rtrim(get_option('merafraud_api_base', 'https://api.merafraud.com/api'), '/');
    $response = wp_remote_post($base . $endpoint, [
        'headers' => [
            'Content-Type' => 'application/json',
            'X-API-Key' => $api_key,
        ],
        'body' => wp_json_encode($payload),
        'timeout' => 5,
    ]);

    if (is_wp_error($response)) return $response;
    $body = wp_remote_retrieve_body($response);
    return json_decode($body, true);
}

/**
 * ── Order list column: show the risk badge at a glance ─────────────────
 */
add_filter('manage_edit-shop_order_columns', function ($columns) {
    $columns['merafraud_risk'] = __('Fraud Risk', 'merafraud');
    return $columns;
});

add_action('manage_shop_order_posts_custom_column', function ($column, $post_id) {
    if ($column !== 'merafraud_risk') return;
    $order = wc_get_order($post_id);
    if (!$order) return;
    $level = $order->get_meta('_merafraud_risk_level');
    $score = $order->get_meta('_merafraud_risk_score');
    if (!$level) { echo '—'; return; }
    $colors = ['approve' => '#34d399', 'review' => '#fbbf24', 'block' => '#fb4570'];
    $color = $colors[$level] ?? '#999';
    printf(
        '<span style="background:%s22; color:%s; border:1px solid %s55; padding:3px 8px; border-radius:999px; font-size:11px; font-weight:600;">%s (%s%%)</span>',
        esc_attr($color), esc_attr($color), esc_attr($color), esc_html(ucfirst($level)), esc_html($score)
    );
}, 10, 2);
