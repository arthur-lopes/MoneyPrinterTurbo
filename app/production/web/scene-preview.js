"use strict";

function createScenePreview() {
  const video = node("video", {preload: "auto", playsinline: true,
    "aria-label": tr("Scene in progress", "Cena em edição")});
  video.muted = true;
  const frame = node("div", {class: "scene-preview-frame", hidden: true}, video);
  const track = typeof VTTCue !== "undefined" && video.addTextTrack("subtitles");
  const status = node("small", {class: "scene-preview-status", role: "status"});
  const time = input(0, {type: "range", min: 0, max: 0, step: ".01",
    "aria-label": tr("Scene playhead", "Posição na cena")});
  const readout = small("");
  const playText = () => video.paused ? tr("Play scene", "Reproduzir cena") : tr("Pause scene", "Pausar cena");
  const play = node("button", {type: "button", onclick: togglePlayback}, playText());
  const controls = node("div", {class: "scene-preview-controls", hidden: true}, play, time, readout);
  const element = node("div", {class: "scene-preview stack"}, frame, controls, status);
  let plan, audio, voice, source, audioRequest, audioData, generation = 0, tick;
  let pending = false, starting = false;

  function loadVoice(url) {
    audioRequest?.abort();
    audioRequest = new AbortController();
    audioData = fetch(url, {signal: audioRequest.signal}).then(response =>
      response.ok ? response.arrayBuffer() : null).catch(() => null);
  }

  function position() {
    return plan ? Math.min(plan.seconds, Math.max(0, video.currentTime - plan.trim)) : 0;
  }

  function syncControls() {
    play.textContent = playText();
    play.disabled = !plan || pending;
    play.setAttribute("aria-busy", String(starting));
    time.disabled = !plan || pending || starting;
  }

  function showPosition() {
    if (!plan) return;
    const value = position();
    time.value = value;
    const text = value.toFixed(1) + " / " + plan.seconds.toFixed(1) + " s";
    if (readout.textContent !== text) {
      time.setAttribute("aria-valuetext", text);
      readout.textContent = text;
    }
  }

  function stopVoice() {
    if (!source) return;
    source.stop();
    source.disconnect();
    source = null;
  }

  function syncVoice() {
    stopVoice();
    if (!plan || !voice || video.paused || video.seeking || video.readyState < 3) return;
    const at = position();
    const delay = Math.max(0, plan.narration_offset - at);
    const offset = Math.max(0, at - plan.narration_offset);
    const length = Math.min(voice.duration - offset, plan.seconds - at - delay);
    if (length <= 0) return;
    const part = audio.createBufferSource();
    part.buffer = voice;
    part.connect(audio.destination);
    part.start(audio.currentTime + delay, offset, length);
    source = part;
  }

  function advance() {
    if (!plan || video.paused) return;
    if (position() >= plan.seconds) {
      video.pause();
      video.currentTime = Math.min(video.duration, plan.trim + plan.seconds);
    } else {
      tick = requestAnimationFrame(advance);
    }
    showPosition();
  }

  function halt() {
    generation++;
    starting = false;
    video.pause();
    stopVoice();
    cancelAnimationFrame(tick);
    syncControls();
  }

  async function togglePlayback() {
    if (!plan || pending || starting) return;
    if (!video.paused) {
      halt();
      return;
    }
    const playback = ++generation;
    starting = true;
    status.textContent = tr("Loading narration…", "Carregando narração…");
    syncControls();
    try {
      audio ??= new AudioContext();
      await audio.resume();
      if (playback !== generation) return;
      if (!voice) {
        if (!audioData) loadVoice(plan.voice);
        const trackData = await audioData;
        if (playback !== generation) return;
        if (!trackData) throw new Error();
        const decoded = await audio.decodeAudioData(trackData.slice(0));
        if (playback !== generation) return;
        voice = decoded;
      }
      if (playback !== generation) return;
      if (video.ended || position() >= plan.seconds - 0.01) video.currentTime = plan.trim;
      await video.play();
      if (playback === generation) status.textContent = "";
    } catch {
      if (playback === generation) {
        audioData = null;
        status.textContent = tr("Could not play this scene. Try again.",
          "Não foi possível reproduzir a cena. Tente novamente.");
      }
    } finally {
      if (playback === generation) {
        starting = false;
        syncControls();
      }
    }
  }

  for (const event of ["pause", "waiting", "seeking", "ended"]) video.addEventListener(event, stopVoice);
  for (const event of ["playing", "seeked"]) video.addEventListener(event, syncVoice);
  video.addEventListener("play", () => { syncControls(); cancelAnimationFrame(tick); advance(); });
  video.addEventListener("pause", syncControls);
  video.addEventListener("ended", syncControls);
  video.addEventListener("timeupdate", showPosition);
  video.addEventListener("error", () => {
    halt();
    status.textContent = tr("Could not load the take.", "Não foi possível carregar a tomada.");
  });
  time.addEventListener("input", () => { video.currentTime = plan.trim + Number(time.value); });

  function apply(next) {
    const move = !plan || ["take", "trim", "seconds"].some(field => plan[field] !== next[field]);
    const sound = plan?.voice !== next.voice;
    if (move || sound || plan.narration_offset !== next.narration_offset) halt();
    if (sound) {
      loadVoice(next.voice);
      voice = null;
    }
    if (plan?.take !== next.take) video.src = next.take;
    plan = next;
    if (move) video.currentTime = next.trim;
    if (track) {
      for (const cue of Array.from(track.cues || [])) track.removeCue(cue);
      track.mode = "showing";
      for (const cue of next.cues) {
        const end = Math.min(cue.end, next.seconds);
        if (end > cue.start) track.addCue(new VTTCue(next.trim + cue.start, next.trim + end,
          cue.text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")));
      }
    }
    frame.classList.toggle("portrait", next.aspect === "9:16");
    frame.hidden = controls.hidden = false;
    time.max = next.seconds;
    pending = false;
    status.textContent = "";
    syncControls();
    showPosition();
  }

  return {element, apply, halt,
    playing: () => !video.paused,
    updating() {
      pending = true;
      status.textContent = tr("Updating scene preview…", "Atualizando prévia da cena…");
      syncControls();
    },
    error(message) {
      halt();
      frame.hidden = controls.hidden = true;
      status.textContent = message;
    },
    dispose() {
      halt();
      audioRequest?.abort();
      video.removeAttribute("src");
      video.load();
      audio?.close().catch(() => {});
    },
  };
}
