/**
 * MeraFraud — Floating Support Widget
 * ------------------------------------
 * Self-contained: injects its own styles + markup, so it can be dropped
 * into any page (website or dashboard) with a single <script> tag.
 *
 * Three tiers of help, in order:
 *   1. Autonomous bot — quick keyword-matched answers to common questions
 *   2. WhatsApp — for anyone who wants to talk to a real person right away
 *   3. "Message an advisor" — a ticket-style form for anything the bot
 *      can't resolve (NOT wired to a real inbox yet — see console note)
 *
 * IMPORTANT: WhatsApp number below is a placeholder. Replace WHATSAPP_NUMBER
 * with the real business WhatsApp number (international format, no +) before
 * going live, and connect the advisor form to a real inbox/helpdesk (e.g.
 * a shared email, Zendesk, Freshdesk, or Crisp/Intercom-style tool).
 */
(function(){
  const WHATSAPP_NUMBER_FALLBACK = "905374575844"; // used only if /api/config is unreachable

  const FAQ = [
    { keys: ['api key','apikey','anahtar','key lost','sk_live'],
      answer: "You can get (or re-check) your API key from the Sign Up page — each store gets its own key instantly. If you've lost a key, you'll need a new one for now (key recovery isn't built yet), via the same Sign Up flow." },
    { keys: ['threshold','eşik','risk profile','block review','sensitivity'],
      answer: "You can update your risk thresholds anytime via PUT /api/tenants/thresholds with your API key, or by choosing a new profile (Lenient/Standard/Strict) — see the README's API section for the exact request." },
    { keys: ['invoice','fatura','billing','payment','ödeme','charge','card'],
      answer: "Billing runs monthly after any 7-day trial ends. For invoice copies or payment issues, it's best to talk to a human advisor — use the button below." },
    { keys: ['trial','deneme','free','7 day','7-day'],
      answer: "Every plan (including Starter) includes a 7-day free trial. You won't be charged until day 8, and you can cancel anytime before then from Checkout or by contacting us." },
    { keys: ['report','rapor','export','csv','data download','indirmek'],
      answer: "Go to Dashboard → Settings, then click \"📄 Export Transaction Report (CSV)\" under Usage — it downloads your real transaction history instantly, no ticket needed." },
    { keys: ['integrate','integration','entegrasyon','how to connect','setup'],
      answer: "Call POST /api/predict with your API key and transaction details from your checkout flow — see the README for a full example, or the homepage's 'How It Works' section." },
    { keys: ['map','harita','geo'],
      answer: "The Global Risk Map on your dashboard shows recent flagged transactions by location — it refreshes automatically and uses your account's live activity." },
    { keys: ['cancel','iptal','close account','delete account'],
      answer: "To cancel or delete your account, please talk to a human advisor — this isn't self-serve yet in the MVP." },
  ];

  function matchFAQ(text){
    const t = text.toLowerCase();
    for(const item of FAQ){
      if(item.keys.some(k => t.includes(k))) return item.answer;
    }
    return null;
  }

  const css = `
  #mf-support-bubble{
    position:fixed; bottom:24px; right:24px; z-index:9999;
    width:58px; height:58px; border-radius:50%; cursor:pointer; border:none;
    background:linear-gradient(135deg,#7c3aed,#ec4899);
    box-shadow:0 10px 30px -6px rgba(236,72,153,0.55);
    display:flex; align-items:center; justify-content:center; font-size:24px;
    transition:transform .2s ease;
  }
  #mf-support-bubble:hover{ transform:scale(1.07); }
  #mf-support-panel{
    position:fixed; bottom:92px; right:24px; z-index:9999; width:340px; max-width:calc(100vw - 32px);
    max-height:70vh; display:none; flex-direction:column; overflow:hidden;
    background:#150b34; border:1px solid rgba(255,255,255,0.12); border-radius:18px;
    box-shadow:0 20px 60px rgba(0,0,0,0.5); font-family:'Space Grotesk',sans-serif;
  }
  #mf-support-panel.open{ display:flex; }
  .mf-sp-head{
    padding:16px 18px; background:linear-gradient(135deg,rgba(124,58,237,0.35),rgba(236,72,153,0.25));
    border-bottom:1px solid rgba(255,255,255,0.08);
  }
  .mf-sp-head .title{ font-family:'Fraunces',serif; font-weight:600; font-size:15.5px; color:#f5f3ff; }
  .mf-sp-head .sub{ font-size:11.5px; color:#b8aee0; margin-top:2px; }
  .mf-sp-tabs{ display:flex; gap:6px; padding:10px 12px 0; }
  .mf-sp-tab{
    flex:1; text-align:center; font-size:11.5px; padding:8px 6px; border-radius:8px 8px 0 0;
    color:#b8aee0; cursor:pointer; background:rgba(255,255,255,0.03);
  }
  .mf-sp-tab.active{ background:#1c1044; color:#f5f3ff; font-weight:600; }
  .mf-sp-body{ flex:1; overflow-y:auto; padding:14px 16px; background:#1c1044; }
  .mf-sp-msg{ font-size:13px; color:#e5e0f5; background:rgba(255,255,255,0.05); padding:10px 12px; border-radius:10px; margin-bottom:10px; line-height:1.5; }
  .mf-sp-msg.user{ background:rgba(124,58,237,0.35); margin-left:24px; }
  .mf-sp-quick{ display:flex; flex-wrap:wrap; gap:6px; margin-bottom:12px; }
  .mf-sp-chip{
    font-size:11px; color:#f5f3ff; background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.1);
    padding:6px 10px; border-radius:999px; cursor:pointer;
  }
  .mf-sp-chip:hover{ border-color:#ec4899; }
  .mf-sp-inputrow{ display:flex; gap:8px; padding:12px; background:#150b34; border-top:1px solid rgba(255,255,255,0.08); }
  .mf-sp-inputrow input{
    flex:1; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:10px;
    padding:10px 12px; color:#f5f3ff; font-size:13px; font-family:inherit;
  }
  .mf-sp-inputrow button{
    border:none; border-radius:10px; padding:0 16px; background:linear-gradient(135deg,#7c3aed,#ec4899);
    color:#fff; font-weight:600; cursor:pointer; font-size:13px;
  }
  .mf-sp-escalate{
    display:flex; gap:8px; padding:12px; background:#150b34; border-top:1px solid rgba(255,255,255,0.08);
  }
  .mf-sp-btn{
    flex:1; text-align:center; text-decoration:none; font-size:12px; padding:10px 8px; border-radius:10px; cursor:pointer;
    border:1px solid rgba(255,255,255,0.12); color:#f5f3ff; background:rgba(255,255,255,0.03);
  }
  .mf-sp-btn.whatsapp{ background:rgba(37,211,102,0.15); border-color:rgba(37,211,102,0.4); color:#25d366; }
  .mf-sp-form textarea, .mf-sp-form input{
    width:100%; background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.12); border-radius:10px;
    padding:10px 12px; color:#f5f3ff; font-size:13px; font-family:inherit; margin-bottom:10px;
  }
  .mf-sp-form textarea{ min-height:80px; resize:vertical; }
  .mf-sp-form label{ font-size:11.5px; color:#b8aee0; display:block; margin-bottom:5px; }
  .mf-sp-note{ font-size:10.5px; color:#b8aee0; opacity:0.75; margin-top:8px; }
  `;

  const styleTag = document.createElement('style');
  styleTag.textContent = css;
  document.head.appendChild(styleTag);

  const bubble = document.createElement('button');
  bubble.id = 'mf-support-bubble';
  bubble.innerHTML = '💬';
  bubble.setAttribute('aria-label','Open support chat');

  const panel = document.createElement('div');
  panel.id = 'mf-support-panel';
  panel.innerHTML = `
    <div class="mf-sp-head">
      <div class="title">MeraFraud Support</div>
      <div class="sub">Instant answers, or talk to a real person</div>
      <div class="mf-sp-tabs">
        <div class="mf-sp-tab active" data-tab="bot">Quick Help</div>
        <div class="mf-sp-tab" data-tab="human">Message an Advisor</div>
      </div>
    </div>
    <div class="mf-sp-body" id="mf-sp-body-bot">
      <div class="mf-sp-msg">👋 Hi! Ask me about API keys, thresholds, billing, trials, reports, or integration — I'll try to answer instantly.</div>
      <div class="mf-sp-quick">
        <span class="mf-sp-chip" data-q="How do I get my API key?">API key</span>
        <span class="mf-sp-chip" data-q="How do billing and invoices work?">Billing</span>
        <span class="mf-sp-chip" data-q="How does the free trial work?">Trial</span>
        <span class="mf-sp-chip" data-q="How do I export a report?">Reports</span>
      </div>
    </div>
    <div class="mf-sp-body mf-sp-form" id="mf-sp-body-human" style="display:none;">
      <label>Your email</label>
      <input type="email" id="mf-sp-email" placeholder="you@yourstore.com">
      <label>What do you need help with?</label>
      <textarea id="mf-sp-msg-text" placeholder="Describe your issue…"></textarea>
      <button class="mf-sp-btn" style="background:linear-gradient(135deg,#7c3aed,#ec4899); border:none; width:100%;" id="mf-sp-send">Send to Support Team</button>
      <div class="mf-sp-note">Demo only — not yet connected to a real inbox. In production this would route to a helpdesk (e.g. Freshdesk/Zendesk) or a shared support email.</div>
    </div>
    <div class="mf-sp-inputrow" id="mf-sp-inputrow">
      <input type="text" id="mf-sp-input" placeholder="Type a question…">
      <button id="mf-sp-ask">Ask</button>
    </div>
    <div class="mf-sp-escalate">
      <a href="#" class="mf-sp-btn whatsapp" id="mf-sp-whatsapp" target="_blank" rel="noopener">🟢 WhatsApp</a>
      <span class="mf-sp-btn" id="mf-sp-gotohuman">👤 Talk to advisor</span>
    </div>
  `;

  document.body.appendChild(bubble);
  document.body.appendChild(panel);

  bubble.addEventListener('click', () => panel.classList.toggle('open'));

  const waLink = document.getElementById('mf-sp-whatsapp');
  waLink.href = `https://wa.me/${WHATSAPP_NUMBER_FALLBACK}?text=${encodeURIComponent('Hi MeraFraud, I need help with my account.')}`;
  fetch("https://merafraud-api.onrender.com/api/config").then(r => r.json()).then(cfg => {
    waLink.href = `https://wa.me/${cfg.whatsapp_number}?text=${encodeURIComponent('Hi MeraFraud, I need help with my account.')}`;
  }).catch(() => {}); // keep the fallback number if the API isn't reachable

  function addMsg(text, isUser){
    const body = document.getElementById('mf-sp-body-bot');
    const msg = document.createElement('div');
    msg.className = 'mf-sp-msg' + (isUser ? ' user' : '');
    msg.textContent = text;
    body.appendChild(msg);
    body.scrollTop = body.scrollHeight;
  }

  function askBot(text){
    if(!text.trim()) return;
    addMsg(text, true);
    const answer = matchFAQ(text);
    setTimeout(() => {
      if(answer){
        addMsg(answer, false);
      } else {
        addMsg("I couldn't find a quick answer for that. Tap \"Talk to advisor\" below and I'll pass this along to a real person.", false);
      }
    }, 350);
  }

  document.getElementById('mf-sp-ask').addEventListener('click', () => {
    const input = document.getElementById('mf-sp-input');
    askBot(input.value);
    input.value = '';
  });
  document.getElementById('mf-sp-input').addEventListener('keydown', (e) => {
    if(e.key === 'Enter'){
      const input = document.getElementById('mf-sp-input');
      askBot(input.value);
      input.value = '';
    }
  });
  panel.querySelectorAll('.mf-sp-chip').forEach(chip => {
    chip.addEventListener('click', () => askBot(chip.dataset.q));
  });

  function switchTab(tab){
    panel.querySelectorAll('.mf-sp-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
    document.getElementById('mf-sp-body-bot').style.display = tab === 'bot' ? 'block' : 'none';
    document.getElementById('mf-sp-body-human').style.display = tab === 'human' ? 'block' : 'none';
    document.getElementById('mf-sp-inputrow').style.display = tab === 'bot' ? 'flex' : 'none';
  }
  panel.querySelectorAll('.mf-sp-tab').forEach(t => {
    t.addEventListener('click', () => switchTab(t.dataset.tab));
  });
  document.getElementById('mf-sp-gotohuman').addEventListener('click', () => switchTab('human'));

  document.getElementById('mf-sp-send').addEventListener('click', () => {
    const email = document.getElementById('mf-sp-email').value;
    const text = document.getElementById('mf-sp-msg-text').value;
    if(!email || !text){ return; }
    const body = document.getElementById('mf-sp-body-human');
    body.innerHTML = `<div class="mf-sp-msg">✅ Thanks! (Demo only) A real advisor would follow up at <b>${email}</b> shortly. For anything urgent right now, use WhatsApp below.</div>`;
  });
})();
