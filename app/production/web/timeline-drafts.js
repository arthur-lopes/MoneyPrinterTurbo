"use strict";

function browserTimelineDrafts(projectId) {
  const prefix = "mpt-timeline-draft:" + projectId + ":";
  const pointer = prefix + "active";
  let active, writer;

  function read(key) {
    if (!key?.startsWith(prefix)) return null;
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return null;
      const data = JSON.parse(raw), draft = data?.draft;
      if (!Array.isArray(draft?.clips) || !Array.isArray(draft.languages) || !draft.metadata ||
          !draft.clips.every(clip => typeof clip?.scene === "string") ||
          (data.base !== null && typeof data.base !== "string") || !Number.isFinite(data.updated)) return null;
      return {key, raw, data};
    } catch {
      return null;
    }
  }

  function remember() {
    try {
      if (active) sessionStorage.setItem(pointer, active.key);
      else sessionStorage.removeItem(pointer);
    } catch {
      // Drafts remain available through the browser's recovery list.
    }
  }

  try {
    active = read(sessionStorage.getItem(pointer));
  } catch {
    active = null;
  }

  function remove(entry) {
    try {
      if (entry && localStorage.getItem(entry.key) === entry.raw) localStorage.removeItem(entry.key);
    } catch {
      // The saved server revision is unaffected by local storage restrictions.
    }
  }

  function otherKeys() {
    try {
      return Object.keys(localStorage).filter(key => key !== active?.key && key.startsWith(prefix));
    } catch {
      return [];
    }
  }

  return {
    current: () => active?.data,
    hasOthers: () => otherKeys().length > 0,
    others() {
      return otherKeys().map(read).filter(Boolean).sort((a, b) => b.data.updated - a.data.updated);
    },
    select(entry) {
      writer = null;
      active = entry;
      remember();
    },
    write(base, draft, scene) {
      try {
        if (active?.data.base === base && active.data.scene === scene &&
            JSON.stringify(active.data.draft) === JSON.stringify(draft)) return true;
        writer ??= prefix + crypto.randomUUID();
        const data = {base, draft, scene, updated: Date.now()};
        const raw = JSON.stringify(data);
        localStorage.setItem(writer, raw);
        active = {key: writer, raw, data: JSON.parse(raw)};
        remember();
        return true;
      } catch {
        return false;
      }
    },
    reset(discard = false) {
      if (discard) remove(active);
      active = null;
      writer = null;
      remember();
    },
    remove,
  };
}
