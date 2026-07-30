// MeraFraud — shared scroll-reveal + decorative "smart cube" motion for marketing pages
document.addEventListener('DOMContentLoaded', () => {
  const items = document.querySelectorAll('.reveal');
  if(items.length){
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if(entry.isIntersecting){
          entry.target.classList.add('in');
          observer.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });

    items.forEach(el => observer.observe(el));
  }

  // Inject the decorative "smart cube" once per page — no markup needed
  // in the HTML files themselves, so every page picks it up automatically.
  if(!document.querySelector('.smart-cube-wrap') && !window.matchMedia('(prefers-reduced-motion: reduce)').matches){
    const wrap = document.createElement('div');
    wrap.className = 'smart-cube-wrap';
    wrap.setAttribute('aria-hidden', 'true');
    wrap.innerHTML =
      '<div class="smart-cube">' +
        '<div class="face f-front"></div>' +
        '<div class="face f-back"></div>' +
        '<div class="face f-right"></div>' +
        '<div class="face f-left"></div>' +
        '<div class="face f-top"></div>' +
        '<div class="face f-bottom"></div>' +
      '</div>';
    document.body.appendChild(wrap);

    // Subtle mouse-driven tilt on top of the constant slow spin —
    // composes with the CSS keyframe via custom properties instead of
    // fighting it directly on the transform property.
    let targetX = 0, targetY = 0, curX = 0, curY = 0;
    const cube = wrap.querySelector('.smart-cube');
    window.addEventListener('mousemove', (e) => {
      targetX = (e.clientX / window.innerWidth - 0.5) * 26;
      targetY = (e.clientY / window.innerHeight - 0.5) * -14;
    });
    function tick(){
      curX += (targetX - curX) * 0.06;
      curY += (targetY - curY) * 0.06;
      cube.style.setProperty('--ctx', curX.toFixed(1) + 'deg');
      cube.style.setProperty('--cty', curY.toFixed(1) + 'deg');
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }
});
