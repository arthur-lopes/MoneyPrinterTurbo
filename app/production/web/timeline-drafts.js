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

function mergeTimelineDraft(base, local, saved, activeScenes) {
  if (!base || !saved) return null;
  const order = timeline => timeline.clips.map(clip => clip.scene);
  const sceneSet = [...activeScenes].sort().join(",");
  if ([base, local, saved].some(timeline => order(timeline).sort().join(",") !== sceneSet)) return null;

  const draft = structuredClone(saved);
  const conflicts = [];
  let reapplied = 0;
  const encode = value => JSON.stringify(value, (_, entry) =>
    entry && typeof entry === "object" && !Array.isArray(entry) ?
      Object.fromEntries(Object.entries(entry).sort(([a], [b]) => a.localeCompare(b))) : entry);

  function compare(field, before, mine, current, apply, context = {}) {
    const original = encode(before), edited = encode(mine), latest = encode(current);
    if (edited === original || edited === latest) return;
    if (latest === original) {
      apply(structuredClone(mine));
      reapplied++;
    } else {
      conflicts.push({field, mine, current, ...context, apply});
    }
  }

  const group = (field, keys, original, edited, current, target, context) => {
    const values = source => Object.fromEntries(keys.map(key => [key, source[key]]));
    compare(field, values(original), values(edited), values(current), chosen => Object.assign(target, chosen), context);
  };
  const byScene = timeline => new Map(timeline.clips.map(clip => [clip.scene, clip]));
  const originalClips = byScene(base), editedClips = byScene(local), mergedClips = byScene(draft);
  compare("order", order(base), order(local), order(saved),
    chosen => { draft.clips = chosen.map(scene => mergedClips.get(scene)); });
  for (const clip of saved.clips) {
    const scene = clip.scene, original = originalClips.get(scene), edited = editedClips.get(scene);
    const target = mergedClips.get(scene);
    group("picture", ["video", "trim", "seconds"], original, edited, clip, target, {scene});
    for (const field of ["narration_offset", "effect", "transition"]) {
      compare(field, original[field], edited[field], clip[field], chosen => { target[field] = chosen; }, {scene});
    }
    const before = original.captions || {}, mine = edited.captions || {}, current = clip.captions || {};
    for (const language of new Set([...Object.keys(before), ...Object.keys(mine), ...Object.keys(current)])) {
      compare("caption", before[language], mine[language], current[language], chosen => {
        if (chosen !== undefined) {
          (target.captions ??= {})[language] = chosen;
        } else if (target.captions) {
          delete target.captions[language];
          if (!Object.keys(target.captions).length) delete target.captions;
        }
      }, {scene, language});
    }
  }
  for (const field of ["aspect", "resolution", "subtitles", "watermark"]) {
    compare(field, base[field], local[field], saved[field], chosen => { draft[field] = chosen; });
  }
  group("music", ["music", "music_volume"], base, local, saved, draft);
  group("output", ["languages", "metadata"], base, local, saved, draft);
  return {draft, conflicts, reapplied};
}
