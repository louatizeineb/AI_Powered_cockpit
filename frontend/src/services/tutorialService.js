const STORAGE_PREFIX = "athena:tutorial";

export function tutorialStorageKey(namespace) {
  return `${STORAGE_PREFIX}:${namespace}:dismissed`;
}

export function isTutorialDismissed(namespace) {
  try {
    return window.localStorage.getItem(tutorialStorageKey(namespace)) === "true";
  } catch {
    return false;
  }
}

export function setTutorialDismissed(namespace, dismissed = true) {
  try {
    window.localStorage.setItem(tutorialStorageKey(namespace), dismissed ? "true" : "false");
  } catch {
    // Local storage can be unavailable in restricted browser contexts.
  }
}

export function getTutorialScreen(tutorials, screenKey) {
  return tutorials.find((item) => item.key === screenKey) || tutorials[0] || null;
}

export function tutorialIndex(tutorials, screenKey) {
  return Math.max(0, tutorials.findIndex((item) => item.key === screenKey));
}
