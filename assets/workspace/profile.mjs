import {requestJSON} from './request.mjs';

let pending;

export function getAppProfile() {
  if (!pending) {
    pending = requestJSON('/api/app-profile', {timeout: 12000}).then(profile => {
      if (!profile || !['/chat', '/write'].includes(profile.home)
          || typeof profile.features?.rag !== 'boolean' || typeof profile.features?.write !== 'boolean') {
        throw new Error('Account information is temporarily unavailable. Please retry.');
      }
      return profile;
    }).catch(error => { pending = null; throw error; });
  }
  return pending;
}
