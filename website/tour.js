/**
 * MeraFraud — Interactive Product Tour
 * ---------------------------------------
 * A self-contained spotlight walkthrough: dims the page, cuts a bright
 * "hole" over the current target section, and shows a tooltip with
 * Next/Back/Skip controls. No external library — works by giving the
 * spotlight element a huge box-shadow that darkens everything except
 * itself.
 *
 * Usage: include this script on a page, then either call
 * MeraTour.start() from a button's onclick, or add
 * data-tour-autostart="true" to the <script> tag to launch on load
 * (first visit only, remembered via localStorage).
 */
(function(){
  const scriptTag = document.currentScript;

  const STEPS = [
    { target: '#tour-hero', title: 'Welcome to MeraFraud',
      text: "This is the homepage your customers land on. Let's walk through what MeraFraud actually does, in about 30 seconds." },
    { target: '#tour-trust', title: 'The numbers that matter',
      text: 'Real model performance, not marketing fluff — ROC-AUC, response time, and precision, measured on the current model.' },
    { target: '#tour-problem', title: 'The problem, and the fix',
      text: "SMEs get hit by fraud but can't afford enterprise tools. MeraFraud is built to be affordable and fast to set up." },
    { target: '#tour-features', title: 'What you actually get',
      text: 'Real-time scoring, plain-language explanations for every decision, and risk profiles you control — not a black box.' },
    { target: '#tour-steps', title: 'Getting started', placement: 'top',
      text: 'Sign up, call one API endpoint from your checkout, get a decision back in milliseconds. That\'s the whole integration.' },
    { target: '#tour-cta', title: 'Try it yourself', placement: 'top',
      text: "Ready? Get a free API key and start scoring real transactions right now." },
  ];

  const css = `
  #mf-tour-overlay{ position:fixed; inset:0; z-index:9998; pointer-events:none; }
  #mf-tour-spot{
    position:fixed; z-index:9998; border-radius:14px; pointer-events:none;
    box-shadow:0 0 0 9999px rgba(6,3,20,0.78);
    transition:top .45s cubic-bezier(.4,0,.2,1), left .45s cubic-bezier(.4,0,.2,1),
               width .45s cubic-bezier(.4,0,.2,1), height .45s cubic-bezier(.4,0,.2,1);
    outline:2px solid rgba(236,72,153,0.6);
  }
  #mf-tour-card{
    position:fixed; z-index:9999; width:300px; max-width:calc(100vw - 32px);
    background:#150b34; border:1px solid rgba(255,255,255,0.14); border-radius:16px;
    padding:20px; box-shadow:0 20px 50px rgba(0,0,0,0.5);
    font-family:'Space Grotesk',sans-serif; color:#f5f3ff;
    transition:top .45s cubic-bezier(.4,0,.2,1), left .45s cubic-bezier(.4,0,.2,1);
  }
  #mf-tour-card .step-dots{ display:flex; gap:5px; margin-bottom:12px; }
  #mf-tour-card .dot{ width:6px; height:6px; border-radius:50%; background:rgba(255,255,255,0.2); }
  #mf-tour-card .dot.active{ background:#ec4899; width:16px; border-radius:3px; }
  #mf-tour-card h4{ font-family:'Fraunces',serif; font-size:16px; font-weight:600; margin-bottom:8px; }
  #mf-tour-card p{ font-size:13px; color:#b8aee0; line-height:1.55; margin-bottom:16px; }
  #mf-tour-card .row{ display:flex; justify-content:space-between; align-items:center; gap:8px; }
  #mf-tour-card .skip{ font-size:12px; color:#b8aee0; cursor:pointer; background:none; border:none; }
  #mf-tour-card .skip:hover{ color:#f5f3ff; }
  #mf-tour-card .nav{ display:flex; gap:8px; }
  #mf-tour-card button.nav-btn{
    font-size:12.5px; padding:8px 14px; border-radius:8px; cursor:pointer; font-family:inherit; font-weight:600;
  }
  #mf-tour-card .btn-back{ background:transparent; border:1px solid rgba(255,255,255,0.15); color:#f5f3ff; }
  #mf-tour-card .btn-next{ background:linear-gradient(135deg,#7c3aed,#ec4899); border:none; color:#fff; }
  #mf-tour-launch{
    position:fixed; bottom:24px; left:24px; z-index:9997;
    background:rgba(21,11,52,0.95); border:1px solid rgba(255,255,255,0.14); color:#f5f3ff;
    padding:11px 18px; border-radius:999px; font-family:'Space Grotesk',sans-serif; font-size:13px; font-weight:600;
    cursor:pointer; display:flex; align-items:center; gap:8px; box-shadow:0 10px 26px rgba(0,0,0,0.35);
  }
  #mf-tour-launch:hover{ border-color:#ec4899; }
  `;

  let current = 0;
  let active = false;

  function inject(){
    if(document.getElementById('mf-tour-style')) return;
    const style = document.createElement('style');
    style.id = 'mf-tour-style';
    style.textContent = css;
    document.head.appendChild(style);

    const spot = document.createElement('div');
    spot.id = 'mf-tour-spot';
    spot.style.display = 'none';
    document.body.appendChild(spot);

    const card = document.createElement('div');
    card.id = 'mf-tour-card';
    card.style.display = 'none';
    document.body.appendChild(card);

    const launch = document.createElement('button');
    launch.id = 'mf-tour-launch';
    launch.innerHTML = '🧭 Product Tour';
    launch.onclick = start;
    document.body.appendChild(launch);
  }

  function positionSpotlight(el){
    const r = el.getBoundingClientRect();
    const pad = 12;
    const spot = document.getElementById('mf-tour-spot');
    spot.style.top = (r.top - pad) + 'px';
    spot.style.left = (r.left - pad) + 'px';
    spot.style.width = (r.width + pad*2) + 'px';
    spot.style.height = (r.height + pad*2) + 'px';
    spot.style.display = 'block';
  }

  function positionCard(el, placement){
    const r = el.getBoundingClientRect();
    const card = document.getElementById('mf-tour-card');
    const cardH = card.offsetHeight || 180;
    let top;
    if(placement === 'top'){
      top = r.top - cardH - 24;
      if(top < 16) top = r.bottom + 24;
    } else {
      top = r.bottom + 24;
      if(top + cardH > window.innerHeight - 16) top = Math.max(16, r.top - cardH - 24);
    }
    let left = r.left + r.width/2 - 150;
    left = Math.max(16, Math.min(left, window.innerWidth - 316));
    card.style.top = top + 'px';
    card.style.left = left + 'px';
  }

  function renderStep(){
    const step = STEPS[current];
    const el = document.querySelector(step.target);
    if(!el){ next(); return; }

    el.scrollIntoView({ behavior:'smooth', block:'center' });

    setTimeout(() => {
      positionSpotlight(el);
      const card = document.getElementById('mf-tour-card');
      card.style.display = 'block';
      const dots = STEPS.map((_, i) => `<span class="dot ${i===current?'active':''}"></span>`).join('');
      card.innerHTML = `
        <div class="step-dots">${dots}</div>
        <h4>${step.title}</h4>
        <p>${step.text}</p>
        <div class="row">
          <button class="skip" onclick="MeraTour.end()">Skip tour</button>
          <div class="nav">
            ${current > 0 ? '<button class="nav-btn btn-back" onclick="MeraTour.back()">Back</button>' : ''}
            <button class="nav-btn btn-next" onclick="MeraTour.next()">${current === STEPS.length-1 ? 'Finish' : 'Next'}</button>
          </div>
        </div>
      `;
      positionCard(el, step.placement);
    }, 380);
  }

  function start(){
    active = true;
    current = 0;
    document.getElementById('mf-tour-launch').style.display = 'none';
    renderStep();
    localStorage.setItem('merafraud_tour_seen', 'true');
  }
  function next(){
    if(current < STEPS.length - 1){ current++; renderStep(); }
    else end();
  }
  function back(){
    if(current > 0){ current--; renderStep(); }
  }
  function end(){
    active = false;
    document.getElementById('mf-tour-spot').style.display = 'none';
    document.getElementById('mf-tour-card').style.display = 'none';
    document.getElementById('mf-tour-launch').style.display = 'flex';
  }

  window.addEventListener('resize', () => { if(active) renderStep(); });

  window.MeraTour = { start, next, back, end };

  document.addEventListener('DOMContentLoaded', () => {
    inject();
    const autostart = scriptTag && scriptTag.dataset.tourAutostart === 'true';
    if(autostart && !localStorage.getItem('merafraud_tour_seen')){
      setTimeout(start, 900);
    }
  });
})();
