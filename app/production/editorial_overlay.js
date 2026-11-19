/* HyperFrames drives this paused Anime.js timeline in milliseconds. */
const { createTimeline, svg } = anime;
const cues = createTimeline({ autoplay: false, defaults: { ease: "inOutSine" } });
for (const cue of document.querySelectorAll("[data-editorial-cue]")) {
  const start = Number(cue.dataset.startMs);
  const duration = Number(cue.dataset.durationMs);
  const end = start + duration;
  const enter = Math.min(duration * .4, 640);
  const exit = Math.min(duration * .36, 480);
  cues.add(cue, { opacity: [0, 1], duration: enter }, start);
  cues.add(cue, { opacity: [1, 0], duration: exit }, end - exit);
  if (cue.dataset.editorialCue === "reveal") {
    cues.add(cue, { y: [9, 0], duration: enter }, start);
  }
  if (cue.dataset.editorialCue === "path") {
    const draw = duration * .63;
    cues.add(svg.createDrawable(cue.querySelector(".route")),
      { draw: ["0 0", "0 1"], duration: draw }, start);
    cues.add(cue.querySelector(".end"), { opacity: [0, 1], duration: enter * .65 }, start + draw * .82);
  }
}
(window.__hfAnime ||= []).push(cues);
