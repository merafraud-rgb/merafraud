<?php
/**
 * MeraFraud Integration for Magento 2
 * -------------------------------------------
 * Confidence: HIGH — Magento 2's observer/event system and its Customer/
 * Order repositories are stable and have been consistent across versions
 * for years.
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
use Magento\Customer\Api\CustomerRepositoryInterface;
use Magento\Sales\Api\OrderRepositoryInterface;
use Magento\Framework\Api\SearchCriteriaBuilder;
use Magento\Framework\Exception\NoSuchEntityException;
use Psr\Log\LoggerInterface;

class CheckOrderObserver implements ObserverInterface
{
    private const MERAFRAUD_API_BASE = 'https://your-merafraud-api.onrender.com/api'; // ⚠ replace with your real deploy URL
    private const MERAFRAUD_API_KEY  = 'sk_live_REPLACE_ME'; // ⚠ better: pull from Magento's encrypted config

    private Curl $curl;
    private LoggerInterface $logger;
    private CustomerRepositoryInterface $customerRepository;
    private OrderRepositoryInterface $orderRepository;
    private SearchCriteriaBuilder $searchCriteriaBuilder;

    public function __construct(
        Curl $curl,
        LoggerInterface $logger,
        CustomerRepositoryInterface $customerRepository,
        OrderRepositoryInterface $orderRepository,
        SearchCriteriaBuilder $searchCriteriaBuilder
    ) {
        $this->curl = $curl;
        $this->logger = $logger;
        $this->customerRepository = $customerRepository;
        $this->orderRepository = $orderRepository;
        $this->searchCriteriaBuilder = $searchCriteriaBuilder;
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
        $isGuest = (bool) $order->getCustomerIsGuest();
        $customerId = $order->getCustomerId();

        // --- Account age: real signup date from the customer repository ---
        // Guest checkouts have no customer record, so this stays 0 for them.
        $accountAgeDays = 0;
        if (!$isGuest && $customerId) {
            try {
                $customerEntity = $this->customerRepository->getById($customerId);
                $createdAt = $customerEntity->getCreatedAt(); // 'Y-m-d H:i:s' string
                if ($createdAt) {
                    $accountAgeDays = max(0, (int) floor((time() - strtotime($createdAt)) / 86400));
                }
            } catch (NoSuchEntityException $e) {
                // customer record vanished/was merged — leave accountAgeDays at 0
            }
        }

        // --- Order history: LTV, average order size, recency, 24h velocity ---
        $customerLtv = 0.0;
        $amountRatioToAvg = 1.0;
        $timeSinceLastTxMin = 999999.0;
        $numTxLast24h = 0;
        if (!$isGuest && $customerId) {
            try {
                $searchCriteria = $this->searchCriteriaBuilder
                    ->addFilter('customer_id', $customerId, 'eq')
                    ->create();
                $pastOrders = $this->orderRepository->getList($searchCriteria)->getItems();

                $nowTs = time();
                $pastAmounts = [];
                $mostRecentTs = null;
                foreach ($pastOrders as $pastOrder) {
                    if ((int) $pastOrder->getEntityId() === (int) $order->getEntityId()) {
                        continue; // exclude the order that triggered this observer
                    }
                    $pastAmounts[] = (float) $pastOrder->getGrandTotal();
                    $createdTs = strtotime($pastOrder->getCreatedAt());
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
                    $amountRatioToAvg = $avgAmount > 0 ? round(((float) $order->getGrandTotal()) / $avgAmount, 2) : 1.0;
                }
                if ($mostRecentTs !== null) {
                    $timeSinceLastTxMin = max(0.0, ($nowTs - $mostRecentTs) / 60);
                }
            } catch (\Exception $e) {
                $this->logger->warning('MeraFraud: order history lookup failed: ' . $e->getMessage());
            }
        }

        // --- Billing vs. shipping mismatch (compare street AND country) ---
        $billingShippingMismatch = 0;
        if ($billingAddress && $shippingAddress) {
            $streetMismatch = $billingAddress->getStreet(1) !== $shippingAddress->getStreet(1);
            $countryMismatch = $billingAddress->getCountryId() !== $shippingAddress->getCountryId();
            $billingShippingMismatch = ($streetMismatch || $countryMismatch) ? 1 : 0;
        }

        $payload = [
            'transaction_amount' => (float) $order->getGrandTotal(),
            'amount_ratio_to_avg' => $amountRatioToAvg,
            'account_age_days' => $accountAgeDays,
            'customer_ltv' => round($customerLtv, 2),
            'time_since_last_tx_min' => round($timeSinceLastTxMin, 1),
            'num_tx_last_24h' => $numTxLast24h,
            'hour_of_day' => (int) date('H'),
            'num_items_in_cart' => (int) $order->getTotalItemCount(),
            // Magento doesn't track failed-payment attempts or prior login
            // attempts on the order object — these stay as safe defaults.
            'num_failed_payments_7d' => 0,
            'login_attempts_before_purchase' => 1,
            'billing_shipping_mismatch' => $billingShippingMismatch,
            // Magento's order object doesn't expose an IP-derived country —
            // only the raw IP itself (below). Sending customer_ip + billing_country
            // lets MeraFraud's own IP intelligence compute this mismatch server-side.
            'ip_billing_country_mismatch' => 0,
            'new_device' => 0,
            'new_payment_method' => 0,
            'free_email_domain' => (int) in_array(
                strtolower(substr(strrchr($customer, '@'), 1)),
                ['gmail.com', 'yahoo.com', 'hotmail.com', 'outlook.com']
            ),
            'express_shipping' => 0,
            'customer_id' => $customer,
            'customer_ip' => $order->getRemoteIp(),
            'billing_country' => $billingAddress ? $billingAddress->getCountryId() : null,
        ];
        $payload = array_filter($payload, static fn($v) => $v !== null);

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
                $this->orderRepository->save($order);
            } elseif (isset($result['risk_level']) && $result['risk_level'] === 'review') {
                $order->addCommentToStatusHistory(
                    'MeraFraud: flagged for manual review. Reasons: ' . implode('; ', $result['reasons'] ?? [])
                );
                $this->orderRepository->save($order);
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
