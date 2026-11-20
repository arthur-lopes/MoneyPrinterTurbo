"use strict";

function createScenePreview(onScene) {
  const slots = Array.from({length: 2}, () => {
    const video = node("video", {preload: "auto", playsinline: true});
    video.muted = true;
    return {video, track: typeof VTTCue !== "undefined" && video.addTextTrack("subtitles"), index: -1};
  });
  const mark = node("span", {class: "scene-preview-watermark", hidden: true, "aria-hidden": "true"}, "DRAFT");
  const frame = node("div", {class: "scene-preview-frame", hidden: true}, slots[0].video, mark);
  const status = node("small", {class: "scene-preview-status", role: "status"});
  const time = input(0, {type: "range", min: 0, max: 0, step: ".01"});
  const readout = small("");
  const play = node("button", {type: "button", onclick: togglePlayback});
  const controls = node("div", {class: "scene-preview-controls", hidden: true}, play, time, readout);
  const element = node("div", {class: "scene-preview stack"}, frame, controls, status);
  const sounds = new Map();
  const lifecycle = new AbortController();
  let plan, signature, active = slots[0], audio, sources = [], generation = 0, tick;
  let requested = false, starting = false, pending = false;

  const scene = () => plan?.clips[active.index];
  const localTime = () => Math.min(scene().seconds, Math.max(0, active.video.currentTime - scene().trim));

  function syncControls() {
    play.textContent = plan?.continuous ?
      (requested ? tr("Pause cut", "Pausar corte") : tr("Play cut", "Reproduzir corte")) :
      (requested ? tr("Pause scene", "Pausar cena") : tr("Play scene", "Reproduzir cena"));
    play.disabled = !scene() || (pending && !requested);
    play.setAttribute("aria-busy", String(starting));
    time.disabled = !scene() || pending || starting;
  }

  function showPosition() {
    const clip = scene();
    if (!clip) return;
    const at = localTime();
    const value = clip.start + at;
    time.value = value;
    const text = value.toFixed(1) + " / " + plan.seconds.toFixed(1) + " s";
    if (readout.textContent !== text) {
      time.setAttribute("aria-valuetext", text);
      readout.textContent = text;
    }
    const light = clip.fade ? Math.min(1, at / clip.fade, (clip.seconds - at) / clip.fade) : 1;
    active.video.style.filter = light === 1 ? "" : `brightness(${Math.max(0, light)})`;
  }

  function stopSound() {
    for (const [part, gain] of sources) {
      part.stop();
      part.disconnect();
      gain.disconnect();
    }
    sources = [];
  }

  function schedule(buffer, delay, offset, length, volume, loop = false) {
    if (!buffer || length <= 0 || volume === 0) return;
    const part = audio.createBufferSource();
    const gain = audio.createGain();
    part.buffer = buffer;
    part.loop = loop;
    gain.gain.value = volume;
    part.connect(gain).connect(audio.destination);
    part.start(audio.currentTime + delay, offset, length);
    sources.push([part, gain]);
  }

  function syncSound() {
    stopSound();
    const clip = scene(), video = active.video;
    if (!clip || !requested || video.paused || video.seeking || video.readyState < 3) return;
    const at = localTime(), left = clip.seconds - at;
    const voice = sounds.get(clip.voice)?.buffer;
    const effect = sounds.get(clip.effect)?.buffer;
    const music = sounds.get(plan.music)?.buffer;
    const delay = Math.max(0, clip.narration_offset - at);
    const offset = Math.max(0, at - clip.narration_offset);
    if (voice) schedule(voice, delay, offset, Math.min(voice.duration - offset, left - delay), effect ? .5 : 1);
    if (effect) schedule(effect, 0, at, Math.min(effect.duration - at, left), .5);
    if (music) schedule(music, 0, (clip.start + at) % music.duration, left, plan.music_volume, true);
  }

  function soundEntry(url) {
    if (sounds.has(url)) return sounds.get(url);
    const controller = new AbortController();
    const bytes = fetch(url, {signal: controller.signal}).then(res => res.ok ? res.arrayBuffer() : null).catch(() => null);
    const entry = {controller, bytes};
    sounds.set(url, entry);
    return entry;
  }

  function soundBuffer(url) {
    const entry = soundEntry(url);
    entry.decode ??= entry.bytes.then(bytes => {
      if (!bytes) throw new Error("Audio unavailable");
      return audio.decodeAudioData(bytes);
    }).then(buffer => { entry.buffer = buffer; }).catch(error => {
      if (sounds.get(url) === entry) sounds.delete(url);
      throw error;
    });
    return entry.decode;
  }

  function soundUrls(index) {
    const clip = plan.clips[index];
    return clip ? [clip.voice, clip.effect, plan.music_volume > 0 && plan.music].filter(Boolean) : [];
  }

  function warmSounds() {
    const next = soundUrls(active.index + 1);
    const retained = new Set([...soundUrls(active.index), ...next]);
    for (const [url, entry] of sounds) {
      if (retained.has(url)) continue;
      entry.controller.abort();
      sounds.delete(url);
    }
    retained.forEach(soundEntry);
    if (audio) next.forEach(url => soundBuffer(url).catch(() => {}));
  }

  function prepare(slot, index, seek) {
    const clip = plan.clips[index], video = slot.video;
    slot.index = clip ? index : -1;
    if (!clip) {
      if (video.hasAttribute("src")) {
        video.removeAttribute("src");
        video.load();
      }
      return;
    }
    if (video.getAttribute("src") !== clip.take) video.src = clip.take;
    else if (video.error && seek != null) video.load();
    if (seek != null) video.currentTime = clip.trim + seek;
    const cueKey = JSON.stringify([clip.trim, clip.seconds, clip.cues]);
    if (!slot.track || slot.cueKey === cueKey) return;
    slot.cueKey = cueKey;
    for (const cue of Array.from(slot.track.cues || [])) slot.track.removeCue(cue);
    slot.track.mode = "showing";
    for (const cue of clip.cues) {
      const end = Math.min(cue.end, clip.seconds);
      if (end > cue.start) slot.track.addCue(new VTTCue(clip.trim + cue.start, clip.trim + end,
        cue.text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")));
    }
  }

  function halt() {
    generation++;
    requested = starting = false;
    active.video.pause();
    stopSound();
    cancelAnimationFrame(tick);
    status.textContent = "";
    syncControls();
  }

  async function begin() {
    const playback = ++generation;
    const video = active.video;
    requested = starting = true;
    const notice = setTimeout(() => {
      if (playback === generation) status.textContent = plan.continuous ?
        tr("Loading media…", "Carregando mídia…") : tr("Loading narration…", "Carregando narração…");
    }, 180);
    syncControls();
    try {
      audio ??= new AudioContext();
      await audio.resume();
      if (playback !== generation) return;
      warmSounds();
      await Promise.all(soundUrls(active.index).map(soundBuffer));
      if (playback !== generation) return;
      await video.play();
      if (playback === generation) status.textContent = "";
    } catch {
      if (playback === generation) {
        halt();
        status.textContent = tr("Could not play this material. Try again.", "Não foi possível reproduzir esta mídia. Tente novamente.");
      }
    } finally {
      clearTimeout(notice);
      if (playback === generation) {
        starting = false;
        syncControls();
      }
    }
  }

  function activate(index, offset, resume = false) {
    halt();
    const ready = slots.find(slot => slot.index === index);
    active = ready || active;
    const preload = ready && ready.video.readyState >= 2 && !ready.video.seeking &&
      Math.abs(ready.video.currentTime - plan.clips[index].trim - offset) < .005;
    prepare(active, index, preload ? null : offset);
    for (const slot of slots) slot.video.hidden = slot !== active;
    const standby = slots.find(slot => slot !== active);
    if (plan.continuous) prepare(standby, index + 1, 0);
    warmSounds();
    showPosition();
    if (plan.continuous) onScene?.(scene().scene);
    if (resume) begin();
  }

  function finishScene() {
    const next = active.index + 1;
    if (requested && plan.clips[next]) {
      activate(next, 0, true);
    } else {
      halt();
      active.video.currentTime = Math.min(active.video.duration, scene().trim + scene().seconds);
      showPosition();
    }
  }

  function advance() {
    if (!scene() || active.video.paused) return;
    if (localTime() >= scene().seconds) return finishScene();
    showPosition();
    tick = requestAnimationFrame(advance);
  }

  function togglePlayback() {
    if (!scene() || (pending && !requested)) return;
    if (requested) return halt();
    if (active.video.error) return activate(active.index, localTime(), true);
    if (active.video.ended || localTime() >= scene().seconds - .01) {
      const restart = active.index === plan.clips.length - 1 ? 0 : active.index;
      return activate(restart, 0, true);
    }
    begin();
  }

  for (const slot of slots) {
    const {video} = slot;
    for (const event of ["pause", "waiting", "seeking"]) {
      video.addEventListener(event, () => { if (active === slot) stopSound(); });
    }
    for (const event of ["playing", "seeked"]) {
      video.addEventListener(event, () => { if (active === slot) syncSound(); });
    }
    video.addEventListener("play", () => {
      if (active !== slot) return;
      cancelAnimationFrame(tick);
      advance();
    });
    video.addEventListener("ended", () => { if (active === slot && scene()) finishScene(); });
    video.addEventListener("timeupdate", () => { if (active === slot) showPosition(); });
    video.addEventListener("error", () => {
      if (active !== slot || !scene()) return;
      halt();
      status.textContent = tr("Could not load the take.", "Não foi possível carregar a tomada.");
    });
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden && requested) halt();
  }, {signal: lifecycle.signal});

  time.addEventListener("input", event => {
    event.stopPropagation();
    if (!scene()) return;
    const value = Number(time.value);
    if (value >= plan.seconds) {
      const last = plan.clips.length - 1;
      activate(last, plan.clips[last].seconds);
      return;
    }
    const index = plan.clips.findLastIndex(clip => clip.start <= value);
    const local = value - plan.clips[index].start;
    if (index === active.index) active.video.currentTime = scene().trim + local;
    else activate(index, local, requested);
  });

  function unload() {
    halt();
    for (const entry of sounds.values()) entry.controller.abort();
    sounds.clear();
    for (const slot of slots) {
      slot.index = -1;
      slot.video.removeAttribute("src");
      slot.video.load();
    }
    plan = signature = null;
    active = slots[0];
    frame.hidden = controls.hidden = true;
  }

  function apply(next, selected, offset = 0) {
    const continuous = Boolean(next.clips);
    const clips = next.clips || [{...next, start: 0}];
    const key = JSON.stringify([continuous, next.aspect, next.music, next.music_volume,
      clips.map(({cues, title, ...timing}) => timing)]);
    const changed = key !== signature;
    if (changed) unload();
    plan = {...next, clips, continuous};
    signature = key;
    if (continuous && !frame.contains(slots[1].video)) frame.insertBefore(slots[1].video, mark);
    if (!continuous) slots[1].video.remove();
    for (const slot of slots) slot.video.setAttribute("aria-label", continuous ?
      tr("Cut in progress", "Corte em edição") : tr("Scene in progress", "Cena em edição"));
    time.setAttribute("aria-label", continuous ? tr("Cut playhead", "Posição no corte") : tr("Scene playhead", "Posição na cena"));
    frame.classList.toggle("portrait", next.aspect === "9:16");
    mark.hidden = !next.watermark;
    frame.hidden = controls.hidden = false;
    time.max = next.seconds;
    pending = false;
    status.textContent = "";
    if (changed) {
      const index = Math.max(0, clips.findIndex(clip => clip.scene === selected));
      activate(index, Math.min(offset, clips[index].seconds));
    } else slots.forEach(slot => { if (slot.index >= 0) prepare(slot, slot.index); });
    syncControls();
    showPosition();
  }

  return {element, apply, clear: unload,
    playing: () => requested,
    pause: halt,
    position() {
      const clip = scene();
      return clip && {scene: clip.scene, offset: localTime()};
    },
    jump(id) {
      if (!plan?.continuous) return;
      const index = plan.clips.findIndex(clip => clip.scene === id);
      if (index >= 0) activate(index, 0, requested);
    },
    updating() {
      pending = true;
      status.textContent = tr("Updating preview…", "Atualizando prévia…");
      syncControls();
    },
    error(message) {
      unload();
      pending = false;
      status.textContent = message;
      syncControls();
    },
    dispose() {
      lifecycle.abort();
      unload();
      audio?.close().catch(() => {});
    },
  };
}
