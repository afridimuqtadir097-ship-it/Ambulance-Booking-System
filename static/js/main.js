function showToast(message) {
  const zone = document.getElementById('toastZone');
  if (!zone) return;
  const el = document.createElement('div');
  el.className = 'app-toast';
  el.innerHTML = `<div class="d-flex align-items-start gap-2"><i class="bi bi-bell-fill text-brand mt-1"></i><div>${message}</div></div>`;
  zone.appendChild(el);
  setTimeout(() => {
    el.style.transition = 'opacity 0.3s ease';
    el.style.opacity = '0';
    setTimeout(() => el.remove(), 300);
  }, 6000);
}

function pollNotifications() {
  const bell = document.getElementById('notifBell');
  if (!bell) return;
  const portal = window.MEDRUSH_PORTAL || 'patient';
  fetch('/api/notifications?as=' + encodeURIComponent(portal))
    .then(r => r.ok ? r.json() : [])
    .then(items => {
      if (items && items.length) {
        items.forEach(n => showToast(n.message));
      }
    })
    .catch(() => {});
}

if (document.getElementById('notifBell')) {
  setInterval(pollNotifications, 6000);
}
