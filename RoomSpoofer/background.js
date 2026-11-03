// Enables the toolbar icon only on the Hunting Together RPG page, and uses it
// as a show/hide toggle for the in-page panel.
const GAME_PATH = 'games/117483759937632';

function setupActionRules() {
  chrome.action.disable();
  chrome.declarativeContent.onPageChanged.removeRules(undefined, () => {
    chrome.declarativeContent.onPageChanged.addRules([
      {
        conditions: [
          new chrome.declarativeContent.PageStateMatcher({
            pageUrl: { hostEquals: 'www.roblox.com', pathContains: GAME_PATH },
          }),
        ],
        actions: [new chrome.declarativeContent.ShowAction()],
      },
    ]);
  });
}

chrome.runtime.onInstalled.addListener(setupActionRules);
chrome.runtime.onStartup.addListener(() => chrome.action.disable());

chrome.action.onClicked.addListener((tab) => {
  chrome.tabs.sendMessage(tab.id, { type: 'roomspoof:toggle' }).catch(() => {});
});
