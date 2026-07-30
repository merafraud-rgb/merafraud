// MeraFraud — shared scroll-reveal + background motion for marketing pages
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

  // Mouse-reactive parallax for the decorative floating orbs.
  // The orbs still run their own CSS keyframe float — this just nudges
  // them a little further with the cursor via CSS custom properties,
  // so it composes with the animation instead of fighting it.
  const orbs = document.querySelectorAll('.floating-orb');
  if(orbs.length && !window.matchMedia('(prefers-reduced-motion: reduce)').matches){
    let targetX = 0, targetY = 0, curX = 0, curY = 0;
    window.addEventListener('mousemove', (e) => {
      targetX = (e.clientX / window.innerWidth - 0.5) * 46;
      targetY = (e.clientY / window.innerHeight - 0.5) * 46;
    });
    function tick(){
      curX += (targetX - curX) * 0.05;
      curY += (targetY - curY) * 0.05;
      orbs.forEach(o => {
        o.style.setProperty('--mx', curX.toFixed(1) + 'px');
        o.style.setProperty('--my', curY.toFixed(1) + 'px');
      });
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }
});
