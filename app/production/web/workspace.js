"use strict";

const ui = id => document.getElementById(id);
const entry = new URLSearchParams(location.search);
let locale = localStorage.getItem("mpt-production-language") || "en";
let projectId = entry.get("project") || sessionStorage.getItem("mpt-production-project");
let projects = [], project = null, loggedIn = false, page = entry.get("view") || "dashboard";
let loginMethod = "token";
let providerCurrency = null;
let selected = new Set(), noticeTimer, selectedClip = 0, timelineDraft = null, timelineBase = null;
let timelineRecovery, timelineInitial, timelineStored;
let editing = false, polling = false;
let activeScenePreview, editorPreview, reviewEdit, reviewReturnFocus;
let focusedJobLink;
let experimentVersion = null;
let youtubeVersion = null;

const tr = (en, pt) => locale === "pt" ? pt : en;
const navLabels = {
  catalog: ["Editorial catalog", "Catálogo editorial"],
  dashboard: ["Dashboard", "Painel"], projects: ["Projects", "Projetos"], research: ["Research", "Pesquisa"],
  scripts: ["Scripts", "Roteiros"], storyboard: ["Storyboard", "Storyboard"], scenes: ["Scene generation", "Geração de cenas"],
  narration: ["Narration", "Narração"], timeline: ["Timeline", "Linha do tempo"], assets: ["Asset library", "Biblioteca"],
  thumbnails: ["Thumbnails", "Miniaturas"],
  delivery: ["Delivery review", "Revisão da entrega"],
  analysis: ["Experiments and performance", "Testes e desempenho"],
  channel: ["YouTube channel", "Canal do YouTube"],
  youtube: ["YouTube publishing", "Publicação no YouTube"],
  proposals: ["Edit proposals", "Propostas de edição"],
  costs: ["Cost analytics", "Custos"], providers: ["Providers", "Provedores"], settings: ["Settings", "Configurações"],
};
const productionStages = {
  IDEA: ["research", "Start research", "Iniciar pesquisa"],
  RESEARCH: ["research", "Review sources", "Revisar fontes"],
  SCRIPT: ["scripts", "Write script", "Criar roteiro"],
  SCRIPT_APPROVAL: ["scripts", "Review script", "Revisar roteiro"],
  STORYBOARD: ["storyboard", "Plan scenes", "Planejar cenas"],
  STORYBOARD_APPROVAL: ["storyboard", "Review storyboard", "Revisar storyboard"],
  COST_ESTIMATION: ["scenes", "Prepare footage", "Preparar tomadas"],
  ASSET_GENERATION: ["scenes", "Check footage", "Conferir tomadas"],
  SCENE_REVIEW: ["scenes", "Review footage", "Revisar tomadas"],
  VIDEO_ASSEMBLY: ["timeline", "Open editor", "Abrir editor"],
  FINAL_REVIEW: ["delivery", "Review delivery", "Revisar entrega"],
  EXPORT: ["timeline", "View delivery", "Ver entrega"],
};
const projectNavigation = [
  ["Story", "Pauta e roteiro", ["research", "scripts"]],
  ["Scenes and voice", "Cenas e narração", ["storyboard", "scenes", "narration"]],
  ["Assets and publishing", "Acervo e publicação", ["delivery", "assets", "thumbnails", "youtube", "analysis"]],
  ["Studio settings", "Operação", ["proposals", "costs", "providers", "settings"]],
];
const states = {
  APPROVED: ["Approved", "Aprovado"], REJECTED: ["Rejected", "Rejeitado"], DRAFT: ["Draft", "Rascunho"],
  AWAITING_REVIEW: ["Awaiting review", "Em revisão"], REVISION_REQUESTED: ["Revision requested", "Revisão solicitada"],
  FAILED: ["Failed", "Falhou"], COMPLETED: ["Completed", "Concluído"], GENERATING: ["Generating", "Gerando"],
  CANCELLED: ["Cancelled", "Cancelado"], STALE: ["Inputs changed", "Insumos alterados"],
  IDEA: ["Idea", "Ideia"], RESEARCH: ["Research", "Pesquisa"], SCRIPT: ["Script", "Roteiro"],
  STORYBOARD: ["Storyboard", "Storyboard"], SCENE_REVIEW: ["Scene review", "Revisão de cenas"],
  SCRIPT_APPROVAL: ["Script approval", "Aprovação de roteiro"], STORYBOARD_APPROVAL: ["Storyboard approval", "Aprovação de storyboard"],
  COST_ESTIMATION: ["Cost estimation", "Estimativa de custos"], ASSET_GENERATION: ["Asset generation", "Geração de recursos"],
  VIDEO_ASSEMBLY: ["Assembly", "Montagem"], FINAL_REVIEW: ["Final review", "Revisão final"], EXPORT: ["Export", "Exportação"],
};

function node(tag, attrs = {}, ...children) {
  const element = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (name.startsWith("on")) element.addEventListener(name.slice(2), value);
    else if (name === "class") element.className = value;
    else if (["checked", "disabled", "value"].includes(name)) element[name] = value;
    else element.setAttribute(name, value === true ? "" : String(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child !== undefined && child !== null && child !== false) {
      element.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
  }
  return element;
}
const row = (...children) => node("div", {class: "row"}, ...children);
const stack = (...children) => node("div", {class: "stack"}, ...children);
const card = (...children) => node("section", {class: "card stack"}, ...children);
const grid = (...children) => node("div", {class: "grid"}, ...children);
const title = (text, ...children) => node("div", {class: "row between"}, node("h1", {}, text), ...children);
const small = text => node("small", {}, text);
const label = (text, field) => node("label", {class: field.type === "checkbox" ? "checkbox" : null}, text, field);
const input = (value = "", attrs = {}) => node("input", {value, ...attrs});
const area = (value = "", attrs = {}) => node("textarea", {value, ...attrs});
const select = (options, value) => {
  const element = node("select", {}, options.map(([k, v]) => node("option", {value: k}, v)));
  if (value !== undefined) element.value = value;
  return element;
};
const button = (text, action, primary = false, disabled = false) => node("button",
  {type: "button", class: primary ? "primary" : null, disabled, onclick: e => execute(action, e.currentTarget)}, text);
const badge = status => node("span", {class: "status " + status.toLowerCase()}, states[status] ? tr(...states[status]) : status);
const assetBadge = item => badge(item ? (item.fresh ? item.status : "STALE") : "DRAFT");
const current = slot => project?.versions.find(item => item.current && item.slot === slot);
const ready = item => item?.fresh && item.status === "APPROVED";
const sceneData = id => current("scene/" + id);
const path = (tail = "") => "/api/projects/" + projectId + tail;
const format = (amount, currency = project?.costs.currency || "USD") => new Intl.NumberFormat(locale === "pt" ? "pt-BR" : "en-US",
  {style: "currency", currency}).format(amount / 100);
const parse = element => JSON.parse(element.value);
const json = value => JSON.stringify(value, null, 2);
const codexOption = () => ["codex", tr("Codex · ChatGPT plan", "Codex · assinatura ChatGPT")];
const apiOption = ["openai", "OpenAI API"];
const flowOption = () => ["google_flow", tr("Google Flow · manual import", "Google Flow · importação manual")];

function notify(message, error = false) {
  const feedback = error && ui("dialog").open && ui("dialog-feedback");
  if (feedback) {
    feedback.replaceChildren(node("p", {}, message));
    return;
  }
  ui("notices").replaceChildren(node("p", {}, message));
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => ui("notices").replaceChildren(), 6500);
}

async function execute(action, control) {
  if (control) control.disabled = true;
  try {
    await action();
  } catch (error) {
    notify(error.message || tr("Action failed", "A ação falhou"), true);
  } finally {
    if (control) control.disabled = false;
  }
}

