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

  // Mouse-reactive parallax for the decorative floating orb(s).
  // The orb still runs its own CSS keyframe float — this just nudges
  // it further with the cursor via CSS custom properties, so it
  // composes with the animation instead of fighting it.
  const orbs = document.querySelectorAll('.floating-orb');
  if(orbs.length && !window.matchMedia('(prefers-reduced-motion: reduce)').matches){
    let targetX = 0, targetY = 0, curX = 0, curY = 0;
    window.addEventListener('mousemove', (e) => {
      targetX = (e.clientX / window.innerWidth - 0.5) * 90;
      targetY = (e.clientY / window.innerHeight - 0.5) * 90;
    });
    function tick(){
      curX += (targetX - curX) * 0.06;
      curY += (targetY - curY) * 0.06;
      orbs.forEach(o => {
        o.style.setProperty('--mx', curX.toFixed(1) + 'px');
        o.style.setProperty('--my', curY.toFixed(1) + 'px');
      });
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }
});
