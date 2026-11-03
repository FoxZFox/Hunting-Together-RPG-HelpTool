// Runs in the page's own JS world (not the extension's isolated world) so it
// can reach Roblox.GameLauncher. content.js asks it to join via a DOM event.
(() => {
  if (window.__roomspoofLauncher) return;
  window.__roomspoofLauncher = true;

  document.addEventListener('roomspoof:join', (e) => {
    let req;
    try {
      req = JSON.parse(e.detail);
    } catch {
      return;
    }
    const { placeId, gameId } = req;
    const deepLink =
      `roblox://experiences/start?placeId=${encodeURIComponent(placeId)}` +
      `&gameInstanceId=${encodeURIComponent(gameId)}`;

    let method = 'deeplink';
    try {
      const launcher = window.Roblox && window.Roblox.GameLauncher;
      if (launcher && typeof launcher.joinGameInstance === 'function') {
        launcher.joinGameInstance(Number(placeId), gameId);
        method = 'gamelauncher';
      } else {
        location.href = deepLink;
      }
    } catch {
      location.href = deepLink;
    }

    document.dispatchEvent(
      new CustomEvent('roomspoof:joined', { detail: JSON.stringify({ gameId, method }) })
    );
  });
})();
