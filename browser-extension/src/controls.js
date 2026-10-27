const ACTIONS = Object.freeze(['GET_STATUS','START','STOP','TEST']);
export function controlMessage(action, event) {
  if (!ACTIONS.includes(action) || (action !== 'GET_STATUS' && event?.isTrusted !== true)) return null;
  return {type:'CONTENT_CONTROL',action};
}