async function api(url, method = "GET", data, headers = {}) {
  const raw = data instanceof File;
  const options = {method, credentials: "same-origin", headers};
  if (data !== undefined) {
    options.headers = {...headers, "Content-Type": raw ? "application/octet-stream" : "application/json"};
    options.body = raw ? data : JSON.stringify(data);
  }
  const response = await fetch(url, options);
  if (response.status === 401 && url !== "/api/session") {
    loggedIn = false;
    render();
  }
  if (!response.ok) {
    let detail;
    try { detail = (await response.json()).detail; } catch { detail = null; }
    const error = new Error(detail || tr("Request failed", "A solicitação falhou"));
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function modal(heading, content, controls = []) {
  const dialog = ui("dialog");
  const feedback = node("div", {id: "dialog-feedback", class: "dialog-feedback", role: "alert"});
  ui("dialog-content").replaceChildren(stack(node("h2", {id: "dialog-title"}, heading), content,
    node("div", {}, feedback, row(...controls, button(tr("Close", "Fechar"), () => dialog.close())))));
  dialog.setAttribute("aria-labelledby", "dialog-title");
  if (!dialog.open) dialog.showModal();
}

async function refresh(redraw = true) {
  const target = projectId;
  const list = await api("/api/projects");
  const snapshot = list.some(item => item.id === target) ? await api("/api/projects/" + target) : null;
  if (projectId !== target) return;
  projects = list;
  project = snapshot;
  if (redraw) render();
}

async function changeProject(id) {
  projectId = id;
  sessionStorage.setItem("mpt-production-project", id);
  selected = new Set();
  timelineDraft = null;
  timelineRecovery = null;
  reviewEdit = null;
  reviewReturnFocus = null;
  experimentVersion = null;
  youtubeVersion = null;
  selectedClip = 0;
  page = "dashboard";
  await refresh();
}

async function mutate(url, method, data) {
  const result = await api(url, method, data);
  await refresh();
  return result;
}

function review(items, status) {
  const comment = area("", {maxlength: 2000});
  modal(tr(...states[status]), stack(node("p", {}, tr(
    "Record this decision for " + items.length + " version(s).",
    "Registrar esta decisão para " + items.length + " versão(ões).")),
    label(tr("Optional review comment", "Comentário de revisão opcional"), comment)), [
    button(tr("Confirm decision", "Confirmar decisão"), async () => {
      await api(path("/reviews"), "POST", {versions: items.map(item => item.id), status, comment: comment.value});
      selected.clear();
      ui("dialog").close();
      await refresh();
      ui("workspace").focus();
      notify(tr("Review decision recorded", "Decisão registrada"));
    }, true),
  ]);
}

function reviewControls(item) {
  if (!item?.current || !item.fresh || item.status === "APPROVED") return null;
  return row(
    button(tr("Approve", "Aprovar"), () => review([item], "APPROVED"), true),
    button(tr("Reject", "Rejeitar"), () => review([item], "REJECTED")),
    button(tr("Request revision", "Solicitar revisão"), () => review([item], "REVISION_REQUESTED")));
}

async function quote(jobs) {
  if (!jobs.length) return;
  const target = projectId;
  const quotePath = path("/quotes");
  const result = await api(quotePath, "POST", {jobs});
  if (projectId !== target) return;
  const maximum = input((result.total / 100).toFixed(2), {type: "number", min: 0, step: ".01"});
  const consent = input("", {type: "checkbox"});
  const usesCodex = result.items.some(item => item.billing === "subscription");
  modal(tr("Authorize generation", "Autorizar geração"), stack(
    node("p", {}, (usesCodex ? tr("Estimated direct API spend ", "Gasto direto estimado com API ") :
      tr("Estimated cost ", "Custo estimado ")) + format(result.total, result.currency) + " · " +
      tr("Remaining budget ", "Orçamento restante ") + format(result.budget.remaining, result.currency)),
    node("p", {}, `${result.scene_count} ${tr("scenes", "cenas")} · ${result.attempts} ${tr("authorized attempts", "tentativas autorizadas")}`),
    node("ul", {}, result.items.map(item => node("li", {}, `${item.slot.split("/")[0]} · ${item.provider} · ${item.model} · ${item.billing === "subscription" ? tr("ChatGPT plan usage", "Uso da assinatura ChatGPT") : format(item.estimate)}`,
      item.characters ? " · " + item.characters + " " + tr("characters", "caracteres") : ""))),
    usesCodex && node("p", {}, tr(
      "Codex uses your plan allowance or existing ChatGPT credits. Check that automatic reload is off in ChatGPT Usage to avoid buying more.",
      "O Codex usa a franquia ou o saldo de créditos do ChatGPT. Confira se a recarga automática está desativada em Uso no ChatGPT para evitar novas compras.")),
    label(tr("Maximum authorized spend", "Gasto máximo autorizado"), maximum),
    label(tr("I authorize these jobs at this maximum", "Autorizo estes trabalhos até esse limite"), consent)), [
    button(tr("Authorize and queue", "Autorizar e enfileirar"), async () => {
      if (!consent.checked || !maximum.value || !maximum.checkValidity()) {
        throw new Error(tr("Confirm the spending maximum", "Confirme o limite de gasto"));
      }
      await mutate(quotePath + "/" + result.id + "/authorize", "POST",
        {maximum: Math.round(Number(maximum.value) * 100), confirmed: true});
      ui("dialog").close();
      notify(tr("Jobs queued", "Trabalhos enfileirados"));
    }, true),
  ]);
}

function mediaPreview(item, preload = "metadata") {
  if (!item?.url) return null;
  if (item.kind === "captions") return null;
  if (["image", "reference", "thumbnail", "poster"].includes(item.kind)) return node("img", {
    src: item.url, alt: item.data.title || item.data.headline || tr("Image preview", "Prévia da imagem"), loading: "lazy"});
  const tag = ["voice", "audio"].includes(item.kind) ? "audio" : "video";
  return node(tag, {src: item.url, controls: true, preload, playsinline: true});
}

function assemblyPlayer(assembly) {
  const player = mediaPreview(assembly);
  if (!player) return null;
  const versions = new Map(project.versions.map(item => [item.id, item]));
  const take = versions.get(assembly.data.retention?.[0]?.video);
  const poster = versions.get(assembly.data.poster) || take?.dependencies
    .map(dep => versions.get(dep.id)).find(item => item?.kind === "image");
  if (poster?.url) player.poster = poster.url;
  player.setAttribute("aria-label", tr("Rendered assembly", "Montagem renderizada"));
  for (const [language, id] of Object.entries(assembly.data.captions || {})) {
    player.append(node("track", {kind: "subtitles", srclang: language, label: language,
      src: path("/media/" + id + "?webvtt=true")}));
  }
  return player;
}

function seekAssembly(player, seconds) {
  const seek = () => { player.currentTime = Math.max(0, Math.min(seconds, player.duration)); };
  if (player.readyState) seek();
  else player.addEventListener("loadedmetadata", seek, {once: true});
}

function assemblyScenePicker(assembly, player) {
  const chapters = (assembly.data.retention || []).filter(chapter =>
    chapter.scene && Number.isFinite(chapter.start) && chapter.start >= 0 && chapter.start < assembly.data.seconds);
  if (!chapters.length) return null;
  const picker = select([["", tr("Choose a scene", "Escolha uma cena")], ...chapters.map((chapter, index) =>
    [String(index), formatDuration(chapter.start) + " · " + chapter.scene])], "");
  picker.addEventListener("change", () => {
    if (picker.value === "") return;
    seekAssembly(player, chapters[Number(picker.value)].start);
  });
  const field = label(tr("Jump to scene", "Ir para a cena"), picker);
  field.classList.add("review-scene-picker");
  return field;
}

function revisions(slot) {
  const versions = project.versions.filter(item => item.slot === slot);
  const reviews = project.reviews;
  const details = node("details", {}, node("summary", {}, tr("Versions and comparison", "Versões e comparação") +
    " (" + versions.length + ")"));
  function reveal() {
    if (!details.open) return;
    details.removeEventListener("toggle", reveal);
    details.append(grid(...versions.map(item => card(row(node("strong", {}, "v" + item.seq), assetBadge(item)),
      mediaPreview(item, "none"), node("details", {}, node("summary", {}, tr("Parameters and reviews", "Parâmetros e revisões")),
        node("pre", {}, json(item.data)), ...reviews.filter(r => r.version === item.id).map(r =>
          node("p", {}, badge(r.decision), " ", r.comment, " · ", r.actor, " · ", new Date(r.created * 1000).toLocaleString()))),
      item.url && node("a", {href: item.url + "?attachment=true"}, tr("Download version", "Baixar versão")),
      item.kind === "assembly" && item.url && node("a", {
        href: "/?project=" + item.project + "&view=delivery&assembly=" + item.id,
      }, tr("Open this assembly’s review", "Abrir a revisão desta montagem"))))));
  }
  details.addEventListener("toggle", reveal);
  return details;
}

function importAsset(kind, scene, language, {heading, instructions, title = "", source = {}} = {}) {
  const types = {image: "image/png,image/jpeg,image/webp", video: "video/mp4,video/quicktime", voice: "audio/wav,audio/mpeg,audio/flac"};
  const file = input("", {type: "file", accept: types[kind]});
  const name = input(title, {maxlength: 200});
  const uploadUrl = path("/uploads?");
  modal(heading || tr("Import prepared media", "Importar mídia preparada"), stack(instructions, label(tr("Media title", "Título da mídia"), name),
    label(tr("Media file", "Arquivo de mídia"), file)), [button(tr("Upload for review", "Enviar para revisão"), async () => {
    if (!file.files[0] || !name.value.trim()) throw new Error(tr("Choose a file and enter its title", "Selecione o arquivo e informe o título"));
    const params = new URLSearchParams({kind, scene, title: name.value, ...source});
    if (language) params.set("language", language);
    await mutate(uploadUrl + params.toString(), "POST", file.files[0]);
    ui("dialog").close();
  }, true)]);
}

function importFlowClip(sid, scene, image) {
  const {video_prompt, negative_prompt, title, seconds} = scene.data;
  const prompt = area(negative_prompt ? `${video_prompt}\n\nAvoid: ${negative_prompt}` : video_prompt,
    {readonly: true, rows: 5});
  importAsset("video", sid, undefined, {
    heading: "Google Flow · " + title,
    title,
    source: {source_provider: "google_flow", scene_version: scene.id, image_version: image.id},
    instructions: stack(
      label(tr("Scene video prompt", "Prompt de vídeo da cena"), prompt),
      row(button(tr("Copy prompt", "Copiar prompt"), async () => {
        await navigator.clipboard.writeText(prompt.value);
        notify(tr("Prompt copied", "Prompt copiado"));
      }), node("a", {href: image.url + "?attachment=true"}, tr("Download approved frame", "Baixar quadro aprovado")),
      node("a", {href: "https://flow.google.com/", target: "_blank", rel: "noopener noreferrer"}, tr("Open Google Flow", "Abrir Google Flow"))),
      small(tr("Planned format", "Formato previsto") + ` · ${project.data.style.aspect} · ${seconds} s`),
      node("p", {}, tr(
        "In Flow, use your Google account, paste the prompt, and add the frame as a reference. Check the credit cost there before generating. Download the MP4 and select it below. Scene clips are imported without audio.",
        "No Flow, use sua conta Google, cole o prompt e adicione o quadro como referência. Confira o custo em créditos antes de gerar. Baixe o MP4 e selecione-o abaixo. O clipe da cena é importado sem áudio.")),
    ),
  });
}

function loginView() {
  const usePassword = loginMethod === "password";
  const username = input("", {required: true, name: "username", autocomplete: "username", maxlength: 80});
  const secret = input("", {
    required: true, name: usePassword ? "password" : "token", type: "password",
    autocomplete: "current-password", minlength: usePassword ? 1 : 32, maxlength: 256,
  });
  return node("div", {class: "login"}, card(node("h1", {}, tr("Production workspace", "Estúdio de produção")),
    usePassword ? null : node("p", {class: "muted"}, tr("Sign in using the access token configured for this workstation.",
      "Entre com o token de acesso configurado nesta estação.")),
    node("form", {onsubmit: event => {
      event.preventDefault();
      execute(async () => {
        const credentials = usePassword ? {username: username.value, password: secret.value} : {token: secret.value};
        await api("/api/session", "POST", credentials);
        secret.value = "";
        loggedIn = true;
        await refresh();
      }, event.submitter);
    }}, usePassword ? label(tr("Username", "Usuário"), username) : null,
    label(usePassword ? tr("Password", "Senha") : tr("Access token", "Token de acesso"), secret),
    node("button", {class: "primary", type: "submit"}, tr("Sign in", "Entrar")))));
}

function evidenceBrief(evidence, includeSources = false) {
  return stack(
    node("p", {}, node("strong", {}, evidence.question)),
    node("p", {}, evidence.finding),
    small(evidence.angle),
    small(tr("Evidence limit · ", "Limite da evidência · ") + evidence.uncertainty),
    includeSources && row(...evidence.sources.map(source => node("a", {
      href: source.url, target: "_blank", rel: "noopener noreferrer",
    }, source.title))));
}

async function catalogView() {
  const {catalog, projects: linked} = await api("/api/catalog");
  const importFiles = () => {
    const files = [
      ["catalog", tr("Episode catalog", "Catálogo de pautas"), ".json"],
      ["prompts", tr("Production prompts", "Prompts de produção"), ".txt"],
      ["plan", tr("Editorial plan", "Plano editorial"), ".md"],
    ].map(([key, text, accept]) => [key, text, input("", {type: "file", accept})]);
    modal(tr("Import editorial files", "Importar arquivos editoriais"),
      stack(...files.map(([, text, file]) => label(text, file))), [
        button(tr("Import", "Importar"), async () => {
          if (files.some(([, , file]) => !file.files.length)) {
            throw new Error(tr("Choose all three files.", "Selecione os três arquivos."));
          }
          const documents = Object.fromEntries(await Promise.all(
            files.map(async ([key, , file]) => [key, await file.files[0].text()])));
          await api("/api/catalog", "PUT", documents);
          ui("dialog").close();
          render();
        }, true),
      ]);
  };
  const heading = title(tr("Editorial catalog", "Catálogo editorial"),
    button(tr("Import files", "Importar arquivos"), importFiles));
  if (!catalog) return stack(heading);
  const episodes = new Map(catalog.episodes.map(item => [item.id, item]));
  const featured = new Set(catalog.featured || []);
  const episodeDetails = id => {
    const item = episodes.get(id);
    return node("details", {}, node("summary", {}, id + " · " + item.title),
      stack(small(item.series + " · " + item.aspect_ratio + " · " + formatDuration(item.target_duration_seconds)),
        node("p", {}, item.opening_hook), node("p", {}, item.premise),
        item.evidence && evidenceBrief(item.evidence, true),
        node("ol", {}, item.story_beats.map(beat => node("li", {}, beat))),
        node("p", {}, item.visual_plan), node("p", {}, item.fact_check),
        node("p", {}, tr("Thumbnail", "Miniatura") + " · " + item.thumbnail_text),
        !item.evidence && node("a", {href: item.research_start.url, target: "_blank", rel: "noopener noreferrer"}, item.research_start.label),
        row(button(tr("Draft prompt", "Prompt de rascunho"), () => {
          modal(tr("Draft prompt", "Prompt de rascunho"), area(item.draft_prompt, {readonly: true, rows: 14}));
        }), button(linked[id] ? tr("Open project", "Abrir projeto") : tr("Create project", "Criar projeto"),
          () => linked[id] ? changeProject(linked[id]) : createEditorialProject(item), true))));
  };
  const rounds = catalog.publishing_rounds.flatMap((group, index) => {
    const previous = group.filter(id => !featured.has(id));
    return previous.length ? [card(node("h2", {}, tr("Round", "Rodada") + " " + (index + 1)),
      ...previous.map(episodeDetails))] : [];
  });
  return stack(heading, card(node("h2", {}, catalog.channel), node("p", {}, catalog.slogan),
    row(...[["catalog", tr("Catalog JSON", "Catálogo JSON")], ["prompts", "Prompts"],
      ["plan", tr("Editorial plan", "Plano editorial")]].map(([name, text]) =>
      node("a", {href: "/api/catalog/files/" + name, download: true}, text)))),
    featured.size > 0 && card(node("h2", {}, tr("Evidence-backed priorities", "Pautas priorizadas com fontes")),
      ...[...featured].map(episodeDetails)),
    ...(featured.size && rounds.length ? [node("details", {}, node("summary", {},
      tr("Previous editorial catalog", "Catálogo editorial anterior")), stack(...rounds))] : rounds));
}

function formatDuration(seconds) {
  const tenths = Math.round(seconds * 10);
  const rest = String(Math.floor(tenths / 10) % 60).padStart(2, "0");
  return Math.floor(tenths / 600) + ":" + rest +
    (tenths % 10 ? tr(".", ",") + String(tenths % 10) : "");
}

function createEditorialProject(episode) {
  const localization = input("", {type: "checkbox"});
  modal(episode.title, label(tr("Include optional Brazilian Portuguese track", "Incluir faixa opcional em português do Brasil"), localization), [
    button(tr("Create draft project", "Criar projeto em rascunho"), async () => {
      const {id} = await api("/api/catalog/episodes/" + episode.id + "/project", "POST", {localize: localization.checked});
      ui("dialog").close();
      await changeProject(id);
    }, true),
  ]);
}

function generalProjectForm() {
  const name = input("", {required: true});
  const topic = area("", {required: true});
  const audience = input("", {required: true});
  const tone = input(tr("Documentary", "Documentário"));
  const duration = input("45", {type: "number", min: 2, max: 7200, required: true});
  const structure = area(tr("Opening hook\nScenario setup\nInitial consequences\nEscalation\nUnexpected developments\nLong-term consequences\nConclusion",
    "Abertura\nContexto\nPrimeiras consequências\nEscalada\nReviravoltas\nConsequências de longo prazo\nConclusão"));
  const langs = select([["en-US,pt-BR", "English → Português"], ["pt-BR,en-US", "Português → English"], ["en-US", "English"], ["pt-BR", "Português"]]);
  const currency = select([["USD", "USD"], ["BRL", "BRL"]], locale === "pt" ? "BRL" : "USD");
  return node("form", {
      onsubmit: event => {
        event.preventDefault();
        execute(async () => {
          const result = await api("/api/projects", "POST", {title: name.value, topic: topic.value, audience: audience.value,
            tone: tone.value, duration: Number(duration.value), structure: structure.value.split("\n").map(v => v.trim()).filter(Boolean),
            languages: langs.value.split(","), currency: currency.value});
          await changeProject(result.id);
        }, event.submitter);
      },
    }, label(tr("Project title", "Título do projeto"), name), label(tr("Topic", "Tema"), topic),
    label(tr("Target audience", "Público-alvo"), audience), label(tr("Tone", "Tom"), tone),
    label(tr("Target duration (seconds)", "Duração desejada (segundos)"), duration),
    label(tr("Narrative structure · one section per line", "Estrutura narrativa · uma seção por linha"), structure),
    label(tr("Languages · original first", "Idiomas · original primeiro"), langs), label(tr("Currency", "Moeda"), currency),
    node("button", {class: "primary", type: "submit"}, tr("Create project", "Criar projeto")));
}

function hyphotenicProjectForm(profile) {
  const series = select([["", tr("Choose a series", "Escolha a série")],
    ...Object.entries(profile.series).map(([key, value]) => [key, value.name])]);
  series.required = true;
  const publicTitle = input("", {required: true, maxlength: 187});
  const topic = area("", {required: true, maxlength: 8000});
  const changed = area("", {rows: 2, maxlength: 8000});
  const unchanged = area("", {rows: 2, maxlength: 8000});
  const scope = area("", {rows: 2, maxlength: 8000});
  const audience = input(profile.base.audience, {required: true, maxlength: 200});
  const tone = input(profile.base.tone, {required: true, maxlength: 200});
  const duration = input("", {type: "number", min: 2, max: 7200, required: true});
  const structure = area("", {required: true, rows: 8});
  const languages = select([["en-US,pt-BR", "English → Português"], ["en-US", "English"]],
    profile.base.languages.join(","));
  const aspect = select([["16:9", "16:9"], ["9:16", "9:16"]], profile.base.style.aspect);
  const direction = area(profile.base.style.art_direction, {rows: 3, maxlength: 500});
  const palette = area(profile.base.style.palette, {rows: 3, maxlength: 500});
  const restrictions = area(profile.base.style.restrictions, {rows: 3, maxlength: 1000});
  const edited = new Set();
  for (const field of [duration, structure, aspect]) {
    field.addEventListener("input", () => edited.add(field));
  }
  series.addEventListener("change", () => {
    const selected = profile.series[series.value];
    if (!edited.has(duration)) duration.value = selected?.duration ?? "";
    if (!edited.has(structure)) structure.value = selected?.structure.join("\n") ?? "";
    if (!edited.has(aspect)) aspect.value = selected?.style?.aspect || profile.base.style.aspect;
  });

  const sourceFields = stack();
  const sources = new Map();
  const addSource = button(tr("Add a source", "Adicionar uma fonte"), () => {
    if (sources.size === 30) return;
    const name = input("", {required: true, maxlength: 200});
    const url = input("", {required: true, type: "url", maxlength: 2000});
    const excerpt = area("", {required: true, rows: 2, maxlength: 8000});
    const remove = button(tr("Remove source", "Remover fonte"), () => {
      sources.delete(group);
      group.remove();
    });
    const group = stack(label(tr("Source title", "Título da fonte"), name),
      label(tr("Reference URL", "URL da fonte"), url),
      label(tr("Excerpt, date and scope", "Trecho, data e escopo"), excerpt), remove);
    sources.set(group, {name, url, excerpt});
    sourceFields.append(group);
    name.focus();
  });
  return node("form", {onsubmit: event => {
    event.preventDefault();
    execute(async () => {
      const {id} = await api("/api/projects", "POST", {
        channel: "hyphotenic", series: series.value, public_title: publicTitle.value, topic: topic.value,
        changed_condition: changed.value.trim() || null, unchanged_conditions: unchanged.value.trim() || null,
        scope: scope.value.trim() || null,
        sources: Array.from(sources.values(), ({name, url, excerpt}) =>
          ({title: name.value, url: url.value, excerpt: excerpt.value})),
        audience: audience.value, tone: tone.value, duration: duration.valueAsNumber,
        structure: structure.value.split("\n").map(line => line.trim()).filter(Boolean),
        languages: languages.value.split(","),
        style: {aspect: aspect.value, art_direction: direction.value, palette: palette.value, restrictions: restrictions.value},
      });
      await changeProject(id);
    }, event.submitter);
  }},
  label(tr("Series or format", "Série ou formato"), series),
  label(tr("Public episode title", "Título público do episódio"), publicTitle),
  small(tr("Internal title", "Título interno") + " · " + profile.title_prefix.trimEnd() + " […]"),
  label(tr("Central question", "Pergunta central"), topic),
  label(tr("Changed condition", "Condição alterada"), changed),
  label(tr("Conditions that stay the same", "Condições que permanecem"), unchanged),
  label(tr("Scope", "Escopo"), scope),
  node("details", {}, node("summary", {}, tr("Available sources", "Fontes disponíveis")), stack(sourceFields, addSource)),
  label(tr("Target duration (seconds)", "Duração desejada (segundos)"), duration),
  label(tr("Narrative structure · one section per line", "Estrutura narrativa · uma seção por linha"), structure),
  node("details", {}, node("summary", {}, tr("Audience, language and visuals", "Público, idioma e direção visual")),
    stack(label(tr("Target audience", "Público-alvo"), audience), label(tr("Tone", "Tom"), tone),
      label(tr("Languages · original first", "Idiomas · original primeiro"), languages),
      label(tr("Aspect ratio", "Proporção"), aspect), label(tr("Visual direction", "Direção visual"), direction),
      label(tr("Palette", "Paleta"), palette), label(tr("Visual restrictions", "Restrições visuais"), restrictions),
      small(tr("Planning currency", "Moeda de planejamento") + " · " + profile.base.currency))),
  node("button", {class: "primary", type: "submit"}, tr("Create draft project", "Criar projeto em rascunho")));
}

async function projectsView() {
  const profile = await api("/api/project-profiles/hyphotenic");
  const channel = select([["", tr("General project", "Projeto geral")], ["hyphotenic", profile.channel]],
    project?.data.profile?.channel === profile.channel ? "hyphotenic" : "");
  const general = generalProjectForm(), branded = hyphotenicProjectForm(profile);
  const form = stack();
  const showForm = () => form.replaceChildren(channel.value === "hyphotenic" ? branded : general);
  channel.addEventListener("change", showForm);
  showForm();
  return stack(title(tr("Projects", "Projetos")),
    grid(card(node("h2", {}, tr("New project", "Novo projeto")),
      label(tr("Project template", "Modelo de projeto"), channel), form),
    stack(...projects.map(item => card(row(node("h2", {}, item.data.title), badge(item.stage)),
      node("p", {}, item.data.topic), small(item.data.languages.join(" · ")),
      button(tr("Open project", "Abrir projeto"), () => changeProject(item.id)))))));
}

function profileOverview(data) {
  const profile = data.profile;
  const brief = profile.brief;
  const list = (items, ordered = false) =>
    node(ordered ? "ol" : "ul", {}, items.map(item => node("li", {}, item)));
  const group = (heading, items, ordered) =>
    node("details", {}, node("summary", {}, heading), list(items, ordered));
  return card(node("h2", {}, profile.channel + " · " + profile.series_name),
    small(profile.signature + " " + profile.territory),
    node("dl", {}, [
      [tr("Public title", "Título público"), profile.public_title],
      [tr("Changed condition", "Condição alterada"), brief.changed_condition],
      [tr("Conditions that stay the same", "Condições que permanecem"), brief.unchanged_conditions],
      [tr("Scope", "Escopo"), brief.scope],
    ].filter(([, value]) => value).flatMap(([key, value]) => [node("dt", {}, key), node("dd", {}, value)])),
    group(tr("Deliverables", "Entregas"), profile.deliverables),
    group(tr("Approval sequence", "Sequência de aprovações"), profile.approvals, true),
    group(tr("Manual review and publishing", "Revisão e publicação manuais"), profile.manual_controls),
    node("a", {href: "/api/project-profiles/hyphotenic/logo", download: "HYPHOTENIC-Y.png"},
      tr("Download approved Y logo", "Baixar o logo Y aprovado")));
}

function roughCutAction({inEditor = false} = {}) {
  const script = current("script");
  if (!ready(script) || project.scenes.length || current("assembly") || current("timeline")) return null;
  const job = project.jobs.find(item => item.operation === "rough_cut");
  const active = job && ["DRAFT", "GENERATING"].includes(job.status);
  const language = project.data.languages[0];
  return card(node("h2", {}, tr("First editable cut", "Primeiro rascunho editável")),
    active ? node("progress", {max: 100, value: job.progress,
      "aria-label": tr("Draft progress", "Progresso do rascunho")}) :
      node("p", {}, tr(
        `The approved script becomes a 720p draft with visual guides, system narration and captions in ${language}. The render runs on this server at no provider cost.`,
        `O roteiro aprovado vira um rascunho em 720p com guias visuais, voz do sistema e legendas em ${language}. A montagem roda no servidor sem custo de provedor.`)),
    active && small(tr("You can close this page and return to the same render.",
      "Você pode fechar esta página e voltar à mesma montagem.")),
    job?.status === "FAILED" && node("p", {role: "status"}, job.last_error),
    active ? (!inEditor && button(tr("Follow draft", "Acompanhar rascunho"), () => navigate("timeline"), true)) :
      button(tr("Generate local draft", "Gerar rascunho local"), async () => {
        const target = projectId;
        const quotePath = path("/quotes");
        const estimate = await api(quotePath, "POST",
          {jobs: [{operation: "rough_cut", provider: "local", version: script.id}]});
        if (projectId !== target) return;
        if (estimate.total !== 0 || estimate.items.length !== 1 || estimate.items[0].billing !== "local") {
          throw new Error(tr("The draft could not be quoted as local work.",
            "Não foi possível cotar o rascunho como uma tarefa local."));
        }
        try {
          await api(quotePath + "/" + estimate.id + "/authorize", "POST", {maximum: 0, confirmed: true});
        } finally {
          if (projectId === target) await refresh(false);
        }
        if (projectId === target) navigate("timeline");
      }, true));
}

function dashboard() {
  const d = project.data, c = project.costs;
  const values = [
    [tr("Approved scenes", "Cenas aprovadas"), project.approved], [tr("Pending scenes", "Cenas pendentes"), project.pending],
    [tr("Rejected scenes", "Cenas rejeitadas"), project.rejected], [tr("Failed jobs", "Falhas"), project.failed],
    [tr("Estimated spend", "Gasto estimado"), format(c.estimated)], [tr("Actual spend", "Gasto efetivo"), format(c.actual)],
    [tr("Remaining budget", "Orçamento restante"), format(c.remaining)], [tr("Export status", "Estado da exportação"), badge(project.export_status)],
  ];
  const [next, en, pt] = productionStages[project.stage] || productionStages.IDEA;
  const rough = roughCutAction();
  const needsVoice = project.stage === "VIDEO_ASSEMBLY" && project.scenes.some(scene =>
    project.data.languages.some(language => !ready(current("voice/" + scene + "/" + language))));
  return stack(title(d.title, badge(project.stage)),
    node("p", {}, d.topic), small([d.audience, d.languages.join(", "), d.duration + " s"].join(" · ")),
    d.profile && profileOverview(d),
    rough || row(button(needsVoice ? tr("Prepare narration", "Preparar narração") : tr(en, pt),
      () => navigate(needsVoice ? "narration" : next), true)),
    d.editorial && card(node("h2", {}, d.editorial.id + " · " + d.editorial.series),
      node("p", {}, d.editorial.opening_hook), node("p", {}, d.editorial.visual_plan),
      d.editorial.evidence && evidenceBrief(d.editorial.evidence),
      node("p", {}, d.editorial.fact_check),
      node("a", {href: d.editorial.research_start.url, target: "_blank", rel: "noopener noreferrer"},
        d.editorial.research_start.label)),
    node("details", {}, node("summary", {}, tr("Progress and costs", "Progresso e custos")),
      node("progress", {max: 100, value: project.progress, "aria-label": tr("Production progress", "Progresso da produção")}),
      node("div", {class: "grid stats"}, values.map(([key, value]) => card(small(key), node("div", {class: "metric"}, value))))),
    jobHistory());
}

function claimCategory(value, changed) {
  const field = select([["verified", tr("Verified", "Verificado")], ["assumption", tr("Assumption", "Premissa")],
    ["speculation", tr("Speculation", "Especulação")], ["uncertain", tr("Uncertain", "Incerto")]], value);
  field.addEventListener("change", () => changed(field.value));
  return label(tr("Claim category", "Categoria"), field);
}

function citationPicker(sources, selected, changed) {
  if (!sources.length) return small(tr("Add sources in Research.", "Cadastre fontes em Pesquisa."));
  const chosen = new Set(selected);
  const summary = node("summary");
  const details = node("details", {}, summary);
  const updateCount = () => {
    summary.textContent = tr("Linked sources", "Fontes vinculadas") + " (" + chosen.size + ")";
  };
  function open() {
    if (!details.open) return;
    details.removeEventListener("toggle", open);
    const checks = sources.map((source, i) => input("", {type: "checkbox", checked: chosen.has(i + 1)}));
    const updateLimits = () => checks.forEach(field => { field.disabled = !field.checked && chosen.size >= 20; });
    const choices = checks.map((field, i) => {
      const number = i + 1;
      field.addEventListener("change", () => {
        if (field.checked) chosen.add(number);
        else chosen.delete(number);
        updateCount();
        updateLimits();
        changed([...chosen]);
      });
      return node("label", {class: "checkbox"}, field, node("span", {}, number + " · " + sources[i].title));
    });
    updateLimits();
    details.append(node("div", {class: "stack citation-options"}, choices));
  }
  updateCount();
  details.addEventListener("toggle", open);
  return details;
}

function researchView() {
  const item = current("research");
  const initial = {sources: item?.data.sources || [], claims: item?.data.claims || []};
  const {sources, claims} = structuredClone(initial);
  const provider = select([["local", tr("Local worksheet", "Ficha local")], codexOption(), apiOption],
    project.data.defaults?.research || "local");
  const checklist = project.data.profile?.research_checklist;
  const sourceList = stack(), claimList = stack();
  const reviewBox = stack(reviewControls(item));
  const state = node("small", {role: "status"});
  const updateStatus = () => {
    const dirty = json(initial) !== json({sources, claims});
    state.textContent = dirty ? tr("Research has unsaved changes.", "A pesquisa tem alterações não salvas.") : "";
    reviewBox.hidden = dirty;
    if (dirty) editing = true;
  };
  const save = button(tr("Save research revision", "Salvar revisão da pesquisa"), async () => {
    for (const claim of claims) {
      if (!claim.text.trim()) throw new Error(tr("Enter the text of each claim.", "Informe o texto de cada afirmação."));
      if (claim.category === "verified" && !claim.sources.length) {
        throw new Error(tr("Link a source to each verified claim.", "Vincule uma fonte a cada afirmação verificada."));
      }
    }
    await mutate(path("/research"), "POST", {sources, claims});
    notify(tr("Research revision saved.", "Revisão da pesquisa salva."));
  }, true);

  function sourceForm(index) {
    if (index === sources.length && sources.length >= 30) {
      throw new Error(tr("A research revision supports up to 30 sources.", "Uma revisão de pesquisa aceita até 30 fontes."));
    }
    const existing = sources[index];
    const name = input(existing?.title || "", {maxlength: 200, required: true});
    const url = input(existing?.url || "", {type: "url", maxlength: 2000, required: true});
    const excerpt = area(existing?.excerpt || "", {rows: 4, maxlength: 8000, required: true});
    const accept = button(tr("Keep source", "Guardar fonte"), () => {
      let reference;
      try { reference = new URL(url.value); } catch { /* The field is checked below. */ }
      if (!name.value.trim() || !excerpt.value.trim() || !url.checkValidity() || !reference ||
          !["https:", "http:"].includes(reference.protocol) || reference.username || reference.password) {
        throw new Error(tr("Enter a title, public HTTP(S) URL and excerpt.", "Informe título, URL pública HTTP(S) e trecho."));
      }
      sources[index] = {title: name.value.trim(), url: url.value.trim(), excerpt: excerpt.value.trim()};
      ui("dialog").close();
      redraw();
      sourceList.children[index]?.querySelector("button")?.focus();
    }, true);
    modal(existing ? tr("Edit source", "Editar fonte") : tr("Add source", "Adicionar fonte"),
      stack(label(tr("Source title", "Título da fonte"), name), label(tr("Reference URL", "URL da fonte"), url),
        label(tr("Verified excerpt or notes", "Trecho verificado ou anotações"), excerpt)), [accept]);
  }

  function redraw() {
    sourceList.replaceChildren(...sources.map((source, i) => {
      const number = i + 1;
      return card(node("h3", {}, number + " · " + source.title),
        node("details", {}, node("summary", {}, tr("Excerpt and link", "Trecho e link")),
          node("a", {href: source.url, target: "_blank", rel: "noopener noreferrer"}, tr("Open source", "Abrir fonte")),
          node("p", {class: "review-note-text"}, source.excerpt)),
        row(button(tr("Edit source", "Editar fonte"), () => sourceForm(i)),
          button(tr("Remove source", "Remover fonte"), () => {
            if (claims.some(claim => claim.sources.includes(number))) {
              throw new Error(tr("Unlink this source from its claims before removing it.",
                "Desvincule a fonte das afirmações antes de removê-la."));
            }
            sources.splice(i, 1);
            claims.forEach(claim => {
              claim.sources = claim.sources.map(ref => ref > number ? ref - 1 : ref);
            });
            redraw();
            addSource.focus();
          })));
    }));
    claimList.replaceChildren(...claims.map((claim, i) => card(
      label(tr("Claim text", "Texto da afirmação"), area(claim.text, {rows: 3, maxlength: 8000,
        oninput: e => { claim.text = e.target.value; updateStatus(); }})),
      claimCategory(claim.category, value => { claim.category = value; updateStatus(); }),
      citationPicker(sources, claim.sources, selected => { claim.sources = selected; updateStatus(); }),
      button(tr("Remove claim", "Remover afirmação"), () => {
        claims.splice(i, 1);
        redraw();
        addClaim.focus();
      }))));
    updateStatus();
  }

  const addSource = button(tr("Add source", "Adicionar fonte"), () => sourceForm(sources.length));
  const addClaim = button(tr("Add claim", "Adicionar afirmação"), () => {
    if (claims.length >= 60) {
      throw new Error(tr("A research revision supports up to 60 claims.", "Uma revisão de pesquisa aceita até 60 afirmações."));
    }
    claims.push({text: "", category: "uncertain", sources: []});
    redraw();
    claimList.lastElementChild.querySelector("textarea").focus();
  });
  redraw();
  return stack(title(tr("Research", "Pesquisa"), assetBadge(item)),
    checklist && node("ul", {}, checklist.map(entry => node("li", {}, entry))),
    grid(card(node("h2", {}, tr("Sources", "Fontes")), sourceList, addSource),
      card(node("h2", {}, tr("Research claims", "Afirmações da pesquisa")), claimList, addClaim)),
    row(save, state), reviewBox,
    node("details", {}, node("summary", {}, tr("Generate a research draft", "Gerar rascunho de pesquisa")),
      stack(label(tr("Research provider", "Provedor de pesquisa"), provider),
        button(tr("Estimate research", "Estimar pesquisa"), () =>
          quote([{operation: "research", provider: provider.value, sources}])))),
    revisions("research"));
}

const motionKinds = {
  focus: ["Highlight a detail", "Destacar detalhe"],
  path: ["Trace a connection", "Traçar conexão"],
  reveal: ["Reveal a short label", "Revelar texto curto"],
};

function motionCueEditor(initial, changed = () => {}) {
  const cues = structuredClone(initial || []);
  const fields = stack();
  const add = button(tr("Add motion cue", "Adicionar intervenção"), () => {
    if (cues.length === 4) return;
    cues.push({kind: "focus", start: 0, duration: 1.5, x: .5, y: .43, purpose: "", label: ""});
    changed(cues);
    redraw();
  });
  function redraw() {
    add.disabled = cues.length === 4;
    fields.replaceChildren(...cues.map((cue, index) => {
      const kind = select(Object.entries(motionKinds).map(([value, name]) => [value, tr(...name)]), cue.kind);
      kind.addEventListener("change", () => {
        cue.kind = kind.value;
        if (cue.kind === "path") { cue.x2 ??= .72; cue.y2 ??= .56; }
        changed(cues);
        redraw();
      });
      const number = (key, name, max, min, scale = 1) => label(name, input((cue[key] ?? 0) * scale, {
        type: "number", min, max, step: scale === 1 ? ".05" : "1",
        oninput: e => { cue[key] = e.target.valueAsNumber / scale; changed(cues); },
      }));
      return card(row(label(tr("Effect", "Efeito"), kind),
        number("start", tr("Start in scene (s)", "Início na cena (s)"), 120, 0),
        number("duration", tr("Length (s)", "Duração (s)"), 5, .7)),
        row(number("x", "X (%)", 92, 8, 100), number("y", "Y (%)", 77, 10, 100),
          cue.kind === "path" && number("x2", tr("End X (%)", "Fim X (%)"), 92, 8, 100),
          cue.kind === "path" && number("y2", tr("End Y (%)", "Fim Y (%)"), 77, 10, 100)),
        cue.kind === "reveal" && label(tr("On-screen text", "Texto na tela"), input(cue.label, {
          maxlength: 55, oninput: e => { cue.label = e.target.value; changed(cues); },
        })),
        label(tr("What should the viewer notice?", "O que a pessoa deve perceber?"), input(cue.purpose, {
          maxlength: 160, required: true, oninput: e => { cue.purpose = e.target.value; changed(cues); },
        })), button(tr("Remove cue", "Remover intervenção"), () => {
          cues.splice(index, 1); changed(cues); redraw();
        }));
    }));
  }
  redraw();
  return {element: stack(fields, add), cues};
}

function evidenceGraphicEditor(section) {
  const graphic = section.evidence_graphic ? structuredClone(section.evidence_graphic) : {
    layout: "", labels: ["", ""], annotation: "", reveal_at: Math.min(1, Math.max(0, section.seconds - .1)),
  };
  const types = [["", tr("Visual guide", "Guia visual")], ["sequence", tr("Before and after", "Ordem de chegada")],
    ["alignment", tr("Aligned records", "Registros alinhados")], ["coupling", tr("Moving cause and response", "Causa e resposta em movimento")],
    ["routes", tr("Alternative paths", "Caminhos alternativos")]];
  const layout = select(types, graphic.layout);
  const controls = stack();
  const storeGraphic = () => {
    if (graphic.layout) section.evidence_graphic = graphic;
    else delete section.evidence_graphic;
  };
  function fields() {
    controls.replaceChildren();
    if (!graphic.layout) return;
    controls.append(row(...graphic.labels.map((value, index) => label(
      index ? tr("Second label", "Segundo rótulo") : tr("First label", "Primeiro rótulo"),
      input(value, {maxlength: 48, oninput: e => { graphic.labels[index] = e.target.value; storeGraphic(); }})))),
      row(label(tr("Evidence callout", "Destaque da evidência"), input(graphic.annotation, {maxlength: 64,
        oninput: e => { graphic.annotation = e.target.value; storeGraphic(); }})),
        label(tr("Reveal at (s)", "Revelar em (s)"), input(graphic.reveal_at, {type: "number", min: 0, step: ".1",
          oninput: e => { graphic.reveal_at = Number(e.target.value); storeGraphic(); }}))),
      small(tr("Use the section’s source numbers. The video labels this diagram as illustrative.",
        "Use os números das fontes da seção. O vídeo identifica o esquema como ilustrativo.")));
  }
  layout.addEventListener("change", () => { graphic.layout = layout.value; storeGraphic(); fields(); });
  fields();
  return node("details", {open: Boolean(graphic.layout)}, node("summary", {}, tr("Evidence diagram", "Diagrama da evidência")),
    stack(label(tr("Diagram type", "Tipo de diagrama"), layout), controls));
}

function scriptsView() {
  const target = projectId;
  const endpoint = path("/script");
  let item = current("script"), research = current("research");
  let catalog = research?.data.sources || [];
  const identity = source => JSON.stringify([source.title, source.url, source.excerpt]);
  const fallback = [{key: "hook", title: tr("Opening hook", "Abertura"), seconds: 5,
    narration: project.data.topic, category: "uncertain", sources: []}];
  let sections;
  const sourceReview = new Set();
  const reviewBox = stack(reviewControls(item));
  function rebind(previous) {
    const citationKeys = catalog.map(identity);
    for (const section of sections) {
      section.sources = section.sources.flatMap(number => {
        const prior = previous[number - 1];
        const key = prior && identity(prior);
        const index = key ? (citationKeys[number - 1] === key ? number - 1 : citationKeys.indexOf(key)) : -1;
        if (index >= 0) return [index + 1];
        sourceReview.add(section.key);
        return [];
      });
    }
  }
  function load(version) {
    sourceReview.clear();
    const original = project.versions.find(asset => asset.kind === "research" &&
      version?.dependencies.some(dep => dep.id === asset.id));
    sections = structuredClone(version?.data.sections || fallback);
    rebind(original?.data.sources || []);
  }
  load(item);
  const updateApproval = () => {
    reviewBox.hidden = sourceReview.size > 0 || json(item?.data.sections) !== json(sections);
  };
  const provider = select([["local", tr("Local outline", "Estrutura local")], codexOption(), apiOption], project.data.defaults?.script || "local");
  const instructions = area("", {placeholder: tr("Variation or revision instructions", "Instruções para variação ou revisão")});
  const form = stack();
  const recovery = stack();
  recovery.hidden = true;
  const active = () => form.isConnected && projectId === target && page === "scripts";
  const linkTo = (view, text) => node("a", {
    href: "/?project=" + target + "&view=" + view, target: "_blank", rel: "noopener noreferrer",
  }, text);

  function showRecovery(message, ...controls) {
    const notice = node("p", {role: "status", tabindex: -1}, message);
    recovery.replaceChildren(notice, controls.length ? row(controls) : null);
    recovery.hidden = false;
    editing = true;
    notice.focus();
  }

  function scriptConflict() {
    reviewBox.replaceChildren();
    showRecovery(tr("Another session saved a new script. Your edits are still here.",
      "Outra sessão salvou um novo roteiro. Seus ajustes continuam neste formulário."),
      linkTo("scripts", tr("Open saved script in a new tab", "Abrir roteiro salvo em nova aba")));
  }

  async function updateSources() {
    const latest = await api("/api/projects/" + target);
    if (!active()) return;
    const latestScript = latest.versions.find(version => version.current && version.slot === "script");
    if (latestScript?.id !== item?.id) {
      scriptConflict();
      return;
    }
    const next = latest.versions.find(version => version.current && version.slot === "research");
    if (!ready(next)) {
      showRecovery(tr("Review and approve the current research, then update the sources here.",
        "Revise e aprove a pesquisa atual, depois atualize as fontes aqui."),
        linkTo("research", tr("Open research in a new tab", "Abrir pesquisa em nova aba")), updateSourcesButton);
      return;
    }
    const previous = catalog;
    project = latest;
    research = next;
    catalog = next.data.sources;
    rebind(previous);
    fields();
    showRecovery(sourceReview.size ? tr("Your text was kept. Review the sources in the marked sections.",
      "Seu texto foi mantido. Revise as fontes das seções sinalizadas.") :
      tr("Sources updated. Your text is ready to save.", "Fontes atualizadas. Seu texto está pronto para salvar."));
  }

  const updateSourcesButton = button(tr("Update sources and keep text", "Atualizar fontes e manter texto"), updateSources, true);
  form.addEventListener("input", updateApproval);
  form.addEventListener("change", updateApproval);
  function fields() {
    form.replaceChildren(...sections.map((section, i) => {
      const name = input(section.title, {oninput: e => section.title = e.target.value});
      const words = area(section.narration, {oninput: e => section.narration = e.target.value});
      const seconds = input(section.seconds, {type: "number", min: ".1", max: 120, step: ".1",
        oninput: e => section.seconds = Number(e.target.value)});
      const category = claimCategory(section.category, value => section.category = value);
      const citations = citationPicker(catalog, section.sources, selected => { section.sources = selected; });
      const confirm = sourceReview.has(section.key) && stack(
        node("p", {role: "status"}, tr("Some previous citations changed. Review this section's sources.",
          "Algumas fontes da versão anterior mudaram. Revise os vínculos desta seção.")),
        row(button(tr("Confirm sources for this section", "Confirmar fontes desta seção"), () => {
          if (section.category === "verified" && !section.sources.length) {
            throw new Error(tr("Link a source to this verified section.", "Vincule uma fonte a esta seção verificada."));
          }
          sourceReview.delete(section.key);
          confirm.hidden = true;
          (citations.querySelector("summary") || category.querySelector("select")).focus();
          updateApproval();
        })));
      const visual = input(section.visual_beat || "", {maxlength: 240, oninput: e => {
        if (e.target.value.trim()) section.visual_beat = e.target.value;
        else delete section.visual_beat;
      }});
      const motion = motionCueEditor(section.motion_cues, cues => { section.motion_cues = cues; });
      return card(label(tr("Section title", "Título da seção"), name), label(tr("Narration", "Narração"), words),
        row(label(tr("Seconds", "Segundos"), seconds), category), citations, confirm,
        label(tr("Visual beat", "Mudança visual"), visual),
        evidenceGraphicEditor(section),
        node("details", {open: Boolean(section.motion_cues?.length)},
          node("summary", {}, tr("Editorial motion", "Intervenções de movimento")), motion.element),
        button(tr("Remove section", "Remover seção"), () => {
          sourceReview.delete(section.key);
          sections.splice(i, 1);
          editing = true;
          fields();
        }, false, sections.length === 1));
    }));
    updateApproval();
  }
  fields();
  const permanent = project.data.profile?.script_instructions;
  return stack(title(tr("Scripts", "Roteiros"), assetBadge(item)),
    permanent && node("details", {}, node("summary", {},
      tr("Permanent script instructions", "Instruções permanentes de roteiro")),
      permanent.split("\n\n").map(text => node("p", {}, text))),
    roughCutAction(),
    card(small(tr("Pay off the opening question. Give each still a continuous shot and use motion to reveal a useful detail.",
      "Responda à pergunta da abertura. Dê a cada imagem um plano contínuo e use movimento para revelar um detalhe relevante.")),
      row(label(tr("Provider", "Provedor"), provider), label(tr("Instructions", "Instruções"), instructions)),
      row(button(tr("Estimate new variation", "Estimar nova versão"), () =>
        quote([{operation: "script", provider: provider.value, instructions: instructions.value}])),
      item && button(tr("Estimate AI revision", "Estimar revisão por IA"), () =>
        quote([{operation: "script", provider: provider.value, instructions: instructions.value, version: item.id}])))),
    recovery, form, row(button(tr("Add section", "Adicionar seção"), () => {
      sections.push({...fallback[0], key: crypto.randomUUID()});
      fields();
      editing = true;
      form.lastElementChild.querySelector("input").focus();
    }), button(tr("Save script revision", "Salvar revisão do roteiro"), async () => {
      if (sourceReview.size) throw new Error(tr("Confirm the sources for the marked sections before saving.",
        "Confirme as fontes das seções sinalizadas antes de salvar."));
      if (sections.some(section => section.category === "verified" && !section.sources.length)) {
        throw new Error(tr("Link a source to each verified section.", "Vincule uma fonte a cada seção verificada."));
      }
      const draft = json(sections);
      const headers = item ? {"If-Match": '"' + item.id + '"'} : {"If-None-Match": "*"};
      if (research) headers["X-Research-Revision"] = research.id;
      try {
        const saved = await api(endpoint, "POST", {sections: JSON.parse(draft)}, headers);
        if (!active()) return;
        item = saved;
        await refresh(false);
        if (!active()) return;
        if (json(sections) === draft) {
          render();
          notify(tr("Script revision saved.", "Revisão do roteiro salva."));
        } else {
          reviewBox.replaceChildren();
          showRecovery(tr("Revision saved. Your newer edits are still unsaved.",
            "Revisão salva. As alterações feitas depois continuam no formulário."));
        }
      } catch (error) {
        if (!active()) return;
        if (error.status === 412) scriptConflict();
        else if (error.status === 409 && error.message.startsWith("Research changed.")) {
          reviewBox.replaceChildren();
          showRecovery(tr("The research changed. You can update sources without replacing your text.",
            "A pesquisa mudou. Você pode atualizar as fontes e manter seu texto."), updateSourcesButton);
        } else throw error;
      }
    }, true),
    item && button(tr("Open storyboard", "Abrir storyboard"), async () => {
      await mutate(path("/storyboard"), "POST");
      page = "storyboard";
      render();
    }, false, !ready(item))), reviewBox, revisions("script"),
    item && node("details", {}, node("summary", {}, tr("Load earlier text for review", "Carregar texto anterior para revisar")),
      row(...project.versions.filter(v => v.kind === "script" && !v.current).map(v =>
        button("v" + v.seq, () => {
          load(v);
          fields();
          editing = true;
          form.querySelector("input").focus();
        })))));
}

const sceneFields = {
  title: ["Scene title", "Título da cena"], narration: ["Narration", "Narração"], seconds: ["Duration (seconds)", "Duração (segundos)"],
  description: ["Visual description", "Descrição visual"], camera_angle: ["Camera angle", "Ângulo de câmera"],
  camera_movement: ["Camera movement", "Movimento de câmera"], lighting: ["Lighting", "Iluminação"],
  environment: ["Environment", "Ambiente"], artistic_style: ["Artistic style", "Estilo artístico"],
  image_prompt: ["Image prompt", "Prompt da imagem"], video_prompt: ["Video prompt", "Prompt do vídeo"],
  negative_prompt: ["Negative prompt", "Prompt negativo"],
};

function editScene(sid, item) {
  const data = {...item.data};
  const fields = Object.entries(sceneFields).map(([key, labels]) => {
    const attrs = {oninput: event => data[key] = key === "seconds" ? Number(event.target.value) : event.target.value};
    const field = ["narration", "image_prompt", "video_prompt", "description"].includes(key) ? area(data[key], attrs) :
      input(data[key], {...attrs, type: key === "seconds" ? "number" : "text"});
    return label(tr(...labels), field);
  });
  const models = {local: "motion", runway: "gen4_turbo", google_flow: "import"};
  const provider = select([["local", tr("Local motion", "Movimento local")], ["runway", "Runway · Gen-4 Turbo"], flowOption()], data.provider);
  const overrides = area(json(data.style_overrides));
  const references = project.versions.filter(v => v.kind === "reference" && ready(v)).map(v => {
    const choice = input("", {type: "checkbox", checked: data.references.includes(v.id)});
    return [v.id, choice, label(v.data.title, choice)];
  });
  modal(tr("Edit scene", "Editar cena"), stack(...fields, label(tr("Video provider and model", "Provedor e modelo de vídeo"), provider),
    label(tr("Style overrides", "Ajustes de estilo"), overrides), small(tr("Approved references", "Referências aprovadas")),
    ...references.map(([, , field]) => field)), [button(tr("Save scene revision", "Salvar revisão da cena"), async () => {
      await mutate(path("/scenes/" + sid), "PUT", {...data, provider: provider.value,
        model: models[provider.value], style_overrides: parse(overrides),
        references: references.filter(([, check]) => check.checked).map(([id]) => id)});
      ui("dialog").close();
    }, true)]);
}

function moveScene(sid, offset) {
  const order = [...project.scenes];
  const i = order.indexOf(sid);
  [order[i], order[i + offset]] = [order[i + offset], order[i]];
  return mutate(path("/order"), "PUT", {scenes: order});
}

function splitScene(sid, item) {
  const seconds = input(item.data.seconds / 2, {type: "number", min: ".1", max: item.data.seconds - .1, step: ".1"});
  const words = item.data.narration.split(" ");
  const middle = Math.max(1, Math.floor(words.length / 2));
  const first = area(words.slice(0, middle).join(" "));
  const second = area(words.slice(middle).join(" "));
  modal(tr("Split scene", "Dividir cena"), stack(label(tr("First segment seconds", "Segundos do primeiro trecho"), seconds),
    label(tr("First narration", "Primeira narração"), first), label(tr("Second narration", "Segunda narração"), second)), [
    button(tr("Split scene", "Dividir cena"), async () => {
      await mutate(path("/scenes/" + sid + "/split"), "POST", {seconds: Number(seconds.value), first: first.value, second: second.value});
      ui("dialog").close();
    }, true),
  ]);
}

function storyboardView() {
  const provider = select([["local", tr("Local composition", "Composição local")], ["openai", "OpenAI"]], project.data.defaults?.image || "local");
  const selection = () => project.scenes.filter(sid => selected.has(sid));
  const cards = project.scenes.map((sid, i) => {
    const scene = sceneData(sid), image = current("image/" + sid);
    const check = input("", {type: "checkbox", checked: selected.has(sid),
      onchange: event => event.target.checked ? selected.add(sid) : selected.delete(sid)});
    return card(row(label(scene.data.title, check), assetBadge(scene)), small(i + 1 + " · " + scene.data.seconds + " s"),
      node("p", {}, scene.data.description), small([scene.data.camera_angle, scene.data.camera_movement, scene.data.lighting].filter(Boolean).join(" · ")),
      mediaPreview(image), image && row(small(tr("Reference frame", "Quadro de referência")), assetBadge(image)), reviewControls(image),
      row(button(tr("Edit visuals", "Editar visuais"), () => editScene(sid, scene)),
        button(tr("Split", "Dividir"), () => splitScene(sid, scene)), button("↑", () => moveScene(sid, -1), false, i === 0),
        button("↓", () => moveScene(sid, 1), false, i === project.scenes.length - 1)),
      reviewControls(scene), button(tr("Estimate reference image", "Estimar imagem de referência"),
        () => quote([{operation: "image", scene: sid, provider: provider.value}]), false, !ready(scene) || ready(image)),
      button(tr("Import storyboard frame", "Importar quadro do storyboard"), () => importAsset("image", sid), false, !ready(scene)),
        button(tr("Image prompt", "Prompt da imagem"), () => modal(
          tr("Image prompt", "Prompt da imagem"),
          stack(area(scene.data.image_prompt, {readonly: true, rows: 6}),
            node("a", {href: "https://chatgpt.com/images", target: "_blank", rel: "noopener noreferrer"},
              tr("Open ChatGPT Images", "Abrir ChatGPT Imagens"))))),
      revisions(scene.slot), image && revisions(image.slot));
  });
  return stack(title(tr("Storyboard", "Storyboard")), card(
    row(button(tr("Build from approved script", "Montar a partir do roteiro aprovado"),
      () => mutate(path("/storyboard"), "POST"), true, !ready(current("script"))),
    label(tr("Preview provider", "Provedor de prévias"), provider)),
    row(button(tr("Approve selected scenes", "Aprovar cenas selecionadas"), () => {
      const items = selection().map(sceneData);
      if (!items.length) throw new Error(tr("Select scenes to approve", "Selecione cenas para aprovar"));
      review(items, "APPROVED");
    }), button(tr("Merge selected scenes", "Unir cenas selecionadas"), () => mutate(path("/scenes/merge"), "POST", {scenes: selection()})),
    button(tr("Estimate selected previews", "Estimar prévias selecionadas"),
      () => quote(selection().map(scene => ({operation: "image", scene, provider: provider.value})))))), grid(...cards));
}

const animations = {
  editorial_overlay: ["Editorial overlay · Anime.js", "Sobreposição editorial · Anime.js"],
  zoom_in: ["Push in", "Aproximar câmera"],
  zoom_out: ["Pull back", "Afastar câmera"],
  pan_left: ["Pan left", "Deslocar à esquerda"],
  pan_right: ["Pan right", "Deslocar à direita"],
  parallax: ["Layered parallax", "Parallax em camadas"],
  map: ["Animated map", "Mapa animado"],
  bar_chart: ["Animated bar chart", "Gráfico de barras animado"],
  timeline: ["Animated timeline", "Linha do tempo animada"],
};
const graphicModes = new Set(["map", "bar_chart", "timeline"]);

function chooseAnimation(sid, scene, clip) {
  const saved = clip?.data.animation;
  const planned = scene.data.sections.length === 1 && current("script")?.data.sections
    .find(section => section.key === scene.data.sections[0])?.motion_cues;
  const motion = motionCueEditor(saved?.cues || planned);
  const mode = select(Object.entries(animations).map(([name, labels]) => [name, tr(...labels)]),
    saved?.mode || (motion.cues.length ? "editorial_overlay" : "zoom_in"));
  const fields = stack();
  const heading = input(saved?.title || "", {maxlength: 100, placeholder: scene.data.title});
  const references = project.versions.filter(v => ready(v) && v.kind === "reference" && v.data.transparent);
  const layerOptions = [["", tr("Choose a layer", "Escolha uma camada")], ...references.map(v => [v.id, v.data.title])];
  const layers = [0, 1].map(i => select(layerOptions, saved?.layers?.[i] || ""));
  const entries = stack();
  let values = [];

  function addEntry(existing = {}) {
    const name = input(existing.label || "", {maxlength: 55, required: true});
    const value = input(existing.value ?? "", {type: "number", min: 0, step: "any"});
    const lat = input(existing.lat ?? "", {type: "number", min: -90, max: 90, step: "any"});
    const lon = input(existing.lon ?? "", {type: "number", min: -180, max: 180, step: "any"});
    const item = {name, value, lat, lon};
    const entry = row(label(tr("Label", "Rótulo"), name),
      mode.value === "bar_chart" && label(tr("Value", "Valor"), value),
      mode.value === "map" && [label(tr("Latitude", "Latitude"), lat), label(tr("Longitude", "Longitude"), lon)],
      button(tr("Remove", "Remover"), () => {
        values = values.filter(value => value !== item);
        entry.remove();
      }));
    values.push(item);
    entries.append(entry);
  }

  function redraw() {
    fields.replaceChildren();
    values = [];
    entries.replaceChildren();
    if (mode.value === "editorial_overlay") {
      const frame = current("image/" + sid);
      fields.append(small(tr("Use the approved frame as the background. Positions are percentages from the top left.",
        "O quadro aprovado fica como fundo. As posições são percentuais a partir do canto superior esquerdo.")));
      if (frame) fields.append(node("details", {}, node("summary", {}, tr("View reference frame", "Ver quadro de referência")), mediaPreview(frame)));
      fields.append(motion.element);
    }
    if (mode.value === "parallax") {
      fields.append(node("p", {}, tr(
        "The approved storyboard frame is the background. Upload transparent PNGs under Asset library and approve them to use as layers.",
        "O quadro aprovado do storyboard é o fundo. Importe PNGs transparentes na Biblioteca e aprove-os para usar como camadas.")),
      label(tr("Middle layer", "Camada intermediária"), layers[0]),
      label(tr("Foreground layer (optional)", "Primeiro plano (opcional)"), layers[1]));
    }
    if (graphicModes.has(mode.value)) {
      fields.append(label(tr("Graphic title", "Título do gráfico"), heading), entries,
        button(tr("Add entry", "Adicionar item"), () => {
          if (values.length < 8) addEntry();
        }));
      (saved?.mode === mode.value ? saved.entries : [{}]).forEach(addEntry);
    }
  }
  mode.addEventListener("change", redraw);
  redraw();
  const dialog = ui("dialog");
  modal(tr("Local animation", "Animação local"), stack(label(tr("Preset", "Modelo"), mode), fields), [
    button(tr("Estimate animation", "Estimar animação"), async () => {
      const animation = {mode: mode.value};
      if (mode.value === "editorial_overlay") animation.cues = motion.cues;
      if (mode.value === "parallax") {
        animation.layers = layers.map(control => control.value).filter(Boolean);
      }
      if (graphicModes.has(mode.value)) {
        animation.title = heading.value;
        animation.entries = values.map(({name, value, lat, lon}) => {
          const entry = {label: name.value.trim()};
          if (mode.value === "bar_chart") entry.value = value.valueAsNumber;
          if (mode.value === "map") {
            entry.lat = lat.valueAsNumber;
            entry.lon = lon.valueAsNumber;
          }
          return entry;
        });
      }
      await quote([{operation: "video", scene: sid, animation}]);
    }, true),
  ]);
}

function scenesView() {
  const eligible = sid => {
    const scene = sceneData(sid);
    return ready(scene) && scene.data.provider !== "google_flow" && ready(current("image/" + sid)) && !ready(current("video/" + sid));
  };
  return stack(title(tr("Scene generation", "Geração de cenas")),
    row(button(tr("Estimate all eligible scenes", "Estimar todas as cenas elegíveis"),
      () => quote(project.scenes.filter(eligible).map(scene => ({operation: "video", scene}))), true,
      !project.scenes.some(eligible))),
    grid(...project.scenes.map(sid => {
      const scene = sceneData(sid), image = current("image/" + sid), clip = current("video/" + sid);
      const usesFlow = scene.data.provider === "google_flow";
      const jobs = project.jobs.filter(job => job.slot === "video/" + sid);
      const generating = jobs.some(job => ["DRAFT", "GENERATING"].includes(job.status));
      return card(row(node("h2", {}, scene.data.title), assetBadge(clip)), mediaPreview(clip),
        clip?.data.source_provider === "google_flow" && small(tr("Imported from Google Flow", "Importado do Google Flow")),
        node("p", {}, scene.data.narration), small([usesFlow ? flowOption()[1] : `${scene.data.provider} · ${scene.data.model}`, scene.data.seconds + " s",
          clip ? clip.data.resolution + "p" : "", jobs.reduce((sum, job) => sum + job.attempts, 0) + " " + tr("attempts", "tentativas")]
          .filter(Boolean).join(" · ")),
        node("p", {}, scene.data.video_prompt), image && node("details", {}, node("summary", {}, tr("Reference image", "Imagem de referência")), mediaPreview(image)),
        jobs[0] && small(tr("Last estimate ", "Última estimativa ") + format(jobs[0].quoted) + " · " +
          tr("Actual ", "Efetivo ") + format(jobs[0].actual) + " · " + tr("Uncertain ", "Incerto ") + format(jobs[0].uncertain)),
        row(!usesFlow && button(clip ? tr("Estimate replacement", "Estimar substituição") : tr("Estimate generation", "Estimar geração"),
          () => quote([{operation: "video", scene: sid}]), true, generating || !eligible(sid)),
        scene.data.provider === "local" && button(tr("Local animation", "Animação local"),
          () => chooseAnimation(sid, scene, clip), false, generating || !ready(scene)),
        button(tr("Edit prompt or model", "Editar prompt ou modelo"), () => editScene(sid, scene)),
        button("Google Flow", () => importFlowClip(sid, scene, image), usesFlow, !ready(scene) || !ready(image)),
        button(tr("Import clip", "Importar clipe"), () => importAsset("video", sid), false, !ready(scene) || !ready(image))), reviewControls(clip),
        revisions("video/" + sid));
    })), jobsTable());
}

function editTranslation(sid, lang, item) {
  const data = item?.data || {title: "", description: "", narration: ""};
  const name = input(data.title);
  const description = area(data.description);
  const narration = area(data.narration);
  modal(lang, stack(label(tr("Translated title", "Título traduzido"), name), label(tr("Translated description", "Descrição traduzida"), description),
    label(tr("Translated narration", "Narração traduzida"), narration)), [
    button(tr("Save translation", "Salvar tradução"), async () => {
      await mutate(path("/scenes/" + sid + "/translation/" + lang), "POST",
        {title: name.value, description: description.value, narration: narration.value});
      ui("dialog").close();
    }, true),
  ]);
}

async function chooseElevenLabsVoice(target, cursor = "") {
  const entries = await api("/api/providers/elevenlabs/voices" + (cursor ? "?cursor=" + encodeURIComponent(cursor) : ""));
  const voice = select(entries.voices.map(item => [item.id, item.name]));
  modal("ElevenLabs", entries.voices.length ? label(tr("Voice", "Voz"), voice)
    : node("p", {}, tr("No voices on this page.", "Nenhuma voz nesta página.")), [
    entries.next && button(tr("More voices", "Mais vozes"), () => chooseElevenLabsVoice(target, entries.next)),
    button(tr("Use voice", "Usar voz"), () => {
      target.value = voice.value;
      ui("dialog").close();
    }, true, !entries.voices.length),
  ].filter(Boolean));
}

function narrationView() {
  return stack(title(tr("Narration", "Narração")), grid(...project.scenes.flatMap(sid => project.data.languages.map((language, i) => {
    const scene = sceneData(sid), translation = current("translation/" + sid + "/" + language);
    const voice = current("voice/" + sid + "/" + language);
    const provider = select([["local", tr("System voice", "Voz do sistema")], ["elevenlabs", "ElevenLabs"], ["openai", "OpenAI"]], project.data.defaults?.voice || "local");
    const translationProvider = select([codexOption(), apiOption], project.data.defaults?.translation || "openai");
    const voiceName = input(voice?.data.provider === provider.value ? voice.data.voice : provider.value === "elevenlabs" ? "" : "alloy");
    const picker = button(tr("Choose ElevenLabs voice", "Escolher voz do ElevenLabs"),
      () => chooseElevenLabsVoice(voiceName));
    picker.hidden = provider.value !== "elevenlabs";
    provider.addEventListener("change", () => {
      picker.hidden = provider.value !== "elevenlabs";
      voiceName.value = voice?.data.provider === provider.value ? voice.data.voice : picker.hidden ? "alloy" : "";
    });
    return card(row(node("h2", {}, scene.data.title), small(language)), node("p", {}, i ? translation?.data.narration : scene.data.narration),
      i > 0 && row(assetBadge(translation), button(tr("Edit translation", "Editar tradução"), () => editTranslation(sid, language, translation), false, !ready(scene)),
        label(tr("Translation provider", "Provedor de tradução"), translationProvider),
        button(tr("Estimate AI translation", "Estimar tradução por IA"),
          () => quote([{operation: "translation", scene: sid, language, provider: translationProvider.value}]), false, !ready(scene))),
      reviewControls(translation), row(label(tr("Speech provider", "Provedor de voz"), provider),
        label(tr("Voice or voice ID", "Voz ou ID da voz"), voiceName), picker),
      button(tr("Estimate narration", "Estimar narração"),
        () => quote([{operation: "voice", scene: sid, language, voice: voiceName.value, provider: provider.value}]),
        true, !ready(scene) || (i > 0 && !ready(translation))),
      button(tr("Import narration", "Importar narração"), () => importAsset("voice", sid, language),
        false, !ready(scene) || (i > 0 && !ready(translation))),
      row(assetBadge(voice), voice && small(voice.data.voice)), mediaPreview(voice), reviewControls(voice),
      revisions("voice/" + sid + "/" + language));
  }))), jobsTable());
}

function retentionPanel(assembly) {
  const markers = (assembly?.data.retention || []).flatMap(scene => scene.cues.map(cue => ({...cue, scene: scene.scene})));
  const exports = project.versions.filter(v => v.kind === "export" && v.data.retention?.length).slice(0, 6);
  return card(node("h2", {}, tr("Audience retention", "Retenção de público")),
    small(tr("Compare the exact export in YouTube Studio. Review dips, replays and average percentage viewed for comparable traffic and lengths.",
      "Compare a exportação no YouTube Studio. Confira quedas, reprises e percentual médio assistido em vídeos de duração e tráfego comparáveis.")),
    markers.length > 0 && node("details", {}, node("summary", {}, tr("Rendered motion cues", "Intervenções renderizadas")),
      ...markers.map(cue => node("p", {}, cue.start.toFixed(2) + "–" + cue.end.toFixed(2) + " s · " + cue.scene + " · " + cue.purpose))),
    ...exports.map(item => node("a", {href: path("/retention/" + item.id + ".csv"), download: true},
      tr("Retention worksheet", "Planilha de retenção") + " · v" + item.seq)),
    node("a", {href: "https://support.google.com/youtube/answer/9314415", target: "_blank", rel: "noopener noreferrer"},
      tr("YouTube retention report", "Relatório de retenção do YouTube")));
}

function sceneEvidence(scene, sceneTitle) {
  const endpoint = path("/scenes/" + scene + "/evidence");
  const content = stack();
  const details = node("details", {},
    node("summary", {}, tr("Scene sources", "Fontes da cena")), content);
  const categories = {
    verified: ["Verified in script", "Verificado no roteiro"],
    assumption: ["Assumption", "Premissa"],
    speculation: ["Speculation", "Especulação"],
    uncertain: ["Uncertain", "Incerto"],
  };
  let loading = false, loaded = false;
  async function load() {
    if (!details.open || loading || loaded) return;
    loading = true;
    content.replaceChildren(node("p", {role: "status"}, tr("Loading sources…", "Carregando fontes…")));
    try {
      const evidence = await api(endpoint);
      if (!details.isConnected) return;
      loaded = true;
      content.replaceChildren(stack(
        evidence.binding === "historical" && small(tr("Sources reconstructed from the script of that revision.",
          "Fontes recuperadas do roteiro da época.")),
        !evidence.fresh && small(tr("This scene needs review. Its original sources are shown here.",
          "Esta cena precisa de revisão. As fontes originais aparecem aqui.")),
        evidence.sections.length ? evidence.sections.map(section => stack(
          (evidence.sections.length > 1 || section.title !== sceneTitle) && node("h3", {}, section.title),
          small(tr(...(categories[section.category] || categories.uncertain))),
          section.sources.length ? node("ul", {}, section.sources.map(source => node("li", {},
            /^https?:\/\//i.test(source.url) ? node("a", {href: source.url, target: "_blank", rel: "noopener noreferrer"},
              source.title) : source.title,
            node("p", {class: "review-note-text"}, source.excerpt)))) :
            small(tr("No source was recovered for this section.", "Nenhuma fonte foi recuperada para esta seção.")),
        )) : node("p", {}, tr("No research is linked to this scene.", "Esta cena não tem pesquisa vinculada."))));
    } catch {
      if (details.isConnected) content.replaceChildren(
        node("p", {role: "status"}, tr("Could not load source material.", "Não foi possível carregar as fontes.")),
        button(tr("Try again", "Tentar de novo"), () => {
          details.querySelector("summary").focus();
          return load();
        }));
    } finally {
      loading = false;
    }
  }
  details.addEventListener("toggle", load);
  return details;
}

function timelineView() {
  let saved = current("timeline");
  const recovery = timelineRecovery ??= browserTimelineDrafts(projectId);
  if (!timelineDraft || (!timelineDraft.clips.length && project.scenes.length)) {
    const defaultClips = project.scenes.map(scene => ({scene, trim: 0, narration_offset: 0, transition: "cut"}));
    const existing = saved?.data;
    const initial = existing && defaultClips.length === existing.clips.length &&
      existing.clips.every(clip => project.scenes.includes(clip.scene)) ? structuredClone(existing) : {
      clips: defaultClips, languages: [...project.data.languages], aspect: project.data.style.aspect,
      resolution: 1080, subtitles: true, watermark: false, music: null, music_volume: .15, metadata: {},
    };
    const checkpoint = recovery.current();
    timelineBase = checkpoint ? checkpoint.base : saved?.id || null;
    timelineDraft = checkpoint ? structuredClone(checkpoint.draft) : initial;
    timelineInitial = json(checkpoint?.base ?
      project.versions.find(version => version.id === checkpoint.base)?.data ?? initial : initial);
    timelineStored = Boolean(checkpoint);
    if (checkpoint?.scene) selectedClip = Math.max(0, timelineDraft.clips.findIndex(clip => clip.scene === checkpoint.scene));
  }
  const draft = timelineDraft;
  const reviewContext = reviewEdit?.project === projectId ? reviewEdit : null;
  const reviewIndex = reviewContext && reviewContext.revision === saved?.id &&
    json(draft) === json(saved.data) ? draft.clips.findIndex(clip => clip.scene === reviewContext.scene) : -1;
  let reviewOffset = reviewIndex >= 0 && !reviewContext.opened ? reviewContext.offset : null;
  if (reviewOffset !== null) selectedClip = reviewIndex;
  const rough = roughCutAction({inEditor: true});
  const usable = item => ready(item) || (draft.watermark && item?.draft_preview &&
    item.fresh && item.status === "AWAITING_REVIEW");
  const sequence = node("div", {class: "timeline", role: "group",
    "aria-label": tr("Scene sequence", "Sequência de cenas")});
  const inspector = node("div", {class: "editor-inspector stack"});
  const audioAssets = project.versions.filter(v => v.kind === "audio" && ready(v)).map(v => [v.id, v.data.title]);
  const assembly = current("assembly"), exported = current("export");
  const panel = node("div", {class: "preview-stage"});
  const previewInfo = node("p", {class: "muted preview-info"});
  const draftInfo = node("small", {role: "status"});
  const assemblyBadge = assetBadge(assembly);
  const versions = new Map(project.versions.map(v => [v.id, v]));
  const renderedRevision = versions.get(assembly?.data.timeline_revision);
  const chapters = (assembly?.data.retention || []).flatMap(entry => {
    const slot = versions.get(entry.video)?.slot;
    return slot?.startsWith("video/") ? [{...entry, sceneId: slot.slice(6)}] : [];
  });
  const remembered = editorPreview?.project === projectId ? editorPreview : {};
  const interfaceLanguage = locale === "pt" ? "pt-BR" : "en-US";
  const cutLanguage = draft.clips.length && draft.clips.every(clip =>
    usable(sceneData(clip.scene)) && usable(footage(clip))) ?
    [remembered.language, interfaceLanguage, ...draft.languages].find(language =>
      draft.languages.includes(language) && draft.clips.every(clip =>
        usable(current("voice/" + clip.scene + "/" + language)))) : undefined;
  const fallbackMode = assembly?.url ? "assembly" : "clip";
  const sources = [
    ["cut", tr("Cut preview", "Prévia do corte")],
    ...(assembly?.url ? [["assembly", tr("Rendered assembly", "Montagem renderizada")]] : []),
    ["scene", tr("Live scene", "Prévia da cena")],
    ["clip", tr("Original take", "Tomada original")],
  ];
  let explicitMode = reviewOffset === null && sources.some(([value]) => value === remembered.mode) ?
    remembered.mode : null;
  let openingCut = !explicitMode && Boolean(cutLanguage);
  const mode = select(sources, explicitMode || (openingCut ? "cut" : fallbackMode));
  mode.setAttribute("aria-label", tr("Preview source", "Fonte da prévia"));
  const previewWarning = node("small", {class: "scene-preview-status", role: "status"});
  const retryCut = button(tr("Retry cut preview", "Tentar prévia do corte novamente"), () => {
    openingCut = true;
    mode.value = "cut";
    mode.focus();
    clearPreviewWarning();
    showPreview(true);
  });
  const previewRecovery = row(previewWarning, retryCut);
  previewRecovery.hidden = true;
  const previewLanguage = select([]);
  const languageField = label(tr("Preview language", "Idioma da prévia"), previewLanguage);
  languageField.classList.add("scene-preview-language");
  const scenePlayer = createScenePreview(scene => {
    if (mode.value !== "cut" || inspector.contains(document.activeElement)) return;
    const index = draft.clips.findIndex(clip => clip.scene === scene);
    if (index >= 0 && index !== selectedClip) selectClip(index, false);
  });
  activeScenePreview = scenePlayer;
  const interactive = () => mode.value === "scene" || mode.value === "cut";
  let sceneRequest = 0, sceneTimer, sceneKey, previewLanguages = [];
  let previewMedia, clipPreview, clipPreviewId, rendered;

  function footage(clip) {
    return clip?.video ? versions.get(clip.video) : clip && current("video/" + clip.scene);
  }

  function referenceImage(video) {
    return video?.dependencies.map(dep => versions.get(dep.id)).find(asset => asset?.kind === "image");
  }

  function clearPreviewWarning() {
    previewWarning.textContent = "";
    previewRecovery.hidden = true;
  }

  function refreshDraftInfo() {
    let message;
    if ((saved?.id || null) !== timelineBase) {
      message = timelineStored ?
        tr("Draft kept in this browser · the saved cut has changed", "Rascunho neste navegador · o corte salvo mudou") :
        tr("The saved timeline changed in another session.", "A linha do tempo salva mudou em outra sessão.");
    } else if (saved && json(saved.data) === json(draft)) {
      message = tr("Timeline saved · v", "Linha do tempo salva · v") + saved.seq;
    } else if (timelineStored) {
      message = tr("Draft kept in this browser", "Rascunho guardado neste navegador");
    } else if (json(draft) !== timelineInitial) {
      message = tr("Edits are only in this tab. Save the timeline to keep them.",
        "Os ajustes estão só nesta aba. Salve a linha do tempo para guardá-los.");
    } else {
      message = tr("Unsaved timeline", "Linha do tempo ainda não salva");
    }
    if (draftInfo.textContent !== message) draftInfo.textContent = message;
  }

  function keepDraft() {
    if (json(draft) === timelineInitial) {
      if (timelineStored) recovery.reset();
      timelineStored = false;
    } else {
      timelineStored = recovery.write(timelineBase, draft, draft.clips[selectedClip]?.scene);
    }
  }

  function openBrowserDrafts() {
    const date = new Intl.DateTimeFormat(locale === "pt" ? "pt-BR" : "en-US", {dateStyle: "short", timeStyle: "medium"});
    const items = recovery.others();
    modal(tr("Drafts in this browser", "Rascunhos neste navegador"),
      items.length ? stack(items.map(item => {
        const when = date.format(item.data.updated);
        const matches = item.data.base === (current("timeline")?.id || null);
        const restore = button(tr("Resume draft · ", "Retomar rascunho · ") + when, () => {
          recovery.select(item);
          timelineDraft = null;
          ui("dialog").close();
          render();
          ui("workspace").focus();
        });
        const removeDraft = button(tr("Delete draft", "Excluir rascunho"), () => {
          modal(tr("Delete this browser draft?", "Excluir este rascunho do navegador?"),
            node("p", {}, tr("The saved cut will still be available.", "O corte salvo continuará disponível.")),
            [button(tr("Delete draft", "Excluir rascunho"), () => {
              recovery.remove(item);
              refreshTimelineState();
              openBrowserDrafts();
            }, true)]);
        });
        const baseLabel = matches ? tr("Current saved cut", "Corte salvo atual") :
          tr("Earlier saved cut", "Corte salvo anterior");
        return stack(small([sceneData(item.data.scene)?.data.title, baseLabel].filter(Boolean).join(" · ")),
          row(restore, removeDraft));
      })) : node("p", {}, tr("There are no other drafts.", "Nenhum outro rascunho neste navegador.")));
  }

  function seek(player, time) {
    if (!player || !Number.isFinite(time)) return;
    if (player.readyState > 0) player.currentTime = Math.min(time, player.duration);
    else player.addEventListener("loadedmetadata", () => {
      player.currentTime = Math.min(time, player.duration);
    }, {once: true});
  }

  function renderedPlayer() {
    if (rendered) return rendered;
    rendered = assemblyPlayer(assembly);
    rendered.addEventListener("timeupdate", () => {
      if (mode.value !== "assembly" || inspector.contains(document.activeElement)) return;
      const chapter = chapters.find(item => item.start <= rendered.currentTime && rendered.currentTime < item.end);
      const index = draft.clips.findIndex(clip => clip.scene === chapter?.sceneId);
      if (index >= 0 && index !== selectedClip) selectClip(index, false);
    });
    return rendered;
  }

  function syncPreviewLanguages() {
    const languages = draft.languages.length ? draft.languages : project.data.languages;
    if (languages.join() !== previewLanguages.join()) {
      const preferred = [previewLanguage.value, openingCut && cutLanguage, remembered.language, interfaceLanguage]
        .find(lang => languages.includes(lang));
      previewLanguage.replaceChildren(...languages.map(lang => node("option", {value: lang}, lang)));
      previewLanguage.value = preferred || languages[0];
      previewLanguages = [...languages];
    }
    languageField.hidden = !interactive() || previewLanguages.length < 2;
  }

  function updateScenePreview() {
    syncPreviewLanguages();
    const clip = draft.clips[selectedClip];
    if (!interactive() || !clip) return;
    editorPreview = {project: projectId, mode: explicitMode, language: previewLanguage.value};
    const view = mode.value;
    const language = previewLanguage.value;
    const spec = view === "cut" ? {timeline: draft, language} : {clip, language, aspect: draft.aspect,
      resolution: draft.resolution, subtitles: draft.subtitles, watermark: draft.watermark};
    const key = JSON.stringify([view, spec]);
    if (key === sceneKey) return;
    sceneKey = key;
    const request = ++sceneRequest;
    const stillSelected = () => activeScenePreview === scenePlayer && request === sceneRequest && mode.value === view;
    const endpoint = path("/timeline/" + view + "-preview");
    scenePlayer.updating();
    clearTimeout(sceneTimer);
    sceneTimer = setTimeout(async () => {
      if (!stillSelected()) return;
      try {
        const plan = await api(endpoint, "POST", JSON.parse(key)[1]);
        if (stillSelected()) {
          openingCut = false;
          scenePlayer.apply(plan, draft.clips[selectedClip]?.scene, view === "cut" ? reviewOffset ?? 0 : 0);
          if (view === "cut" && reviewOffset !== null) {
            reviewContext.opened = true;
            reviewOffset = null;
          }
        }
      } catch (error) {
        if (stillSelected()) {
          sceneKey = null;
          if (openingCut) {
            openingCut = false;
            mode.value = fallbackMode;
            showPreview();
            previewRecovery.hidden = false;
            previewWarning.textContent = tr("The cut preview could not load.", "Não foi possível carregar a prévia do corte.");
          } else scenePlayer.error(error.message);
        }
      }
    }, 120);
  }

  function useScenePreview(lang) {
    if (lang && !previewLanguages.includes(lang)) return;
    if (lang) previewLanguage.value = lang;
    if (!interactive()) mode.value = "scene";
    explicitMode = mode.value;
    openingCut = false;
    clearPreviewWarning();
    showPreview();
  }

  function showPreview(seekToScene = false) {
    const clip = draft.clips[selectedClip];
    let media;
    syncPreviewLanguages();
    editorPreview = {project: projectId, mode: explicitMode, language: previewLanguage.value};
    assemblyBadge.hidden = mode.value !== "assembly";
    if (interactive()) {
      media = scenePlayer.element;
      previewInfo.textContent = mode.value === "cut" ? tr("Edits appear here. Review the rendered assembly to approve.",
        "Os ajustes aparecem aqui. Revise a montagem renderizada para aprovar.") :
        tr("Cut, narration and captions. Check the music, effects and transitions in the assembly.",
          "Corte, narração e legendas. Confira trilha, efeitos e transições na montagem.");
      if (clip) updateScenePreview();
      else scenePlayer.error(tr("Select a scene to preview it.", "Escolha uma cena para assistir aqui."));
      if (mode.value === "cut" && seekToScene && clip) scenePlayer.jump(clip.scene);
    } else if (mode.value === "clip") {
      const source = footage(clip);
      if (source?.id !== clipPreviewId) {
        clipPreviewId = source?.id;
        clipPreview = mediaPreview(source);
        if (clipPreview) {
          clipPreview.setAttribute("aria-label", tr("Selected take", "Tomada selecionada"));
          const poster = referenceImage(source);
          if (poster?.url) clipPreview.poster = poster.url;
        }
      }
      media = clipPreview;
      previewInfo.textContent = source ? tr(
        "Source take · assembly audio, subtitles and transitions appear after rendering.",
        "Tomada original · áudio da montagem, legendas e transições aparecem após renderizar.") :
        tr("Add a take to this scene to preview it.", "Adicione uma tomada à cena para assistir aqui.");
      if (seekToScene) seek(media, clip?.trim || 0);
    } else {
      media = renderedPlayer();
      previewInfo.textContent = (assembly.data.rough_cut ?
        tr("Local draft · v", "Rascunho local · v") : tr("Rendered assembly · v", "Montagem renderizada · v")) + assembly.seq +
        (renderedRevision ? tr(" · timeline v", " · linha do tempo v") + renderedRevision.seq : "") +
        (assembly.fresh ? "" : tr(" · inputs have changed", " · os insumos mudaram"));
      const chapter = chapters.find(item => item.sceneId === clip?.scene);
      if (seekToScene && chapter) seek(media, chapter.start);
    }
    if (media === previewMedia && panel.hasChildNodes()) return;
    if (previewMedia === scenePlayer.element) {
      clearTimeout(sceneTimer);
      sceneRequest++;
      sceneKey = null;
      scenePlayer.clear();
    } else previewMedia?.pause();
    panel.replaceChildren(media || node("p", {class: "muted"},
      tr("Your preview will appear here.", "Sua prévia vai aparecer aqui.")));
    previewMedia = media;
  }

  function captionEditor(clip) {
    const languages = project.data.languages;
    const language = select(languages.map(lang => [lang, lang]), previewLanguage.value);
    const text = area("", {rows: 4, maxlength: 8000});
    const hint = small("");
    const narration = () => current("voice/" + clip.scene + "/" + language.value)?.data.narration || "";
    const showText = () => {
      text.value = clip.captions?.[language.value] ?? narration();
      hint.textContent = previewLanguages.includes(language.value) ?
        tr("Caption edits keep the recorded narration.", "A edição da legenda mantém a narração gravada.") :
        tr("Enable this language in output settings to preview it.", "Inclua este idioma na saída para conferi-lo na prévia.");
    };
    language.addEventListener("change", () => {
      showText();
      if (interactive()) useScenePreview(language.value);
    });
    text.addEventListener("input", () => {
      const value = text.value.trim();
      if (value && value !== narration()) {
        clip.captions ??= {};
        clip.captions[language.value] = value;
      } else if (clip.captions) {
        delete clip.captions[language.value];
        if (!Object.keys(clip.captions).length) delete clip.captions;
      }
      useScenePreview(language.value);
    });
    showText();
    return node("details", {}, node("summary", {}, tr("Scene captions", "Legenda da cena")),
      stack(label(tr("Caption language", "Idioma da legenda"), language),
        label(tr("Caption text", "Texto da legenda"), text), hint));
  }

  function selectClip(index, seekToScene) {
    syncPreviewLanguages();
    if (seekToScene && reviewOffset !== null) {
      reviewContext.opened = true;
      reviewOffset = null;
    }
    selectedClip = index;
    [...sequence.children].forEach((control, i) =>
      control.setAttribute("aria-pressed", String(i === index)));
    const clip = draft.clips[index];
    inspector.replaceChildren(clip ? clipEditor(clip) : node("p", {},
      tr("Plan the scenes before assembling your video.", "Planeje as cenas antes de montar o vídeo.")));
    showPreview(seekToScene);
  }

  function fields(focus = false) {
    selectedClip = Math.min(selectedClip, Math.max(0, draft.clips.length - 1));
    sequence.replaceChildren(...draft.clips.map((item, i) => {
      const scene = sceneData(item.scene);
      const image = referenceImage(footage(item)) || current("image/" + item.scene);
      return node("button", {type: "button", onclick: () => selectClip(i, true)},
        image?.url && node("img", {src: image.url, alt: "", loading: "lazy", decoding: "async"}),
        node("span", {}, scene?.data.title || item.scene),
        small(formatDuration(item.seconds ?? scene?.data.seconds ?? 0)));
    }));
    selectClip(selectedClip, false);
    if (focus) sequence.children[selectedClip]?.focus();
  }

  mode.addEventListener("change", () => {
    explicitMode = mode.value;
    openingCut = false;
    clearPreviewWarning();
    showPreview(true);
  });
  mode.addEventListener("input", event => event.stopPropagation());
  previewLanguage.addEventListener("change", () => {
    openingCut = false;
    updateScenePreview();
  });
  previewLanguage.addEventListener("input", event => event.stopPropagation());

  function clipEditor(clip) {
    const sceneTitle = sceneData(clip.scene)?.data.title || tr("Scene", "Cena");
    const options = project.versions.filter(item => item.slot === "video/" + clip.scene && usable(item)).map(item => [item.id, "v" + item.seq]);
    const video = select([["", tr("Current take", "Tomada atual")], ...options], clip.video || "");
    if (clip.video && !options.some(([id]) => id === clip.video)) {
      video.append(node("option", {value: clip.video}, tr("Previous take · needs review", "Tomada anterior · requer revisão")));
      video.value = clip.video;
    }
    video.addEventListener("change", () => {
      clip.video = video.value || null;
      useScenePreview();
    });
    const numeric = (key, en, pt, optional = false) => label(tr(en, pt), input(clip[key] ?? "", {
      type: "number", min: 0, step: ".05", oninput: e => {
        clip[key] = optional && !e.target.value ? null : Number(e.target.value);
        useScenePreview();
      },
    }));
    const transition = select([["cut", tr("Cut", "Corte")], ["fade", tr("Fade to black", "Fade para preto")]], clip.transition);
    transition.addEventListener("change", () => { clip.transition = transition.value; });
    const effect = select([["", tr("None", "Nenhum")], ...audioAssets], clip.effect || "");
    effect.addEventListener("change", () => { clip.effect = effect.value || null; });
    const swap = offset => {
      const a = selectedClip, b = a + offset;
      [draft.clips[a], draft.clips[b]] = [draft.clips[b], draft.clips[a]];
      selectedClip = b;
      keepDraft();
      refreshDraftInfo();
      fields(true);
    };
    return stack(node("h2", {}, sceneTitle),
      label(tr("Take", "Tomada"), video),
      node("div", {class: "clip-timing"},
        numeric("trim", "Trim start (s)", "Corte inicial (s)"),
        numeric("seconds", "Clip length (s)", "Duração do clipe (s)", true)),
      row(button(tr("Move earlier", "Mover antes"), () => swap(-1), false, selectedClip === 0),
        button(tr("Move later", "Mover depois"), () => swap(1), false, selectedClip >= draft.clips.length - 1)),
      captionEditor(clip),
      sceneEvidence(clip.scene, sceneTitle),
      node("details", {}, node("summary", {}, tr("Scene audio and transition", "Áudio e transição da cena")),
        stack(numeric("narration_offset", "Narration offset (s)", "Atraso da narração (s)"),
          label(tr("Transition", "Transição"), transition), label(tr("Sound effect", "Efeito sonoro"), effect))));
  }
  fields();
  const resolution = select([[720, "720p"], [1080, "1080p"], [1440, "1440p"], [2160, "4K"]], draft.resolution);
  resolution.addEventListener("change", () => { draft.resolution = Number(resolution.value); });
  const aspect = select([["16:9", "16:9"], ["9:16", "9:16"]], draft.aspect);
  aspect.addEventListener("change", () => { draft.aspect = aspect.value; });
  const music = select([["", tr("None", "Nenhuma")], ...audioAssets], draft.music || "");
  music.addEventListener("change", () => { draft.music = music.value || null; });
  const output = card(node("h2", {}, tr("Output settings", "Configurações de saída")), row(
    label(tr("Resolution", "Resolução"), resolution), label(tr("Aspect ratio", "Proporção"), aspect),
    label(tr("Background music", "Trilha sonora"), music),
    label(tr("Music volume", "Volume da trilha"), input(draft.music_volume, {type: "number", min: 0, max: 1, step: ".05",
      oninput: e => { draft.music_volume = Number(e.target.value); }}))),
  row(label(tr("Subtitles", "Legendas"), input("", {type: "checkbox", checked: draft.subtitles,
    onchange: e => { draft.subtitles = e.target.checked; }})),
  label(tr("Draft watermark", "Marca d’água de rascunho"), input("", {type: "checkbox", checked: draft.watermark,
    onchange: e => {
      draft.watermark = e.target.checked;
      selectClip(selectedClip, false);
    }}))),
  ...project.data.languages.map(language => {
    const translation = project.scenes.map(sid => current("translation/" + sid + "/" + language)).find(ready);
    const info = draft.metadata[language] || {title: translation?.data.title || project.data.title,
      description: translation?.data.description || project.data.topic};
    const updateInfo = (key, value) => {
      info[key] = value;
      if (draft.languages.includes(language)) draft.metadata[language] = info;
    };
    const enabled = input("", {type: "checkbox", checked: draft.languages.includes(language), onchange: e => {
      if (e.target.checked) {
        draft.languages.push(language);
        draft.metadata[language] = info;
      } else {
        draft.languages = draft.languages.filter(item => item !== language);
        delete draft.metadata[language];
      }
    }});
    return row(label(language, enabled), label(tr("Export title", "Título de exportação"), input(info.title,
      {oninput: e => updateInfo("title", e.target.value)})), label(tr("Export description", "Descrição de exportação"), input(info.description,
      {oninput: e => updateInfo("description", e.target.value)})));
  }));
  refreshDraftInfo();
  const activeRender = project.jobs.find(job =>
    ["assembly", "rough_cut"].includes(job.operation) && ["DRAFT", "GENERATING"].includes(job.status));
  const missingInput = () => {
    if (!draft.clips.length || draft.clips.some(clip => !usable(sceneData(clip.scene)))) return "storyboard";
    if (draft.clips.some(clip => !usable(footage(clip)))) return "scenes";
    if (!draft.languages.length) return "output";
    if (draft.clips.some(clip => draft.languages.some(lang => !usable(current("voice/" + clip.scene + "/" + lang))))) {
      return "narration";
    }
    return null;
  };
  const previewActions = {
    storyboard: ["Review storyboard", "Revisar storyboard"],
    scenes: ["Prepare takes", "Preparar tomadas"],
    narration: ["Prepare narration", "Preparar narração"],
    output: ["Choose output languages", "Escolher idiomas da saída"],
  };
  const outputDetails = node("details", {}, node("summary", {}, tr("Output settings", "Configurações de saída")), output);
  async function loadSavedTimeline() {
    const target = projectId;
    await refresh(false);
    if (projectId !== target || timelineDraft !== draft) return;
    recovery.reset();
    timelineDraft = null;
    ui("dialog").close();
    render();
    ui("workspace").focus();
  }

  async function combineTimeline() {
    const target = projectId;
    await refresh(false);
    if (projectId !== target || timelineDraft !== draft || page !== "timeline") return;
    const latest = current("timeline");
    saved = latest;
    refreshTimelineState();
    if (latest?.id === timelineBase) {
      ui("dialog").close();
      return;
    }
    const base = project.versions.find(version => version.id === timelineBase);
    const plan = mergeTimelineDraft(base?.data, draft, latest?.data, project.scenes);
    const heading = tr("Combine timeline versions", "Combinar versões da linha do tempo");
    if (!plan) {
      modal(heading, node("p", {}, tr(
        "The versions need the same storyboard scenes and a saved starting revision. Your draft is still open.",
        "As versões precisam ter as mesmas cenas do storyboard e um corte de origem salvo. Seu rascunho continua aberto.")),
      [button(tr("Load saved timeline", "Carregar versão salva"), loadSavedTimeline)]);
      return;
    }
    const names = {
      order: ["Scene order", "Ordem das cenas"], picture: ["Take and timing", "Tomada e duração"],
      narration_offset: ["Narration offset", "Atraso da narração"], effect: ["Sound effect", "Efeito sonoro"],
      transition: ["Transition", "Transição"], caption: ["Caption", "Legenda"], aspect: ["Aspect ratio", "Proporção"],
      resolution: ["Resolution", "Resolução"], subtitles: ["Subtitles", "Legendas"], watermark: ["Watermark", "Marca d’água"],
      music: ["Music", "Trilha sonora"], output: ["Languages and metadata", "Idiomas e metadados"],
    };
    const sceneTitle = scene => sceneData(scene)?.data.title || scene;
    const number = new Intl.NumberFormat(locale === "pt" ? "pt-BR" : "en-US", {maximumFractionDigits: 3});
    const seconds = value => number.format(value) + " s";
    const assetName = (id, fallback) => {
      if (!id) return fallback;
      const asset = project.versions.find(version => version.id === id);
      return asset?.data.title || (asset ? "v" + asset.seq : id.slice(0, 8));
    };
    const describe = (field, value) => {
      switch (field) {
        case "order": return value.map(sceneTitle).join(" → ");
        case "picture": return [
          assetName(value.video, tr("Current take", "Tomada atual")),
          tr("Trim ", "Corte inicial ") + seconds(value.trim),
          value.seconds == null ? tr("Scene duration", "Duração da cena") : seconds(value.seconds),
        ].join(" · ");
        case "caption": return value ?? tr("From narration", "Da narração");
        case "narration_offset": return seconds(value);
        case "effect": return assetName(value, tr("None", "Nenhum"));
        case "transition": return value === "fade" ? tr("Fade to black", "Fade para preto") : tr("Cut", "Corte");
        case "resolution": return value + "p";
        case "music": return assetName(value.music, tr("None", "Nenhuma")) + " · " + Math.round(value.music_volume * 100) + "%";
        case "output": return value.languages.map(language => [
          language, value.metadata[language]?.title, value.metadata[language]?.description,
        ].filter(Boolean).join(" · ")).join("\n");
        default: return typeof value === "boolean" ? (value ? tr("On", "Ativado") : tr("Off", "Desativado")) : value;
      }
    };
    const choices = [];
    const conflictRows = plan.conflicts.map(change => {
      const title = [change.scene && sceneTitle(change.scene), tr(...names[change.field]), change.language].filter(Boolean).join(" · ");
      const mine = tr("My draft", "Meu rascunho"), currentCut = tr("Saved cut", "Corte salvo");
      const choice = select([["", tr("Choose a version", "Escolha uma versão")], ["mine", mine], ["current", currentCut]]);
      choice.setAttribute("aria-label", tr("Keep for ", "Manter em ") + title);
      choice.addEventListener("change", () => { confirm.disabled = choices.some(option => !option.value); });
      choices.push(choice);
      return card(
        node("h3", {}, title),
        node("div", {class: "proposal-diff"}, [["mine", mine], ["current", currentCut]].map(([side, name]) =>
          node("div", {class: "proposal-value"}, small(name), node("p", {}, describe(change.field, change[side]))))),
        label(tr("Keep", "Manter"), choice));
    });
    const confirm = button(tr("Open combined draft", "Abrir rascunho combinado"), () => {
      const selectedScene = draft.clips[selectedClip]?.scene;
      plan.conflicts.forEach((change, index) => change.apply(structuredClone(change[choices[index].value])));
      timelineDraft = plan.draft;
      timelineBase = latest.id;
      timelineInitial = json(latest.data);
      recovery.reset();
      timelineStored = json(timelineDraft) !== timelineInitial && recovery.write(timelineBase, timelineDraft, selectedScene);
      selectedClip = Math.max(0, timelineDraft.clips.findIndex(clip => clip.scene === selectedScene));
      ui("dialog").close();
      render();
      ui("workspace").focus();
    }, true, choices.length > 0);
    modal(heading, stack(node("p", {}, choices.length ?
      tr("Choose what to keep for each overlapping edit.", "Escolha o que manter em cada ajuste que mudou nas duas versões.") :
      tr("The edits can be combined.", "Os ajustes podem ser combinados.")),
      plan.reapplied > 0 && small(tr("Edits carried over · ", "Ajustes reaproveitados · ") + plan.reapplied),
      conflictRows), [confirm]);
  }

  async function saveTimeline() {
    const target = projectId;
    const draftData = json(draft);
    const headers = timelineBase ? {"If-Match": '"' + timelineBase + '"'} : {"If-None-Match": "*"};
    let result;
    try {
      result = await api(path("/timeline"), "PUT", JSON.parse(draftData), headers);
    } catch (error) {
      if (error.status !== 412 || projectId !== target) throw error;
      modal(tr("Timeline changed", "A linha do tempo mudou"),
        node("p", {}, timelineStored ?
          tr("Another revision was saved. You can load it and return to your draft from this browser's drafts.",
            "Outra revisão foi salva. Você pode carregá-la e voltar aos seus ajustes pelos rascunhos deste navegador.") :
          tr("Another revision was saved. Load it to discard the edits in this tab.",
            "Outra revisão foi salva. Carregue-a para descartar os ajustes desta aba.")), [
          button(tr("Combine versions", "Combinar versões"), combineTimeline, true),
          button(tr("Load saved timeline", "Carregar versão salva"), loadSavedTimeline),
        ]);
      return null;
    }
    if (projectId !== target || timelineDraft !== draft) return null;
    saved = result;
    timelineBase = result.id;
    const unchanged = json(draft) === draftData;
    timelineInitial = json(result.data);
    if (unchanged) {
      recovery.reset(true);
      timelineStored = false;
      timelineDraft = structuredClone(result.data);
    } else keepDraft();
    await refresh(unchanged);
    if (!unchanged) refreshDraftInfo();
    return projectId === target && unchanged ? result : null;
  }
  const renderButton = button(tr("Render preview", "Renderizar prévia"), async () => {
    const revision = await saveTimeline();
    if (revision) await quote([{operation: "assembly", version: revision.id}]);
  }, true);
  const missingButton = button("", () => {
    const next = missingInput();
    if (next === "output") {
      outputDetails.open = true;
      outputDetails.querySelector("summary").focus();
    } else if (next) navigate(next);
  }, true);
  const controls = row(
    button(tr("Save timeline", "Salvar linha do tempo"), async () => {
      if (await saveTimeline()) notify(tr("Timeline saved", "Linha do tempo salva"));
    }, false, !draft.clips.length),
    missingButton, renderButton, saved && button(tr("Review delivery", "Revisar entrega"), async () => {
      if (json(saved.data) !== json(draft) && !await saveTimeline()) return;
      navigate("delivery");
    }), activeRender && small(tr("Rendering · ", "Renderizando · ") + activeRender.progress + "%"));
  const reviewReference = reviewContext && stack(
    node("details", {}, node("summary", {}, tr("Review note at ", "Comentário em ") + timecode(reviewContext.note.position_ms)),
      node("p", {class: "review-note-text"}, reviewContext.note.body)),
    reviewIndex < 0 && !reviewContext.opened && node("p", {role: "status"}, tr(
      "Your editor has a different cut. Check the note on its reviewed assembly before applying it.",
      "O corte do editor está diferente. Confira o comentário na montagem revisada antes de aplicar o ajuste.")),
    row(button(tr("Back to review note", "Voltar ao comentário"), () => {
      history.replaceState(null, "", reviewContext.note.url);
      reviewReturnFocus = reviewContext.note.id;
      navigate("delivery");
    })));
  const content = node("section", {class: "editor-surface card stack"}, node("div", {class: "editor-layout"},
    node("div", {class: "stack preview-stack"}, panel, row(mode, languageField, assemblyBadge), previewInfo, previewRecovery,
      reviewReference),
    sequence, inspector));
  const loadSaved = button(tr("Open saved cut", "Abrir corte salvo"), loadSavedTimeline);
  const combine = button(tr("Combine versions", "Combinar versões"), combineTimeline, true);
  const browserDrafts = button(tr("Browser drafts", "Rascunhos neste navegador"), openBrowserDrafts);
  const result = stack(title(project.data.title), row((!rough || timelineStored) && draftInfo, combine, loadSaved, browserDrafts),
    rough || content,
  !rough && controls,
    assembly && card(node("h2", {}, tr("Review rendered version", "Revisar versão renderizada")), reviewControls(assembly),
      row(node("a", {href: assembly.url + "?attachment=true"}, tr("Download assembly", "Baixar montagem")),
        !exported?.fresh && !assembly.data.watermark && button(tr("Prepare approved export", "Preparar exportação aprovada"),
          () => quote([{operation: "export", version: assembly.id}]), true, !ready(assembly))),
      exported && row(node("a", {href: exported.url + "?attachment=true"}, exported.fresh ?
        tr("Download final MP4", "Baixar MP4 final") : tr("Download previous MP4", "Baixar MP4 anterior")),
        ...Object.entries(exported.data.captions || {}).map(([lang, id]) => node("a",
          {href: path("/media/" + id + "?attachment=true")}, lang + " · SRT"))),
      revisions("assembly"), exported && revisions("export")), !rough && outputDetails,
    current("timeline") && button(tr("Review agent proposals", "Revisar propostas de agentes"), () => navigate("proposals")),
    node("details", {}, node("summary", {}, tr("Audience retention", "Retenção de público")), retentionPanel(assembly)), jobHistory());
  const refreshTimelineState = () => {
    refreshDraftInfo();
    loadSaved.hidden = !timelineStored || !saved || (saved.id === timelineBase && json(saved.data) === json(draft));
    combine.hidden = !saved || saved.id === timelineBase;
    browserDrafts.hidden = !recovery.hasOthers();
    browserDrafts.disabled = !timelineStored && json(draft) !== timelineInitial;
    updateScenePreview();
    const next = missingInput();
    missingButton.textContent = next ? tr(...previewActions[next]) : "";
    missingButton.hidden = !next;
    renderButton.hidden = Boolean(next);
    renderButton.disabled = Boolean(activeRender);
  };
  refreshTimelineState();
  const edited = () => {
    keepDraft();
    refreshTimelineState();
  };
  result.addEventListener("input", edited);
  result.addEventListener("change", edited);
  return result;
}

function timecode(ms) {
  const seconds = Math.floor(ms / 1000);
  return [Math.floor(seconds / 60), seconds % 60].map(n => String(n).padStart(2, "0")).join(":") +
    "." + String(ms % 1000).padStart(3, "0");
}

function reviewNotes(assembly, player, items, activeNote, editNote) {
  const endpoint = path("/assemblies/" + assembly.id + "/notes");
  const list = stack();
  const count = node("small", {role: "status"});
  const filter = select([["ALL", tr("All notes", "Todos os comentários")],
    ["OPEN", tr("Open notes", "Comentários em aberto")], ["RESOLVED", tr("Resolved notes", "Comentários resolvidos")]]);
  filter.setAttribute("aria-label", tr("Filter review notes", "Filtrar comentários"));
  function redraw() {
    count.textContent = items.filter(item => item.state === "OPEN").length + " " + tr("open", "em aberto");
    filter.hidden = !items.length;
    const visible = items.filter(item => filter.value === "ALL" || item.state === filter.value)
      .sort((a, b) => a.position_ms - b.position_ms || a.created - b.created || a.id.localeCompare(b.id));
    list.replaceChildren(...(visible.length ? visible.map(note => {
      const resolved = note.state === "RESOLVED";
      const position = timecode(note.position_ms);
      const events = node("ul");
      const history = node("details", {ontoggle: e => {
        if (!e.currentTarget.open || events.childElementCount) return;
        execute(async () => {
          const audit = await api(endpoint + "/" + note.id);
          events.replaceChildren(...audit.events.map(event => node("li", {},
            (event.state === "RESOLVED" ? tr("Resolved by ", "Resolvido por ") : tr("Reopened by ", "Reaberto por ")) +
            event.actor + " · " + new Date(event.created * 1000).toLocaleString())));
        });
      }}, node("summary", {}, tr("Note history", "Histórico do comentário")), events);
      return node("article", {class: "review-note", tabindex: -1, id: "review-note-" + note.id,
        "aria-current": activeNote === note.id ? "true" : null},
      row(button(position, () => {
        player.pause();
        seekAssembly(player, note.position_ms / 1000);
        player.scrollIntoView({block: "nearest"});
        player.focus({preventScroll: true});
      }), small(resolved ? tr("Resolved", "Resolvido") : tr("Open", "Em aberto"))),
      node("p", {class: "review-note-text"}, note.body),
      small(note.actor + " · " + new Date(note.created * 1000).toLocaleString()),
      note.state_version > 0 && history,
      row(button(resolved ? tr("Reopen", "Reabrir") : tr("Resolve", "Resolver"), async () => {
        const next = await api(endpoint + "/" + note.id + "/state", "PUT",
          {state: resolved ? "OPEN" : "RESOLVED", expected_version: note.state_version});
        items = items.map(item => item.id === next.id ? next : item);
        redraw();
        (ui("review-note-" + note.id) || filter).focus({preventScroll: true});
        }), editNote?.(note), button(tr("Copy timestamp link", "Copiar link do trecho"), async () => {
        await navigator.clipboard.writeText(new URL(note.url, location.origin).href);
        notify(tr("Timestamp link copied", "Link do trecho copiado"));
      })));
    }) : [node("p", {class: "muted"}, tr("No notes here.", "Nenhum comentário nesta lista."))]));
  }
  filter.addEventListener("change", redraw);
  const add = button(tr("Comment at current time", "Comentar neste ponto"), () => {
    player.pause();
    const position = Math.min(Math.round(player.currentTime * 1000), Math.round(assembly.data.seconds * 1000));
    const requestId = crypto.randomUUID().replaceAll("-", "");
    const text = area("", {maxlength: 4000});
    text.addEventListener("input", () => text.removeAttribute("aria-invalid"));
    modal(tr("Note at ", "Comentário em ") + timecode(position),
      label(tr("Review note", "Comentário de revisão"), text), [
        button(tr("Save note", "Salvar comentário"), async () => {
          if (!text.value.trim()) {
            text.setAttribute("aria-invalid", "true");
            text.focus();
            throw new Error(tr("Write a review note", "Escreva um comentário de revisão"));
          }
          const saved = await api(endpoint, "POST", {request_id: requestId, position_ms: position, body: text.value});
          items = [...items.filter(item => item.id !== saved.id), saved];
          filter.value = "ALL";
          activeNote = saved.id;
          ui("dialog").close();
          redraw();
          ui("review-note-" + saved.id)?.focus();
        }, true),
      ]);
    text.focus();
  }, true);
  redraw();
  return {add, panel: card(row(node("h2", {}, tr("Review notes", "Comentários da montagem")), count, filter), list)};
}

async function deliveryView() {
  const target = projectId;
  const endpoint = path("/delivery");
  const query = new URLSearchParams(location.search);
  const requested = query.get("assembly");
  const latest = current("assembly"), exported = current("export");
  const linked = requested && project.versions.find(item => item.id === requested && item.kind === "assembly" && item.url);
  const assembly = linked || latest;
  const historic = linked && linked.id !== latest?.id;
  const [plan, annotation] = await Promise.all([
    historic ? null : api(endpoint),
    assembly?.url ? api(path("/assemblies/" + assembly.id + "/notes")) : null,
  ]);
  if (target !== projectId) return null;
  const matching = plan?.revision && assembly?.fresh && assembly.data.timeline_revision === plan.revision;
  const final = matching && !assembly.data.watermark;
  const delivered = final && exported?.fresh && exported.data.timeline_revision === plan.revision;
  const player = assemblyPlayer(assembly);
  const timestamp = query.get("t");
  const marked = linked && annotation?.notes.find(note => note.id === query.get("note"));
  const position = marked?.position_ms ?? (/^\d{1,10}$/.test(timestamp || "") ? Number(timestamp) : null);
  if (player && linked && position !== null) seekAssembly(player, position / 1000);
  const editNote = matching ? note => {
    const time = note.position_ms / 1000;
    const scene = plan.scenes.find((item, i) => item.start <= time &&
      (time < item.end || (i === plan.scenes.length - 1 && time === item.end)));
    if (!scene) return null;
    return button(tr("Edit this moment", "Editar cena deste trecho"), async () => {
      await refresh(false);
      if (target !== projectId || page !== "delivery") return;
      reviewEdit = {project: target, revision: plan.revision, scene: scene.id, offset: time - scene.start, note};
      navigate("timeline");
    });
  } : null;
  const feedback = player && annotation && reviewNotes(assembly, player, annotation.notes, marked?.id, editNote);
  const previous = historic || (assembly && (!assembly.fresh || (assembly.data.timeline_revision && !matching)));
  const preview = player && card(node("div", {class: "preview-stage"}, player), row(assetBadge(assembly),
    small(tr("Assembly v", "Montagem v") + assembly.seq),
    matching && small(tr("Saved cut v", "Corte salvo v") + plan.seq),
    previous && small(tr("Earlier assembly", "Montagem anterior"))),
    node("div", {class: "row review-actions"}, assemblyScenePicker(assembly, player), feedback?.add));
  const files = (item, currentDelivery) => item && row(
    node("a", {href: item.url + "?attachment=true"}, currentDelivery ?
      tr("Download final MP4", "Baixar MP4 final") : tr("Download existing MP4", "Baixar MP4 existente")),
    ...Object.entries(item.data.captions || {}).map(([language, id]) => node("a",
      {href: path("/media/" + id + "?attachment=true")}, language + " · SRT")));
  const editor = button(tr("Open editor", "Abrir editor"), () => navigate("timeline"));
  const heading = stack(title(tr("Delivery review", "Revisão da entrega"), editor),
    requested && !linked && node("p", {role: "status"}, tr(
      "This assembly is unavailable in the project.", "A montagem do link está indisponível neste projeto.")));
  if (historic) return stack(title(tr("Review assembly v", "Revisão da montagem v") + assembly.seq,
    node("a", {href: "/?project=" + target + "&view=delivery"}, tr("Current delivery", "Entrega atual"))),
    preview, feedback?.panel);
  if (plan.blocked) return stack(heading, preview, card(node("p", {role: "status"}, plan.blocked),
    files(exported, false)), feedback?.panel);

  const materialNames = {scene: ["Storyboard", "Storyboard"], image: ["Reference frame", "Quadro de referência"],
    video: ["Take", "Tomada"], voice: ["Narration", "Narração"], translation: ["Translation", "Tradução"],
    reference: ["Reference", "Referência"], audio: ["Audio", "Áudio"]};
  const pending = scene => scene.materials.some(item => item.status === "AWAITING_REVIEW");
  const incomplete = plan.scenes.filter(pending);
  const materialIds = materials => [...new Set(materials.map(item => item.id))];
  const active = project.jobs.find(job => ["assembly", "export"].includes(job.operation) &&
    ["DRAFT", "GENERATING"].includes(job.status));
  const requestRevision = matching && assembly.status === "AWAITING_REVIEW" && !active &&
    button(tr("Request changes to this assembly", "Solicitar ajustes nesta montagem"),
      () => review([assembly], "REVISION_REQUESTED"));
  const chapter = scene => button(tr("Go to scene", "Ir ao trecho"), () => {
    seekAssembly(player, scene.start);
    player.scrollIntoView({block: "nearest"});
    player.focus({preventScroll: true});
  });
  const materials = rows => rows.flatMap(scene => scene.materials);
  const approvals = rows => {
    const pendingMaterials = materials(rows).filter(item => item.status === "AWAITING_REVIEW");
    modal(tr("Approve selected materials", "Aprovar materiais selecionados"),
      stack(node("p", {}, tr(
        "Confirm that you reviewed these scenes, including any provisional frames and system narration.",
        "Confirme que você revisou estas cenas, incluindo os quadros provisórios e a voz do sistema onde estiverem presentes.")),
      node("ul", {}, rows.map(scene => node("li", {}, scene.title))),
      small(pendingMaterials.length + " " + tr("versions to approve", "versões a aprovar"))), [
        button(tr("Confirm material approval", "Confirmar aprovação dos materiais"), async () => {
          await api(endpoint + "/materials", "POST", {revision: plan.revision, confirmed: true,
            scenes: rows.map(scene => scene.id), versions: materialIds(materials(rows))});
          ui("dialog").close();
          if (target === projectId) await refresh();
        }, true),
      ]);
  };
  const checklist = card(node("h2", {}, tr("Scene materials", "Materiais das cenas")),
    ...plan.scenes.map(scene => node("details", {}, node("summary", {},
      scene.title + " · " + (pending(scene) ? tr("Awaiting review", "A revisar") : tr("Reviewed", "Revisada"))),
      stack(row(small(formatDuration(scene.start) + " – " + formatDuration(scene.end)),
        matching && player && chapter(scene)),
      ...scene.materials.map(item => {
        const title = tr(...materialNames[item.kind]) + (item.language ? " · " + item.language : "") + " · v" + item.seq;
        const guide = item.provisional && (item.kind === "voice" || item.kind === "image" || item.kind === "video") ?
          small(item.kind === "voice" ? tr("System voice", "Voz do sistema") : tr("Draft material", "Material de rascunho")) : null;
        return row(item.url ? node("a", {href: item.url, target: "_blank", rel: "noopener"}, title) : small(title),
          guide, badge(item.status));
      }),
      pending(scene) && button(tr("Approve scene materials", "Aprovar materiais da cena"), () => approvals([scene]))))));
  let action;
  if (active) {
    action = stack(small(tr("Processing on the server", "Processando no servidor")),
      node("progress", {max: 100, value: active.progress, "aria-label": tr("Delivery progress", "Progresso da entrega")}));
  } else if (delivered) {
    action = files(exported, true);
  } else if (matching && assembly.status === "REVISION_REQUESTED") {
    action = stack(node("p", {role: "status"}, tr("Changes requested for this assembly.", "Ajustes solicitados nesta montagem.")),
      button(tr("Edit the cut", "Editar o corte"), () => navigate("timeline"), true));
  } else if (incomplete.length) {
    action = stack(small(incomplete.length + " " + tr("scenes with materials to review", "cenas com materiais a revisar")),
      button(tr("Approve reviewed materials", "Aprovar materiais revisados"), () => approvals(incomplete), true));
  } else if (final && ready(assembly)) {
    action = button(tr("Prepare approved export", "Preparar exportação aprovada"),
      () => quote([{operation: "export", version: assembly.id}]), true);
  } else if (final && assembly.status === "AWAITING_REVIEW") {
    action = button(tr("Approve final assembly", "Aprovar montagem final"), () => review([assembly], "APPROVED"), true);
  } else {
    action = button(tr("Prepare final assembly", "Preparar montagem final"), async () => {
      const saved = await api(endpoint + "/prepare", "POST", {revision: plan.revision, confirmed: true,
        versions: materialIds([...materials(plan.scenes), ...plan.attachments])});
      if (target !== projectId) return;
      const editBase = project.versions.find(version => version.id === timelineBase);
      if (timelineDraft && json(timelineDraft) === json(editBase?.data)) timelineDraft = null;
      await refresh(false);
      if (target === projectId) await quote([{operation: "assembly", version: saved.id}]);
    }, true);
  }
  const delivery = card(node("h2", {}, tr("Assembly and export", "Montagem e exportação")),
    small([tr("Cut v", "Corte v") + plan.seq, plan.aspect, plan.resolution + "p", plan.languages.join(", ")].join(" · ")),
    action, requestRevision, !delivered && files(exported, false));
  return stack(heading, preview, delivery, feedback?.panel, checklist, assembly && revisions("assembly"), jobHistory());
}

async function youtubeChannelView() {
  const {configured, connection, can_setup, setup_error} = await api("/api/youtube");
  const headings = {
    connected: tr("Channel verified and connected.", "Canal verificado e conectado."),
    mismatch: tr("The chosen channel differs from the configured channel.", "O canal escolhido difere do canal configurado."),
    expired: tr("Open the connection flow again.", "Abra a conexão novamente."),
    cancelled: tr("Google authorization was cancelled.", "A autorização no Google foi cancelada."),
    error: tr("Google authorization failed. Check the OAuth settings.", "A autorização falhou. Confira a configuração OAuth."),
  };
  const connect = mode => async () => {
    const result = await api("/api/youtube/connect", "POST", {mode});
    location.assign(result.url);
  };
  const configure = () => {
    const client = input("", {autocomplete: "off"});
    const secret = input("", {type: "password", autocomplete: "new-password"});
    const channel = input("", {maxlength: 24, autocomplete: "off"});
    const credentials = input("", {type: "file", accept: ".json,application/json"});
    const heading = connection ? tr("Rotate OAuth secret", "Trocar segredo OAuth") :
      tr("Configure YouTube OAuth", "Configurar OAuth do YouTube");
    youtubeConsent(heading,
      stack(small(location.origin + "/api/youtube/callback"),
        label(tr("Google credentials JSON (optional)", "JSON de credenciais do Google (opcional)"), credentials),
        label(tr("Google OAuth client ID", "Client ID OAuth do Google"), client),
        label(tr("Client secret", "Client secret"), secret),
        !connection && label(tr("Expected channel ID (optional)", "ID do canal esperado (opcional)"), channel),
        connection && small(tr("Use a new secret from the same Google OAuth client.",
          "Use um novo segredo do mesmo cliente OAuth do Google."))),
      tr("Save these credentials in this workspace.", "Salvar estas credenciais neste workspace."),
      async () => {
        let values = {client_id: client.value.trim(), client_secret: secret.value.trim()};
        if (credentials.files.length) {
          try {
            const file = credentials.files[0];
            if (file.size > 16384) throw new Error();
            const {web} = JSON.parse(await file.text());
            const callback = location.origin + "/api/youtube/callback";
            if (!web.redirect_uris?.includes(callback) || !web.client_id || !web.client_secret) throw new Error();
            values = {client_id: web.client_id, client_secret: web.client_secret};
          } catch {
            throw new Error(tr("Choose the Google web client JSON for this callback.",
              "Selecione o JSON do cliente web do Google para este endereço de retorno."));
          }
        }
        await api("/api/youtube/setup", "POST", {...values, channel: channel.value.trim() || null, confirmed: true});
        if (connection) notify(tr("New secret verified and saved.", "Novo segredo verificado e salvo."));
      });
  };
  return stack(title(tr(...navLabels.channel)),
    headings[entry.get("youtube")] && (entry.get("youtube") !== "connected" || connection) &&
      node("p", {role: "status"}, headings[entry.get("youtube")]),
    card(connection ? stack(node("h2", {}, connection.title), small(connection.channel),
      small(connection.writable ? tr("Analytics and publishing access", "Leitura e publicação") :
        tr("Read access", "Somente leitura"))) :
      node("p", {}, setup_error ? tr("The stored OAuth configuration could not be opened.",
        "A configuração OAuth salva não pôde ser aberta.") : configured ? tr("Connect the Google account that manages your channel.",
        "Conecte a conta Google que administra o canal.") :
        tr("YouTube OAuth is awaiting server configuration.", "A configuração OAuth do YouTube está pendente no servidor.")),
    can_setup && button(connection ? tr("Rotate OAuth secret", "Trocar segredo OAuth") :
      tr("Configure OAuth client", "Configurar cliente OAuth"), configure, !configured),
    configured && row(!connection && button(tr("Connect for analytics", "Conectar para leitura"), connect("read")),
      !connection?.writable && button(tr("Connect for publishing", "Conectar para publicar"), connect("publish"), true),
      connection && button(tr("Enable impressions and CTR", "Ativar impressões e CTR"), () =>
        youtubeConsent(tr("Enable YouTube reach reports", "Ativar relatórios de alcance"),
          node("p", {}, tr("YouTube makes a daily report for the connected channel.",
            "O YouTube prepara um relatório diário para o canal conectado.")),
          tr("Enable the report for this channel.", "Ativar o relatório para este canal."),
          async () => { await api("/api/youtube/reach", "POST", {confirmed: true}); notify(tr("Reach reporting enabled", "Relatórios de alcance ativados")); })),
      connection && button(tr("Disconnect", "Desconectar"), () => modal(
        tr("Disconnect YouTube", "Desconectar o YouTube"),
        node("p", {}, tr("Revoke the connection and remove the imported data from this workspace?",
          "Revogar a conexão e remover do workspace os dados importados?")),
        [button(tr("Confirm disconnection", "Confirmar desconexão"), async () => {
          await api("/api/youtube", "DELETE");
          ui("dialog").close();
          render();
        }, true)]))),
    node("a", {href: "/youtube-privacy", target: "_blank", rel: "noopener"},
      tr("YouTube integration privacy", "Privacidade da integração com o YouTube"))));
}

function youtubeConsent(heading, content, labelText, submit) {
  const consent = input("", {type: "checkbox"});
  modal(heading, stack(content, label(labelText, consent)), [
    button(tr("Confirm", "Confirmar"), async () => {
      if (!consent.checked) throw new Error(tr("Confirm this YouTube action", "Confirme esta ação no YouTube"));
      await submit();
      ui("dialog").close();
      render();
    }, true),
  ]);
}

async function youtubeView() {
  const heading = title(tr(...navLabels.youtube));
  const exports = project.versions.filter(item => item.kind === "export" && item.media);
  if (!exports.length) return stack(heading, node("p", {}, tr(
    "Export an approved cut to use this panel.", "Exporte um corte aprovado para usar este painel.")));
  const selected = exports.find(item => item.id === youtubeVersion) || exports[0];
  const chooser = select(exports.map(item => [item.id, tr("Export", "Exportação") + " v" + item.seq +
    " · " + item.data.seconds + " s"]), selected.id);
  chooser.addEventListener("change", () => { youtubeVersion = chooser.value; render(); });
  const target = path("/youtube/" + selected.id);
  const {connection, publication, metrics} = await api(target);
  const selection = label(tr("Exact export", "Exportação exata"), chooser);
  if (!connection) return stack(heading, selection, button(tr("Connect YouTube channel", "Conectar canal do YouTube"),
    () => navigate("channel"), true));
  const channel = small(connection.title + " · " + connection.channel);
  const action = (endpoint, data) => api(target + "/" + endpoint, "POST", data);
  const update = endpoint => async () => { await action(endpoint); render(); };
  const isCurrent = selected.current && selected.fresh;
  const link = () => {
    const id = input("", {maxlength: 11, placeholder: "YouTube ID"});
    youtubeConsent(tr("Link an existing video", "Vincular vídeo existente"), label(tr("YouTube video ID", "ID do vídeo"), id),
      tr("This video belongs to the selected export.", "Este vídeo corresponde à exportação selecionada."),
      () => action("link", {video: id.value.trim(), confirmed: true}));
  };
  const upload = () => {
    const language = selected.data.languages[0];
    const meta = selected.data.metadata?.[language] || {};
    const name = input(meta.title || project.data.profile?.public_title || project.data.title, {maxlength: 100});
    const description = area(meta.description || "", {maxlength: 5000, rows: 5});
    const category = input("27", {inputmode: "numeric", maxlength: 3});
    const choice = [["", tr("Choose", "Selecione")], ["yes", tr("Yes", "Sim")], ["no", tr("No", "Não")]];
    const kids = select(choice), synthetic = select(choice);
    youtubeConsent(tr("Upload as private", "Enviar como privado"),
      stack(channel, label(tr("Title", "Título"), name), label(tr("Description", "Descrição"), description),
        label(tr("YouTube category ID", "ID da categoria no YouTube"), category),
        label(tr("Made for kids?", "Conteúdo feito para crianças?"), kids),
        label(tr("Contains realistic altered or synthetic content?", "Contém material realista alterado ou sintético?"), synthetic)),
      tr("Upload this exact export as private.", "Enviar esta versão exata como privada."),
      async () => {
        if (!kids.value || !synthetic.value) throw new Error(tr("Choose the audience and disclosure.", "Informe o público e a declaração de conteúdo."));
        await action("upload", {channel: connection.channel, title: name.value, description: description.value, category: category.value,
          made_for_kids: kids.value === "yes", synthetic: synthetic.value === "yes", confirmed: true});
      });
  };
  const video = publication?.video;
  const prepared = publication?.processing === "processed" && ["UPLOADED", "LINKED"].includes(publication.state);
  const publish = () => {
    const when = input("", {type: "datetime-local"});
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    youtubeConsent(tr("Approve YouTube publication", "Aprovar publicação no YouTube"), stack(channel,
      small(tr("Video", "Vídeo") + " · " + video),
      label(tr("Schedule in " + zone + " (leave empty to publish now)", "Agendar em " + zone + " (vazio para publicar agora)"), when)),
      tr("I authorize making this video public.", "Autorizo tornar este vídeo público."),
      () => action("publish", {video, publish_at: when.value ? new Date(when.value).toISOString() : null, confirmed: true}));
  };
  const assets = () => {
    const thumb = select([["", tr("Skip thumbnail", "Sem miniatura")], ...project.versions.filter(
      item => item.kind === "thumbnail" && ready(item)).map(item => [item.id, item.data.headline || item.slot])]);
    const captions = Object.keys(selected.data.captions || {}).map(language => [language, input("", {type: "checkbox"})]);
    youtubeConsent(tr("Send publication assets", "Enviar materiais da publicação"),
      stack(channel, label(tr("Approved thumbnail", "Miniatura aprovada"), thumb),
        ...captions.map(([language, field]) => label(tr("Captions", "Legendas") + " · " + language, field))),
      tr("Send the selected files to this video.", "Enviar os arquivos selecionados para este vídeo."),
      () => action("assets", {video, thumbnail: thumb.value || null,
        captions: captions.filter(([, field]) => field.checked).map(([language]) => language), confirmed: true}));
  };
  const stateLabels = {
    QUEUED: ["Queued", "Na fila"], UPLOADING: ["Uploading", "Enviando"], PAUSED: ["Paused", "Pausado"],
    UPLOADED: ["Video ID recorded", "ID do vídeo registrado"], LINKED: ["Linked", "Vinculado"],
    DETACHED: ["Disconnected", "Desconectado"], VISIBILITY_PENDING: ["Confirming visibility", "Confirmando visibilidade"],
    VISIBILITY_UNKNOWN: ["Needs a status check", "Precisa verificar no YouTube"],
  };
  const status = publication && stack(small(tr(...stateLabels[publication.state])),
    publication.last_error && node("p", {role: "status"}, publication.last_error),
    publication.state === "UPLOADING" && node("progress", {max: publication.bytes, value: publication.received,
      "aria-label": tr("Upload progress", "Progresso do envio")}),
    video && node("a", {href: "https://studio.youtube.com/video/" + video + "/edit",
      target: "_blank", rel: "noopener noreferrer"}, tr("Open in YouTube Studio", "Abrir no YouTube Studio")),
    video && small([publication.visibility, publication.processing,
      publication.scheduled && new Date(publication.scheduled).toLocaleString()].filter(Boolean).join(" · ")));
  const metricsPanel = video && ["public", "unlisted"].includes(publication.visibility) &&
    youtubeMetricsPanel(metrics, days => update("metrics/" + days)(), days => update("reach/" + days)());
  return stack(heading, selection, card(channel, status, row(
    !publication && connection.writable && isCurrent && button(tr("Upload as private", "Enviar como privado"), upload, true),
    (!publication || ["PAUSED", "DETACHED"].includes(publication.state)) && button(tr("Link existing video", "Vincular vídeo existente"), link),
    publication?.state === "PAUSED" && connection.writable && isCurrent && button(tr("Resume upload", "Retomar envio"), update("resume")),
    publication && ["QUEUED", "UPLOADING"].includes(publication.state) &&
      button(tr("Pause upload", "Pausar envio"), update("cancel")),
    publication && !video && button(tr("Refresh receipt", "Atualizar recibo"), render),
    video && button(tr("Check YouTube status", "Consultar estado no YouTube"), update("refresh")),
    prepared && connection.writable && isCurrent && button(tr("Send thumbnail and captions", "Enviar miniatura e legendas"), assets),
    prepared && connection.writable && isCurrent && publication.visibility === "private" && !publication.scheduled &&
      button(tr("Approve and publish", "Aprovar e publicar"), publish, true))), metricsPanel);
}

function youtubeMetricsPanel(metrics, sync, reach) {
  const names = {
    views: ["Views", "Visualizações"], engagedViews: ["Engaged views", "Visualizações engajadas"],
    estimatedMinutesWatched: ["Watch time (minutes)", "Tempo assistido (minutos)"],
    averageViewDuration: ["Average duration (s)", "Duração média (s)"],
    averageViewPercentage: ["Average percentage viewed", "Percentual médio assistido"],
  };
  const metricList = totals => node("dl", {}, Object.entries(totals || {}).flatMap(([key, value]) =>
    names[key] ? [node("dt", {}, tr(...names[key])), node("dd", {}, String(value))] : []));
  return card(node("h2", {}, tr("Official YouTube data", "Dados oficiais do YouTube")),
    row(...[2, 7, 28].map(days => button(tr("Import", "Importar") + " D" + days, () => sync(days)))),
    ...metrics.map(({days, data}) => node("details", {}, node("summary", {}, "D" + days + " · " + data.start +
      " – " + data.end + (data.complete ? "" : " · " + tr("Partial", "Parcial"))),
      small("YouTube Analytics · America/Los_Angeles"), metricList(data.totals),
      button(tr("Import impressions and CTR", "Importar impressões e CTR"), () => reach(days)),
      data.reach && node("details", {}, node("summary", {}, tr("Reach by date and origin", "Alcance por data e origem") +
        " · " + data.reach.days_available + " " + tr("days available", "dias disponíveis")),
        small(tr("Raw YouTube Reporting data · up to 30 rows in this view",
          "Dados da YouTube Reporting API · até 30 linhas nesta tela")),
        node("ul", {}, data.reach.rows.slice(0, 30).map(item => node("li", {}, [
          item.date, item.traffic_source_type, item.traffic_source_detail, item.device_type,
          item.video_thumbnail_impressions + " " + tr("impressions", "impressões"),
          "CTR " + item.video_thumbnail_impressions_ctr,].filter(Boolean).join(" · "))))),
      data.traffic?.length ? node("ul", {}, data.traffic.map(item =>
        node("li", {}, item.insightTrafficSourceType + " · " + item.views + " " + tr("views", "visualizações")))) : null,
      data.retention?.length ? node("details", {}, node("summary", {}, tr("Audience retention points", "Pontos de retenção")),
        node("ul", {}, data.retention.map(point => node("li", {},
          String(point.elapsedVideoTimeRatio) + " · " + String(point.audienceWatchRatio))))) : null)));
}

const performanceMetrics = {
  views: ["Views", "Visualizações"], ctr: ["Impressions CTR", "CTR de impressões"],
  retention_30: ["Retention at 30 seconds", "Retenção aos 30 segundos"],
  average_viewed: ["Average percentage viewed", "Percentual médio assistido"],
  stayed: ["Stayed to watch", "Continuaram assistindo"],
};
const trafficSources = {
  total: ["Total", "Total"], browse: ["Browse", "Recursos de navegação"],
  suggested: ["Suggested videos", "Vídeos sugeridos"], search: ["Search", "Pesquisa"],
  shorts_feed: ["Shorts feed", "Feed de Shorts"], external: ["External", "Externo"],
};
const localizedOptions = entries => Object.entries(entries).map(([key, labels]) => [key, tr(...labels)]);

async function experimentsView() {
  const heading = title(tr(...navLabels.analysis));
  const available = project.versions.filter(item => ["assembly", "export"].includes(item.kind) && item.media);
  if (!available.length) return stack(heading, card(node("p", {}, tr(
    "Render a cut to attach a hypothesis to its exact version.",
    "Renderize um corte para vincular a hipótese à sua versão."))));
  const cut = available.find(item => item.id === experimentVersion) ||
    available.find(item => item.kind === "export") || available[0];
  const chooser = select(available.map(item => [item.id,
    (item.kind === "export" ? tr("Export", "Exportação") : tr("Cut", "Corte")) + " v" + item.seq + " · " + item.data.seconds + " s"]),
    cut.id);
  chooser.addEventListener("change", () => {
    experimentVersion = chooser.value;
    render();
  });
  const target = path("/experiments/" + cut.id);
  const record = await api(target);
  const youtubeLink = cut.kind === "export" && button(
    tr("Open official YouTube data", "Abrir dados oficiais do YouTube"),
    () => { youtubeVersion = cut.id; navigate("youtube"); });
  const plan = record.plan?.data || {};
  const hypothesis = area(plan.hypothesis || "", {required: true, rows: 3, maxlength: 8000});
  const variable = input(plan.variable || "", {required: true, maxlength: 200});
  const control = area(plan.control || "", {required: true, rows: 2, maxlength: 8000});
  const isShort = record.format.short;
  const metrics = localizedOptions(performanceMetrics).filter(([key]) =>
    (key !== "stayed" || isShort) && (key !== "retention_30" || (!isShort && record.format.seconds >= 30)));
  const metric = select(metrics, plan.primary_metric || (isShort ? "stayed" : "views"));
  const saveForm = (fields, text, save) => node("form", {onsubmit: event => {
    event.preventDefault();
    execute(save, event.submitter);
  }}, ...fields, node("button", {type: "submit", class: "primary"}, text));
  const hypothesisCard = card(node("h2", {}, tr("Hypothesis", "Hipótese")),
    record.observations.length ? stack(node("p", {}, plan.hypothesis),
      small(tr("Variable", "Variável") + " · " + plan.variable),
      small(tr("Control", "Controle") + " · " + plan.control),
      small(tr("Primary metric", "Métrica principal") + " · " + tr(...performanceMetrics[plan.primary_metric]))) :
      saveForm([label(tr("What do you predict?", "O que você espera que aconteça?"), hypothesis),
        label(tr("What changes?", "O que muda?"), variable),
        label(tr("What stays fixed?", "O que fica fixo?"), control),
        label(tr("Primary metric", "Métrica principal"), metric)],
      tr("Save hypothesis", "Salvar hipótese"), () => mutate(target, "PUT", {
        hypothesis: hypothesis.value, variable: variable.value, control: control.value, primary_metric: metric.value,
      })));

  const example = record.observations[0]?.data;
  const youtube = input(example?.youtube_id || "", {required: true, pattern: "[A-Za-z0-9_-]{11}", maxlength: 11, readonly: Boolean(example)});
  const days = select(["2", "7", "28"].map(value => [value, "D" + value]), "7");
  const traffic = select(localizedOptions(trafficSources), "total");
  const views = input("", {type: "number", min: 0, step: 1, required: true});
  const note = area("", {rows: 2, maxlength: 8000});
  const optional = metrics.filter(([key]) => key !== "views").map(([key, name]) =>
    [key, name, input("", {type: "number", min: 0, max: key === "average_viewed" ? 10000 : 100, step: "any"})]);
  const impressions = input("", {type: "number", min: 0, step: 1});
  const numeric = field => field.value === "" ? null : field.valueAsNumber;
  const observationsForm = record.plan && cut.kind === "export" && card(node("h2", {}, tr("Record Studio data", "Registrar dados do Studio")),
    small(tr("Use the first days after publication and only figures from the selected traffic source.",
      "Use os primeiros dias após a publicação e somente números do recorte de tráfego escolhido.")),
    saveForm([label(tr("YouTube video ID", "ID do vídeo no YouTube"), youtube),
      row(label(tr("Window since publication", "Janela desde a publicação"), days),
        label(tr("Traffic", "Origem do tráfego"), traffic)),
      row(label(tr("Views", "Visualizações"), views), label(tr("Impressions", "Impressões"), impressions)),
      row(...optional.map(([, name, field]) => label(name + " (%)", field))),
      label(tr("Observation", "Observação"), note)], tr("Save observation", "Salvar observação"),
    () => mutate(target + "/observations", "POST", {
      youtube_id: youtube.value, days: Number(days.value), traffic: traffic.value, views: views.valueAsNumber,
      impressions: numeric(impressions), note: note.value.trim() || null,
      ...Object.fromEntries(optional.map(([key, , field]) => [key, numeric(field)])),
    })));

  const number = new Intl.NumberFormat(locale === "pt" ? "pt-BR" : "en-US", {maximumFractionDigits: 2});
  const value = (amount, suffix) => amount === null ? tr("Unavailable", "Indisponível") : number.format(amount) + suffix;
  const results = record.observations.map(item => {
    const data = item.data, comparison = record.comparisons[item.id];
    const units = comparison.metric === "views" ? "" : "%";
    return card(node("h3", {}, "D" + data.days + " · " + tr(...trafficSources[data.traffic])),
      node("p", {}, tr(...performanceMetrics[comparison.metric]) + " · " + value(comparison.value, units)),
      small(number.format(data.views) + " " + tr("views", "visualizações") +
        (data.impressions === null ? "" : " · " + number.format(data.impressions) + " " + tr("impressions", "impressões"))),
      ...metrics.filter(([key]) => key !== "views" && key !== comparison.metric && data[key] !== null)
        .map(([key, name]) => small(name + " · " + value(data[key], "%"))),
      comparison.peers.length ? node("details", {}, node("summary", {},
        tr("Comparable median", "Mediana comparável") + " " + value(comparison.median, units) + " · n=" + comparison.peers.length),
        comparison.delta !== null && node("p", {}, tr("Difference", "Diferença") + " " +
          value(comparison.delta, units ? " p.p." : "")),
        node("ul", {}, comparison.peers.map(peer => node("li", {}, peer.title + " · " +
          value(peer.value, units) + " · " + peer.experiment.variable)))) :
        small(tr("No comparable observations yet.", "Ainda faltam observações comparáveis.")),
      data.note && node("p", {}, data.note));
  });

  const observation = select([["", tr("Cut and hypothesis only", "Apenas corte e hipótese")],
    ...record.observations.map(item => [item.id, "D" + item.data.days + " · " + tr(...trafficSources[item.data.traffic])])],
    record.observations[0]?.id || "");
  const reports = project.versions.filter(item => item.slot === "analysis/" + cut.id);
  const work = project.jobs.some(job => job.slot === "analysis/" + cut.id && ["DRAFT", "GENERATING"].includes(job.status));
  const reportLabels = {
    content_findings: ["Content findings", "Análise do conteúdo"], hypothesis_readout: ["Hypothesis readout", "Leitura da hipótese"],
    limitations: ["Limitations", "Limitações"], next_test: ["Next test", "Próximo teste"],
  };
  const analysis = record.plan && card(node("h2", {}, tr("Codex analysis", "Análise com Codex")),
    small(tr("Uses narration, storyboard, scene timing and the selected figures.",
      "Usa a narração, o storyboard, os tempos de cena e os dados selecionados.")),
    label(tr("Data for analysis", "Dados para análise"), observation),
    button(tr("Review analysis quote", "Revisar cotação da análise"), () => quote([{
      operation: "analysis", provider: "codex", version: cut.id, observation: observation.value || null,
    }]), true, work),
    ...reports.map(report => node("details", {open: report.current},
      node("summary", {}, tr("Analysis", "Análise") + " v" + report.seq + " · " +
        (report.fresh ? new Date(report.created * 1000).toLocaleString() : tr("Earlier inputs", "Dados anteriores"))),
      ...Object.entries(reportLabels).flatMap(([key, name]) =>
        [node("h3", {}, tr(...name)), node("p", {}, report.data[key])]))));
  return stack(heading, row(label(tr("Exact cut or export", "Corte ou exportação analisada"), chooser), youtubeLink),
    grid(hypothesisCard, observationsForm), analysis,
    results.length > 0 && card(node("h2", {}, tr("Observed performance", "Desempenho observado")),
      small(tr("Comparisons use similar duration, same series, format, language, window and traffic.",
        "A comparação usa duração próxima, mesma série, formato, idioma, janela e tráfego.")), grid(...results)),
    jobHistory());
}

function jobHistory() {
  const needsAttention = project.jobs.some(job => isActiveJob(job) || job.uncertain > 0);
  return node("details", {open: needsAttention}, node("summary", {},
    tr("Job history", "Histórico de trabalhos") + " (" + project.jobs.length + ")"), jobsTable());
}

const isActiveJob = job => ["DRAFT", "GENERATING"].includes(job.status);
const canResumeRunway = job => job.provider === "runway" && job.remote && job.status === "FAILED";
const jobNames = {
  rough_cut: ["First draft", "Primeiro rascunho"], image: ["Image", "Imagem"],
  video: ["Video", "Vídeo"], voice: ["Narration", "Narração"], translation: ["Translation", "Tradução"],
  assembly: ["Assembly", "Montagem"], export: ["Export", "Exportação"],
};
const jobMessages = {
  "Provider polling timed out. Reconcile the recorded task.":
    ["The provider status check timed out.", "A consulta ao provedor excedeu o tempo de espera."],
  "Provider response was interrupted. Verify billing before trying again.":
    ["The provider response was interrupted.", "A resposta do provedor foi interrompida."],
  "Interrupted during provider submission. Verify billing.":
    ["The submission was interrupted.", "O envio da solicitação foi interrompido."],
  "Cancelled before execution": ["Cancelled before execution", "Cancelado antes da execução"],
};

function jobsTable(requested = null) {
  const currency = project.costs.currency;
  const activeSlots = new Set(project.jobs.filter(isActiveJob).map(job => job.slot));
  const priority = job => job.id === requested ? -1 :
    isActiveJob(job) ? 0 : job.uncertain > 0 || canResumeRunway(job) ? 1 : 2;
  const jobs = [...project.jobs].sort((a, b) => priority(a) - priority(b)).slice(0, 40);
  return card(row(node("h2", {}, tr("Jobs", "Trabalhos")), button(tr("Refresh", "Atualizar"), refresh)),
    requested && !jobs.some(job => job.id === requested) &&
      node("p", {role: "status"}, tr("This job is unavailable in the current project.", "Este trabalho não está disponível no projeto atual.")),
    ...jobs.map(job => {
      const active = isActiveJob(job);
      const resume = canResumeRunway(job);
      const slotBusy = resume && activeSlots.has(job.slot);
      const endpoint = path("/jobs/" + job.id);
      const confirmSettlement = () => {
        const amount = input("", {type: "number", min: 0, step: ".01"});
        const receipt = input("", {placeholder: tr("Provider invoice or receipt reference", "Referência da fatura ou recibo")});
        modal(tr("Record verified charge", "Registrar cobrança verificada"), stack(
          node("p", {}, tr("Use the amount confirmed by the provider. Enter zero if the provider confirms no charge.",
            "Use o valor confirmado pelo provedor. Informe zero se o provedor confirmar que não houve cobrança.")),
          label(tr("Actual charge", "Cobrança efetiva") + " (" + currency + ")", amount),
          label(tr("Receipt reference", "Referência do recibo"), receipt)), [
          button(tr("Confirm charge", "Confirmar cobrança"), async () => {
            if (!amount.value || !amount.checkValidity() || !receipt.value.trim()) {
              throw new Error(tr("Enter a verified amount and receipt", "Informe um valor verificado e um recibo"));
            }
            await mutate(endpoint + "/settle", "POST",
              {actual: Math.round(Number(amount.value) * 100), receipt: receipt.value, confirmed: true});
            ui("dialog").close();
          }, true),
        ]);
      };
      const cancelJob = async () => {
        if (!job.remote) return mutate(endpoint + "/cancel", "POST");
        modal(tr("Cancel provider task?", "Cancelar tarefa no provedor?"),
          node("p", {}, tr("Cancellation will be requested for this task. Check the provider for the final charge.",
            "O cancelamento será solicitado para esta tarefa. Confira a cobrança final no provedor.")),
          [button(tr("Confirm cancellation", "Confirmar cancelamento"), async () => {
            await mutate(endpoint + "/cancel", "POST");
            ui("dialog").close();
          }, true)]);
      };
      const operation = jobNames[job.operation] || navLabels[job.operation] || [job.operation, job.operation];
      const message = jobMessages[job.last_error];
      const subject = job.slot.split("/")[1];
      const scene = subject && sceneData(subject)?.data.title;
      const info = [job.provider, job.model, active ? job.progress + "%" : null].filter(Boolean).join(" · ");
      const heading = [tr(...operation), scene].filter(Boolean).join(" · ");
      const result = job.result && project.versions.find(version => version.id === job.result);
      const focused = job.id === requested;
      return node("section", {id: "job-" + job.id, class: "card stack job-details", tabindex: focused ? "-1" : null,
        "aria-label": focused ? heading : null},
      row(node("h3", {}, heading), badge(job.status), small(info)),
      active && Boolean(job.cancel) && small(tr("Cancellation requested", "Cancelamento solicitado")),
      job.reserved > 0 && small(tr("Reserved · ", "Reservado · ") + format(job.reserved, currency)),
      job.uncertain > 0 && node("p", {}, tr("Charge to verify · ", "Cobrança a verificar · ") + format(job.uncertain, currency),
        (job.status === "FAILED" || job.status === "CANCELLED") && " " + tr(
          "Check the provider's billing history before authorizing another attempt.",
          "Confira o histórico de cobranças do provedor antes de autorizar outra tentativa.")),
      job.settlement && small(tr("Verified charge · ", "Cobrança verificada · ") + format(job.settlement.actual, currency) +
        " · " + tr("Receipt ", "Recibo ") + job.settlement.receipt),
      job.remote && small(tr("Provider task · ", "Tarefa no provedor · ") + job.remote),
      resume && small(slotBusy ?
        tr("Wait for the active job for this asset to finish.", "Aguarde o trabalho ativo para esta tomada terminar.") :
        tr("Resume checking the original Runway task.", "Retome a consulta da tarefa original no Runway.")),
      job.last_error && !active && node("p", {}, message ? tr(...message) : job.last_error),
      row(active && !job.cancel && button(tr("Cancel", "Cancelar"), cancelJob),
        result?.url && node("a", {href: result.url, target: "_blank", rel: "noopener"},
          tr("Open result · v", "Abrir resultado · v") + result.seq),
        resume && !slotBusy && button(tr("Resume Runway task check", "Retomar consulta no Runway"),
          () => mutate(endpoint + "/reconcile", "POST"), true),
        job.uncertain > 0 && button(tr("Record verified charge", "Registrar cobrança verificada"), confirmSettlement),
        button(tr("View authorization", "Ver autorização"), async () => {
          const authorization = await api(path("/quotes/" + job.quote));
          modal(tr("Recorded authorization", "Autorização registrada"), stack(node("p", {},
            authorization.actor, " · ", format(authorization.maximum), " · ",
            new Date(authorization.authorized_at * 1000).toLocaleString()), node("pre", {}, json(authorization.requests))));
        })));
    }), !project.jobs.length && small(tr("No jobs yet", "Nenhum trabalho ainda")));
}

const thumbnailConcepts = {
  impact: ["A · Main consequence", "A · Consequência principal"],
  contrast: ["B · Visual contrast", "B · Contraste visual"],
  detail: ["C · Curiosity detail", "C · Detalhe intrigante"],
};

function thumbnailsView() {
  const vertical = project.data.style.aspect === "9:16";
  const language = project.data.languages[0];
  const videoTitle = current("timeline")?.data.metadata?.[language]?.title ||
    project.data.profile?.public_title || project.data.title;
  const references = project.versions.filter(v => v.current && ready(v) && ["reference", "image"].includes(v.kind));
  const imageOptions = [["", tr("Built-in graphic", "Arte gráfica local")], ...references.map(v => [
    v.id, v.data.title || sceneData(v.slot.split("/")[1])?.data.title || v.slot,
  ])];
  const cards = [];
  const previews = grid();
  const forms = Object.entries(thumbnailConcepts).map(([concept, labels], index) => {
    const slot = "thumbnail/" + concept;
    const saved = current(slot);
    const text = input(saved?.data.headline ?? (index === 0 ? project.data.editorial?.thumbnail_text || "" : ""), {maxlength: 64});
    const source = select(imageOptions, saved ? (saved.data.source || "") : (references[0]?.id || ""));
    const secondary = select(imageOptions, saved?.data.comparison || "");
    const focal = select([["left", tr("Left", "Esquerda")], ["center", tr("Center", "Centro")], ["right", tr("Right", "Direita")]], saved?.data.focal || "center");
    const accent = select([["mint", tr("Mint", "Menta")], ["amber", tr("Amber", "Âmbar")], ["blue", tr("Blue", "Azul")]],
      saved?.data.accent || ["mint", "amber", "blue"][index]);
    if (saved) {
      previews.append(card(row(node("h2", {}, tr(...labels)), assetBadge(saved)),
        node("div", {class: "thumbnail-tile"},
          node("img", {src:saved.url, alt:saved.data.headline || tr(...labels)}),
          node("p", {class: "thumbnail-video-title"}, videoTitle)),
        row(node("a", {href:saved.url + "?attachment=true", download:true}, tr("Download JPG", "Baixar JPG")),
          reviewControls(saved)),
        revisions(slot)));
    }
    cards.push(card(node("h2", {}, tr(...labels)), label(tr("Short headline (optional)", "Texto curto (opcional)"), text),
      label(tr("Main image", "Imagem principal"), source),
      concept === "contrast" && label(tr("Comparison image", "Imagem de comparação"), secondary),
      row(label(tr("Crop focus", "Foco do recorte"), focal), label(tr("Accent", "Cor de destaque"), accent))));
    return () => ({concept, headline:text.value, source:source.value || null,
      comparison:concept === "contrast" ? secondary.value || null : null, focal:focal.value, accent:accent.value});
  });
  const canBundle = Object.keys(thumbnailConcepts).some(concept => {
    const item = current("thumbnail/" + concept);
    return item?.fresh && ["APPROVED", "AWAITING_REVIEW"].includes(item.status);
  });
  const mobile = input("", {type: "checkbox"});
  mobile.addEventListener("change", () => previews.classList.toggle("thumbnail-compact", mobile.checked));
  return stack(title(tr("Thumbnails", "Miniaturas"),
    canBundle && node("a", {href:path("/thumbnails/bundle"), download:true}, tr("Download options ZIP", "Baixar opções em ZIP"))),
    node("p", {class: "muted"}, vertical
      ? tr("Compare three vertical covers and choose one manually. Upload it in YouTube Studio on desktop when the option is available for your channel.",
          "Compare três capas verticais e escolha uma manualmente. Envie pelo YouTube Studio no computador quando a opção estiver disponível no seu canal.")
      : tr("Test different visual ideas with the same video title in YouTube Studio. Compare by watch time and check that the opening delivers the thumbnail’s promise.",
          "Teste ideias visuais diferentes com o mesmo título no YouTube Studio. Compare pelo tempo de exibição e confira se a abertura entrega o que a miniatura promete.")),
    previews.childElementCount > 0 && stack(label(tr("Compare at mobile size", "Comparar no tamanho de celular"), mobile), previews),
    grid(...cards),
    row(button(tr("Generate 3 drafts", "Gerar 3 opções"), async () => {
      await mutate(path("/thumbnails"), "POST", {variants: forms.map(read => read())});
      notify(tr("Thumbnail drafts saved for review", "Miniaturas salvas para revisão"));
    }, true)),
    node("p", {class:"muted"}, tr("Use one focal point and text that reads on mobile. The opening should deliver the promise shared by the title and thumbnail.",
      "Use um ponto focal e texto legível no celular. A abertura deve entregar a promessa feita pelo título e pela miniatura.")),
    node("a", {href: vertical ? "https://support.google.com/youtube/answer/72431" : "https://support.google.com/youtube/answer/16391400",
      target:"_blank", rel:"noopener noreferrer"}, tr("YouTube Studio guide", "Guia do YouTube Studio")));
}

function assetView() {
  const kind = select([["reference", tr("Reference image", "Imagem de referência")], ["audio", tr("Music or sound effect", "Trilha ou efeito sonoro")]]);
  const filename = input("", {type: "file", accept: "image/png,image/jpeg,image/webp,audio/wav,audio/mpeg,audio/flac"});
  const name = input("");
  const assets = project.versions.filter(item => item.url);
  return stack(title(tr("Asset library", "Biblioteca")), card(
    row(label(tr("Asset type", "Tipo de recurso"), kind), label(tr("Asset title", "Título do recurso"), name),
      label(tr("Media file", "Arquivo de mídia"), filename)),
    button(tr("Upload for review", "Enviar para revisão"), () => {
      if (!filename.files[0] || !name.value.trim()) throw new Error(tr("Choose a file and enter its title", "Selecione o arquivo e informe o título"));
      return mutate(path("/uploads?kind=" + kind.value + "&title=" + encodeURIComponent(name.value)), "POST", filename.files[0]);
    }, true)),
    grid(...assets.map(item => card(row(node("h3", {}, item.data.title || item.slot.split("/")[0] + " · v" + item.seq), assetBadge(item)),
      mediaPreview(item), ["reference", "audio"].includes(item.kind) && reviewControls(item),
      node("a", {href: item.url + "?attachment=true"}, tr("Download", "Baixar"))))));
}

function costsView() {
  const c = project.costs;
  const scope = select([["project", tr("Project", "Projeto")], ["video", tr("Video", "Vídeo")], ["scene", tr("Scene", "Cena")],
    ["day", tr("Daily", "Diário")], ["month", tr("Monthly", "Mensal")], ["provider", tr("Provider", "Provedor")]]);
  const target = select([["", ""]]);
  const targetLabel = label(tr("Budget applies to", "Orçamento para"), target);
  targetLabel.hidden = true;
  scope.addEventListener("change", () => {
    const options = scope.value === "scene" ? project.scenes.map(sid => [sid, sceneData(sid).data.title]) :
      scope.value === "provider" ? [["openai", "OpenAI"], ["runway", "Runway"], ["elevenlabs", "ElevenLabs"]] : [];
    target.replaceChildren(...options.map(([id, name]) => node("option", {value: id}, name)));
    targetLabel.hidden = options.length === 0;
  });
  const amount = input("", {type: "number", min: 0, step: ".01"});
  const values = [[tr("Estimated", "Estimado"), c.estimated], [tr("Reserved", "Reservado"), c.reserved],
    [tr("Actual", "Efetivo"), c.actual], [tr("Uncertain", "Incerto"), c.uncertain], [tr("Remaining", "Restante"), c.remaining]];
  return stack(title(tr("Cost analytics", "Custos")), node("div", {class: "grid stats"}, values.map(([label, value]) => card(small(label),
    node("div", {class: "metric"}, format(value))))),
  card(node("h2", {}, tr("Spending limits", "Limites de gasto")),
    ...c.limits.map(limit => node("p", {}, limit.scope, " · ", sceneData(limit.target)?.data.title ||
      (limit.target !== projectId ? limit.target : "") || c.currency, " · ", format(limit.limit))),
    row(label(tr("Scope", "Escopo"), scope), targetLabel, label(tr("Limit", "Limite") + " (" + c.currency + ")", amount)),
    button(tr("Review budget change", "Revisar mudança de orçamento"), () => {
      if (!amount.value || !amount.checkValidity()) throw new Error(tr("Enter a valid limit", "Informe um limite válido"));
      const limit = Math.round(Number(amount.value) * 100);
      modal(tr("Confirm budget change", "Confirmar mudança de orçamento"), node("p", {}, format(limit)), [
        button(tr("Confirm new limit", "Confirmar novo limite"), async () => {
          await mutate(path("/budget"), "PUT", {scope: scope.value, target: target.value, limit, confirmed: true});
          ui("dialog").close();
        }, true),
      ]);
    })), jobsTable(new URLSearchParams(location.search).get("job")));
}

const codexLoginPath = "/api/providers/codex/login";

async function connectCodex() {
  const challenge = await api(codexLoginPath, "POST");
  if (challenge.status === "connected") {
    render();
    return;
  }
  const status = node("p", {role: "status"}, tr("Waiting for ChatGPT authorization…", "Aguardando autorização no ChatGPT…"));
  const instructions = stack(
    node("p", {}, tr(
      "Enable device code login in ChatGPT Settings → Security. Then open the official authorization page and enter the code below.",
      "Ative o login por código de dispositivo em ChatGPT → Configurações → Segurança. Depois abra a página oficial de autorização e informe o código abaixo.")),
    node("p", {}, node("code", {}, challenge.code)),
    node("a", {href: challenge.url, target: "_blank", rel: "noopener noreferrer"},
      tr("Open ChatGPT authorization", "Abrir autorização do ChatGPT")));
  const dialog = ui("dialog");
  modal(tr("Connect Codex", "Conectar Codex"), stack(instructions, status), [
    button(tr("Cancel sign-in", "Cancelar conexão"), async () => {
      await api(codexLoginPath, "DELETE");
      dialog.close();
    }),
  ]);
  while (dialog.open && status.isConnected) {
    await new Promise(resolve => setTimeout(resolve, 3000));
    if (!dialog.open || !status.isConnected) break;
    const {status: currentStatus} = await api(codexLoginPath);
    if (!dialog.open || !status.isConnected) break;
    if (currentStatus === "pending") continue;
    if (currentStatus === "connected") {
      dialog.close();
      render();
      notify(tr("Codex connected with ChatGPT", "Codex conectado ao ChatGPT"));
      return;
    }
    instructions.hidden = true;
    status.textContent = tr(
      "Sign-in ended. Close this window and start again if needed.",
      "A tentativa de conexão terminou. Feche esta janela e inicie outra, se necessário.");
    return;
  }
}

async function providersView() {
  const providers = await api("/api/providers");
  const currency = providerCurrency || project?.data.currency || (locale === "pt" ? "BRL" : "USD");
  const currencyPicker = select([["BRL", "BRL"], ["USD", "USD"]], currency);
  currencyPicker.addEventListener("change", () => {
    providerCurrency = currencyPicker.value;
    render();
  });
  return stack(title(tr("Providers", "Provedores")), node("p", {class: "muted"}, tr(
    "Rates are operator-entered spending ceilings.",
    "Os valores são tetos informados pelo operador.")),
  label(tr("Currency", "Moeda"), currencyPicker),
  grid(...providers.map(p => {
    if (p.billing === "subscription") {
      const labels = {
        chatgpt: ["Signed in with ChatGPT", "Conectado com o ChatGPT"],
        missing: ["Codex CLI missing", "Codex CLI ausente"],
        login_required: ["Disconnected", "Desconectado"],
        unavailable: ["Codex status unavailable", "Status do Codex indisponível"],
        invalid_model: ["Invalid Codex model configuration", "Configuração de modelo Codex inválida"],
      };
      return card(node("h2", {}, tr("Codex · ChatGPT plan", "Codex · assinatura ChatGPT")),
        node("span", {class: p.credential_present ? "status completed" : "status draft"}, tr(...labels[p.status])),
        small(tr("Research, scripts, translations and editorial analysis use your signed-in Codex account.",
          "Pesquisa, roteiros, traduções e análise editorial usam a sua conta conectada no Codex.")),
        p.status === "login_required" && (p.device_login
          ? button(tr("Connect ChatGPT account", "Conectar conta do ChatGPT"), connectCodex, true)
          : node("p", {}, tr("Sign in on the host with ", "Conecte-se no servidor com "), node("code", {}, "codex login"))));
    }
    const saved = p.configurations[currency];
    const model = select(p.models.map(value => [value, value]), saved?.model);
    const rate = input(saved ? (saved.ceiling / 100).toFixed(2) : "", {type: "number", min: ".01", step: ".01"});
    const interval = input(saved?.interval ?? 5, {type: "number", min: 1, max: 300});
    const timeout = input(saved?.timeout ?? 180, {type: "number", min: 15, max: 1800});
    const enabled = input("", {type: "checkbox", checked: saved?.enabled === true});
    const providerName = {openai: "OpenAI", runway: "Runway", elevenlabs: "ElevenLabs"}[p.provider];
    return card(row(node("h2", {}, p.provider + " · " + p.operation), badge(p.credential_present ? "COMPLETED" : "DRAFT")),
      small(p.credential_present ? tr("Credential present", "Credencial configurada") : tr("Credential missing", "Credencial ausente")),
      p.manage_key && button(p.credential_present ? tr("Replace API key", "Trocar chave de API") : tr("Connect", "Conectar"),
        () => connectProvider(p.provider, providerName)),
      p.provider === "elevenlabs" && small(tr(
        "The key needs Voices Read and Text to Speech access.",
        "A chave precisa de acesso de leitura às vozes e de síntese de voz.")),
      small(p.provider === "runway" ? "2–10 s · 720p · " + tr("approved first frame", "primeiro quadro aprovado") : ""),
      label(tr("Default model", "Modelo padrão"), model),
      label(p.provider === "runway" ? tr("Ceiling per second", "Teto por segundo")
        : p.provider === "elevenlabs" ? tr("Ceiling per 1,000 characters", "Teto por 1.000 caracteres")
        : tr("Ceiling per request", "Teto por solicitação"), rate),
      row(label(tr("Minimum interval (s)", "Intervalo mínimo (s)"), interval), label(tr("Timeout (s)", "Tempo limite (s)"), timeout)),
      label(tr("Enable for this currency", "Ativar para esta moeda"), enabled),
      button(tr("Save provider settings", "Salvar configurações do provedor"), async () => {
        if (![rate, interval, timeout].every(f => f.value && f.checkValidity())) {
          throw new Error(tr("Enter a positive ceiling, interval and timeout", "Informe teto, intervalo e tempo limite válidos"));
        }
        await api("/api/providers", "PUT", {provider: p.provider, operation: p.operation, model: model.value, currency,
          ceiling: Math.round(Number(rate.value) * 100), interval: Number(interval.value), timeout: Number(timeout.value),
          enabled: enabled.checked, confirmed: true});
        notify(tr("Provider settings saved", "Configurações salvas"));
      }));
  })));
}

function connectProvider(provider, name) {
  const key = input("", {type: "password", autocomplete: "off", maxlength: 240});
  modal(name, label(tr("API key", "Chave de API"), key), [
    button(tr("Validate and save", "Validar e salvar"), async () => {
      if (!key.value.trim()) throw new Error(tr("Enter the API key.", "Informe a chave de API."));
      await api("/api/providers/" + provider + "/credential", "PUT", {key: key.value.trim()});
      key.value = "";
      ui("dialog").close();
      render();
    }, true),
  ]);
}

function agentAccessView() {
  const target = projectId;
  const panel = node("details", {}, node("summary", {}, tr("Agent access", "Acesso de agentes")));
  const body = stack();
  const endpoint = path("/agent-access");
  const name = input("", {required: true, maxlength: 200});
  const scope = select([["propose", tr("Read and propose", "Consultar e propor")],
    ["read", tr("Read only", "Somente consulta")]]);
  const days = input("7", {type: "number", min: 1, max: 365, required: true});
  const list = stack();
  const load = async () => {
    const keys = await api(endpoint);
    list.replaceChildren(...keys.map(key => {
      const enabled = !key.revoked && key.expires * 1000 > Date.now();
      const permits = key.scope === "propose" ? tr("Read and propose", "Consultar e propor") :
        tr("Read only", "Somente consulta");
      const state = key.revoked ? tr("Revoked", "Revogado") : enabled ?
        tr("Expires", "Expira em") + " " + new Date(key.expires * 1000).toLocaleDateString() : tr("Expired", "Expirado");
      return row(node("strong", {}, key.label), small(permits + " · " + state),
        enabled && button(tr("Revoke", "Revogar"), async () => {
          await api(endpoint + "/" + key.id, "DELETE");
          await load();
        }));
    }));
  };
  panel.addEventListener("toggle", () => {
    if (panel.open) execute(load);
  });
  body.append(
    node("p", {}, tr("Give an agent access to this project's media, timeline and review notes. Edit proposals await your decision.",
      "Dê acesso às mídias, à linha do tempo e aos comentários de revisão deste projeto. As propostas de edição aguardam sua decisão.")),
    node("form", {onsubmit: event => {
      event.preventDefault();
      execute(async () => {
        const key = await api(endpoint, "POST", {label: name.value, scope: scope.value, days: Number(days.value)});
        name.value = "";
        const secret = input(key.token, {readonly: true, spellcheck: "false"});
        modal(tr("Agent credential created", "Credencial de agente criada"), stack(
          label(tr("Project API", "API do projeto"), input(location.origin + "/api/agent/v1/projects/" + target, {readonly: true})),
          label(tr("Access token", "Token de acesso"), secret),
          small(tr("Copy the token now. The studio shows it once.", "Copie o token agora. O estúdio o exibe uma só vez."))), [
          button(tr("Copy token", "Copiar token"), async () => {
            await navigator.clipboard.writeText(secret.value);
            notify(tr("Token copied", "Token copiado"));
          }, true),
        ]);
        ui("dialog").addEventListener("close", () => {
          if (ui("dialog-content").contains(secret)) ui("dialog-content").replaceChildren();
        }, {once: true});
        await load();
      }, event.submitter);
    }}, row(label(tr("Agent name", "Nome do agente"), name), label(tr("Permissions", "Permissões"), scope),
      label(tr("Valid for days", "Validade em dias"), days)),
    row(node("button", {type: "submit", class: "primary"}, tr("Create credential", "Criar credencial")))),
    list);
  panel.append(card(body));
  return panel;
}

async function proposalsView() {
  const target = projectId;
  const endpoint = path("/proposals");
  const requested = new URLSearchParams(location.search).get("proposal");
  const proposals = await api(endpoint);
  if (requested && !proposals.some(item => item.id === requested)) {
    proposals.unshift(await api(endpoint + "/" + encodeURIComponent(requested)));
  }
  if (projectId !== target) return null;
  proposals.sort((a, b) => Number(b.id === requested) - Number(a.id === requested));
  const fields = {video: ["Take", "Tomada"], trim: ["Trim start", "Corte inicial"],
    seconds: ["Duration", "Duração"], captions: ["Captions", "Legendas"]};
  function proposalPreview(proposal) {
    const languages = current("timeline").data.languages;
    const preferred = locale === "pt" ? "pt-BR" : "en-US";
    const names = new Intl.DisplayNames([preferred], {type: "language"});
    const language = select(languages.map(code => [code, names.of(code)]),
      languages.includes(preferred) ? preferred : languages[0]);
    const playerHost = node("div", {class: "preview-stage"});
    const cuts = [
      ["saved", tr("Saved cut", "Corte salvo")], ["proposed", tr("Proposed cut", "Corte proposto")],
    ].map(([cut, name]) => [cut, node("button", {
      type: "button", "aria-pressed": "false", onclick: () => loadPreview(cut),
    }, name)]);
    const comparison = node("div", {class: "row proposal-comparison", role: "group",
      "aria-label": tr("Compare cuts", "Comparar cortes")}, cuts.map(([, control]) => control));
    const details = node("details", {name: "proposal-previews"},
      node("summary", {}, tr("Compare cuts", "Comparar cortes")),
      stack(comparison, languages.length > 1 && label(tr("Preview language", "Idioma da prévia"), language), playerHost));
    const changed = new Set(proposal.plan.diff.map(item => item.scene));
    let player, position, variant = "proposed", requests = 0;
    function showVariant(cut) {
      cuts.forEach(([value, control]) => control.setAttribute("aria-pressed", String(value === cut)));
    }
    function mountPlayer() {
      activeScenePreview?.dispose();
      player = activeScenePreview = createScenePreview(() => {});
      playerHost.replaceChildren(player.element);
    }
    function closePreview() {
      requests++;
      if (player && activeScenePreview === player) {
        player.dispose();
        activeScenePreview = null;
      }
      player = null;
      position = null;
      variant = "proposed";
      showVariant(null);
      playerHost.replaceChildren();
    }
    async function loadPreview(cut) {
      const request = ++requests;
      variant = cut;
      if (!player || activeScenePreview !== player) mountPlayer();
      const requestedPlayer = player;
      requestedPlayer.pause();
      const previousPosition = requestedPlayer.position();
      position = previousPosition || position;
      requestedPlayer.updating();
      const isCurrent = () => details.isConnected && details.open &&
        request === requests && activeScenePreview === requestedPlayer;
      try {
        const query = new URLSearchParams({language: language.value, cut});
        const plan = await api(endpoint + "/" + proposal.id + "/preview?" + query);
        if (isCurrent()) {
          const scene = position?.scene ?? plan.clips.find(clip => changed.has(clip.scene))?.scene;
          // Native subtitle renderers may retain captions from a paused source.
          if (previousPosition) mountPlayer();
          player.apply(plan, scene, position?.offset);
          showVariant(cut);
        }
      } catch (error) {
        if (isCurrent()) {
          showVariant(null);
          requestedPlayer.error(error.status === 409 ?
            tr("This cut or its assets changed. Refresh the page to check the proposal.",
              "Este corte ou seus materiais mudaram. Atualize a página para conferir a proposta.") :
            tr("Could not load the preview. Select a cut to try again.",
              "Não foi possível carregar a prévia. Selecione um corte para tentar novamente."));
        }
      }
    }
    language.addEventListener("change", () => loadPreview(variant));
    details.addEventListener("toggle", () => {
      if (!details.isConnected || !details.open) closePreview();
      else loadPreview(variant);
    });
    return details;
  }
  const describe = (field, change, side) => {
    const value = change[side];
    if (field === "captions") return Object.entries(change.resolved?.[side] || value || {})
      .map(([lang, text]) => lang + " · " + (text ?? tr("From narration", "Da narração"))).join("\n") ||
        tr("From narration", "Da narração");
    if (field === "video") {
      const ident = change.resolved?.[side] || value;
      const take = project.versions.find(version => version.id === ident);
      const text = ident ? "v" + (take?.seq ?? ident.slice(0, 8)) : tr("Current take", "Tomada atual");
      return take?.url ? node("a", {href: take.url, target: "_blank", rel: "noopener"},
        tr("Watch take ", "Assistir à tomada ") + text) : text;
    }
    return value == null ? tr("Story duration", "Duração do roteiro") : value + " s";
  };
  const decide = (proposal, decision) => {
    const apply = decision === "apply";
    const comment = area("", {maxlength: 4000});
    modal(apply ? tr("Apply edit proposal", "Aplicar proposta de edição") : tr("Reject proposal", "Rejeitar proposta"),
      stack(apply && node("p", {}, tr("The accepted changes become the new saved cut.",
        "As alterações aceitas viram o novo corte salvo.")),
      label(tr("Decision note", "Nota da decisão"), comment)), [
        button(apply ? tr("Apply changes", "Aplicar alterações") : tr("Confirm rejection", "Confirmar rejeição"), async () => {
          const result = await api(endpoint + "/" + proposal.id + "/decision", "POST", {decision, comment: comment.value});
          ui("dialog").close();
          if (projectId === target) {
            const alreadySaved = timelineDraft && json(current("timeline")?.data) === json(timelineDraft);
            if (result.applied_revision && alreadySaved) timelineDraft = null;
            await refresh();
          }
        }, true),
      ]);
  };
  const openAppliedCut = () => {
    const recovery = timelineRecovery ??= browserTimelineDrafts(projectId);
    const open = () => {
      recovery.reset();
      timelineDraft = null;
      navigate("timeline");
    };
    const editBase = project.versions.find(version => version.id === timelineBase);
    if (timelineDraft ? json(timelineDraft) !== json(editBase?.data) : recovery.current()) {
      modal(tr("Open the applied cut?", "Abrir o corte aplicado?"),
        node("p", {}, recovery.current() && (!timelineDraft || timelineStored) ?
          tr("Your work stays available through this browser's drafts.", "Seus ajustes continuam disponíveis nos rascunhos deste navegador.") :
          tr("Opening the applied cut discards the edits in this tab.", "Abrir o corte aplicado descarta os ajustes desta aba.")),
        [button(tr("Open cut", "Abrir corte"), () => { ui("dialog").close(); open(); }, true)]);
    } else open();
  };
  const folder = proposals.map(proposal => {
    const pending = proposal.status === "PENDING";
    const stale = pending && proposal.plan.base_revision !== current("timeline")?.id;
    const status = {PENDING: ["Awaiting decision", "Aguardando decisão"], APPLIED: ["Applied", "Aplicada"],
      REJECTED: ["Rejected", "Rejeitada"]}[proposal.status];
    return card(row(node("h2", {}, proposal.agent), small(tr(...status)),
      small(tr("Base cut · v", "Corte de origem · v") + proposal.plan.base_seq)),
      node("p", {}, proposal.plan.rationale),
      proposal.plan.review_note && node("a", {href: proposal.plan.review_note.url},
        tr("Open source comment at ", "Abrir comentário de origem em ") + timecode(proposal.plan.review_note.position_ms)),
      small(tr("Submitted", "Enviada em") + " " + new Date(proposal.created * 1000).toLocaleString()),
      proposal.plan.diff.map(row => stack(node("h3", {}, row.title),
        Object.entries(row.changes).map(([field, change]) => stack(node("strong", {}, tr(...fields[field])),
          node("div", {class: "proposal-diff"}, ["before", "after"].map(side =>
            node("div", {class: "proposal-value"}, small(side === "after" ? tr("Proposed", "Proposto") : tr("Current", "Atual")),
              node("p", {}, describe(field, change, side))))))))),
      stale && node("p", {role: "status"}, tr("The saved cut changed. Request a proposal based on the latest revision.",
        "O corte salvo mudou. Peça uma proposta baseada na revisão atual.")),
      pending && !stale && proposalPreview(proposal),
      pending && row(button(tr("Apply proposal", "Aplicar proposta"), () => decide(proposal, "apply"), true, stale),
        button(tr("Reject", "Rejeitar"), () => decide(proposal, "reject"))),
      proposal.decided_by && small(proposal.decided_by + " · " + new Date(proposal.decided * 1000).toLocaleString()),
      proposal.comment && node("p", {}, proposal.comment),
      proposal.applied_revision && proposal.applied_revision === current("timeline")?.id &&
        button(tr("Open applied cut", "Abrir corte aplicado"), openAppliedCut, true));
  });
  return stack(title(tr("Edit proposals", "Propostas de edição")),
    folder.length ? folder : node("p", {}, tr("No edit proposals for this project.", "Ainda não há propostas de edição neste projeto.")));
}

function settingsView() {
  const data = {...project.data.style};
  const labels = {art_direction: ["Art direction", "Direção de arte"], palette: ["Color palette", "Paleta"],
    lighting: ["Lighting", "Iluminação"], camera: ["Camera language", "Linguagem de câmera"], environments: ["Environments", "Ambientes"],
    characters: ["Character references", "Referências de personagens"], restrictions: ["Visual restrictions", "Restrições visuais"],
    realism: ["Realism", "Realismo"]};
  const aspect = select([["16:9", "16:9"], ["9:16", "9:16"]], data.aspect);
  const defaults = {...project.data.defaults};
  const operations = {
    research: ["Research", "Pesquisa"], script: ["Script", "Roteiro"], translation: ["Translation", "Tradução"], image: ["Image", "Imagem"],
    video: ["Video for new scenes", "Vídeo para novas cenas"], voice: ["Narration", "Narração"],
  };
  const preferences = Object.entries(operations).map(([key, names]) => {
    const options = key === "translation" ? [codexOption(), apiOption] : [["local", "Local"]];
    if (key === "research" || key === "script") options.push(codexOption());
    if (key !== "translation") options.push(key === "video" ? ["runway", "Runway"] : apiOption);
    if (key === "video") options.push(flowOption());
    if (key === "voice") options.push(["elevenlabs", "ElevenLabs"]);
    const field = select(options, defaults[key] || (key === "translation" ? "openai" : "local"));
    field.addEventListener("change", () => { defaults[key] = field.value; });
    return label(tr(...names), field);
  });
  return stack(title(tr("Project settings", "Configurações do projeto")), agentAccessView(), card(node("h2", {}, tr("Visual style guide", "Guia de estilo visual")),
    small(tr("Changes require storyboard review. Reviewed narration remains available when the scene text matches.",
      "Alterações exigem revisão do storyboard. A narração revisada é reaproveitada quando o texto coincide.")),
    grid(...Object.entries(labels).map(([key, names]) => label(tr(...names), input(data[key], {oninput: e => { data[key] = e.target.value; }})))),
    label(tr("Aspect ratio", "Proporção"), aspect),
    button(tr("Save style guide", "Salvar guia de estilo"), () => mutate(path("/style"), "PUT", {...data, aspect: aspect.value}), true)),
  card(node("h2", {}, tr("Default providers", "Provedores padrão")), grid(...preferences),
    button(tr("Save provider defaults", "Salvar provedores padrão"), () => mutate(path("/defaults"), "PUT", defaults))));
}

function render() {
  activeScenePreview?.dispose();
  activeScenePreview = null;
  editing = false;
  document.documentElement.lang = locale === "pt" ? "pt-BR" : "en";
  ui("skip-main").textContent = tr("Skip to main content", "Pular para o conteúdo principal");
  ui("product").textContent = tr("Production", "Produção");
  ui("utilities").replaceChildren(button(locale === "en" ? "Português" : "English", () => {
    locale = locale === "en" ? "pt" : "en";
    localStorage.setItem("mpt-production-language", locale);
    render();
  }), ...(loggedIn ? [button(tr("Sign out", "Sair"), async () => {
    await api("/api/session", "DELETE");
    loggedIn = false;
    project = null;
    projects = [];
    render();
  })] : []));
  ui("sidebar").hidden = !loggedIn;
  if (!loggedIn) {
    timelineDraft = timelineRecovery = null;
    document.title = tr("Sign in", "Entrar") + " · Hyphotenic";
    ui("workspace").replaceChildren(loginView());
    return;
  }
  const globalPages = ["projects", "catalog", "channel", "providers"];
  const pages = project ? Object.keys(navLabels) : globalPages;
  if (!pages.includes(page)) page = "projects";
  const viewLabel = page === "timeline" ? tr("Edit and review", "Editar e revisar") : tr(...navLabels[page]);
  document.title = [viewLabel, project?.data.title, "Hyphotenic"].filter(Boolean).join(" · ");
  const url = new URL(location.href);
  if (project) url.searchParams.set("project", project.id);
  else url.searchParams.delete("project");
  url.searchParams.set("view", page);
  if (page !== "proposals") url.searchParams.delete("proposal");
  if (page !== "costs") url.searchParams.delete("job");
  if (page !== "delivery") {
    for (const key of ["assembly", "t", "note"]) url.searchParams.delete(key);
  }
  if (url.href !== location.href) history.replaceState(null, "", url);
  if (project) {
    const switcher = select(projects.map(p => [p.id, p.data.title]), projectId);
    switcher.setAttribute("aria-label", tr("Active project", "Projeto ativo"));
    switcher.addEventListener("change", () => execute(() => changeProject(switcher.value)));
    ui("utilities").prepend(switcher);
    ui("utilities").append(button(tr("Refresh project", "Atualizar projeto"), refresh));
  }
  const nav = (name, label = navLabels[name]) => node("button", {type: "button",
    "aria-current": name === page ? "page" : null, onclick: () => navigate(name)}, tr(...label));
  const group = ([en, pt, views]) => node("details", {class: "nav-group", open: views.includes(page)},
    node("summary", {}, tr(en, pt)), node("div", {class: "nav-links"}, views.map(name => nav(name))));
  const menu = project ? [
    nav("dashboard", ["Project overview", "Visão geral"]),
    ...projectNavigation.slice(0, 2).map(group),
    nav("timeline", ["Edit and review", "Editar e revisar"]),
    ...projectNavigation.slice(2).map(group),
    ...globalPages.slice(0, 3).map(name => nav(name)),
  ] : globalPages.map(name => nav(name));
  // A select keeps the complete navigation reachable on narrow screens without
  // moving the project or the player out of the first screen.
  const mobile = select(pages.map(name => [name, tr(...(name === "timeline" ?
    ["Edit and review", "Editar e revisar"] : navLabels[name]))]), page);
  mobile.setAttribute("aria-label", tr("Workspace section", "Seção do estúdio"));
  mobile.addEventListener("change", () => navigate(mobile.value));
  ui("navigation").setAttribute("aria-label", tr("Studio", "Estúdio"));
  ui("navigation").replaceChildren(node("div", {class: "nav-desktop"}, menu),
    node("div", {class: "nav-mobile"}, mobile));
  const asyncViews = {projects: projectsView, providers: providersView, catalog: catalogView, proposals: proposalsView,
    delivery: deliveryView, analysis: experimentsView, channel: youtubeChannelView, youtube: youtubeView};
  if (page in asyncViews) {
    const loadingPage = page;
    const loadingProject = projectId;
    ui("workspace").replaceChildren(node("p", {}, tr("Loading…", "Carregando…")));
    execute(async () => {
      const view = await asyncViews[loadingPage]();
      if (view && page === loadingPage && projectId === loadingProject && loggedIn) {
        ui("workspace").replaceChildren(view);
        if (page === "delivery" && reviewReturnFocus) {
          ui("review-note-" + reviewReturnFocus)?.focus();
          reviewReturnFocus = null;
        }
      }
    });
    return;
  }
  const views = {dashboard, research: researchView, scripts: scriptsView, storyboard: storyboardView,
    scenes: scenesView, narration: narrationView, timeline: timelineView, thumbnails: thumbnailsView, assets: assetView, costs: costsView, settings: settingsView};
  const requestedJob = page === "costs" && url.searchParams.get("job");
  const jobLink = requestedJob && projectId + "/" + requestedJob;
  const jobWasFocused = requestedJob && document.activeElement?.id === "job-" + requestedJob;
  ui("workspace").replaceChildren(views[page]());
  if (jobLink && (jobLink !== focusedJobLink || jobWasFocused)) {
    ui("job-" + requestedJob)?.focus({preventScroll: Boolean(jobWasFocused)});
  }
  focusedJobLink = jobLink;
}

function navigate(name) {
  if (name !== "timeline") reviewEdit = null;
  if (name !== "delivery") reviewReturnFocus = null;
  page = name;
  render();
  ui("workspace").focus();
}

async function start() {
  try {
    const session = await api("/api/session");
    loginMethod = session.login_method || "token";
    loggedIn = session.authenticated;
    if (loggedIn) await refresh(false);
  } catch {
    loggedIn = false;
  }
  render();
}

ui("workspace").addEventListener("input", () => { editing = true; });

function watchingPreview() {
  if (activeScenePreview?.playing()) return true;
  const player = ui("workspace").querySelector(".preview-stage video:not([hidden])");
  return player && !player.paused && !player.ended;
}

setInterval(async () => {
  if (polling || editing || !loggedIn || !project || ui("dialog").open || page === "providers" || watchingPreview() ||
      (ui("workspace").contains(document.activeElement) && ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName))) return;
  if (!project.jobs.some(job => ["DRAFT", "GENERATING"].includes(job.status))) return;
  polling = true;
  const activeProject = projectId;
  const activePage = page;
  try {
    const updated = await api(path());
    if (!editing && !ui("dialog").open && !watchingPreview() && projectId === activeProject && page === activePage &&
        json(updated.jobs) !== json(project.jobs)) {
      project = updated;
      render();
    }
  } catch (error) { notify(error.message); }
  finally { polling = false; }
}, 2500);

start();
