import {createController} from './controller.js';
const controller = createController(chrome);
const report = error => console.warn('CROUS local :', error instanceof Error ? error.message : 'Erreur locale.');
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  controller.message(message, sender).then(respond, error => respond({error: error.message || 'Erreur locale.'}));
  return true;
});
chrome.alarms.onAlarm.addListener(alarm => controller.alarm(alarm.name).catch(report));
chrome.tabs.onUpdated.addListener((id, info, tab) => controller.tabUpdated(id, info, tab).catch(report));
chrome.runtime.onInstalled.addListener(() => controller.startup().catch(report));
chrome.runtime.onStartup.addListener(() => controller.browserStartup().catch(report));
chrome.notifications.onClicked.addListener(() => chrome.tabs.create({url: chrome.runtime.getURL('popup/index.html')}));
controller.startup().catch(report);
