/* HyperFrames drives this paused Anime.js timeline in milliseconds. */
const { createTimeline, svg } = anime;
const cues = createTimeline({ autoplay: false, defaults: { ease: "outCubic" } });
for (const cue of document.querySelectorAll("[data-editorial-cue]")) {
  const start = Number(cue.dataset.startMs);
  const duration = Number(cue.dataset.durationMs);
  const end = start + duration;
  const enter = Math.min(duration * .32, 300);
  const exit = Math.min(duration * .24, 250);
  cues.add(cue, { opacity: [0, 1], duration: enter }, start);
  cues.add(cue, { opacity: [1, 0], duration: exit, ease: "inSine" }, end - exit);
  if (cue.dataset.editorialCue === "reveal") {
    cues.add(cue, { y: [18, 0], duration: enter + 100 }, start);
  }
  if (cue.dataset.editorialCue === "path") {
    const draw = duration - exit - enter;
    cues.add(svg.createDrawable(cue.querySelector(".route")),
      { draw: ["0 0", "0 1"], duration: draw, ease: "inOutQuad" }, start);
    cues.add(cue.querySelector(".end"), { opacity: [0, 1], duration: enter }, start + draw);
  }
}
(window.__hfAnime ||= []).push(cues);
