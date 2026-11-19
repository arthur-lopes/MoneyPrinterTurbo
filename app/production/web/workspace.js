"use strict";

const ui = id => document.getElementById(id);
const entry = new URLSearchParams(location.search);
let locale = localStorage.getItem("mpt-production-language") || "en";
let projectId = entry.get("project") || sessionStorage.getItem("mpt-production-project");
let projects = [], project = null, loggedIn = false, page = entry.get("view") || "dashboard";
let loginMethod = "token";
let providerCurrency = null;
let selected = new Set(), noticeTimer, selectedClip = 0, timelineDraft = null, timelineBase = null;
let editing = false, polling = false;

const tr = (en, pt) => locale === "pt" ? pt : en;
const navLabels = {
  catalog: ["Editorial catalog", "Catálogo editorial"],
  dashboard: ["Dashboard", "Painel"], projects: ["Projects", "Projetos"], research: ["Research", "Pesquisa"],
  scripts: ["Scripts", "Roteiros"], storyboard: ["Storyboard", "Storyboard"], scenes: ["Scene generation", "Geração de cenas"],
  narration: ["Narration", "Narração"], timeline: ["Timeline", "Linha do tempo"], assets: ["Asset library", "Biblioteca"],
  thumbnails: ["Thumbnails", "Miniaturas"],
  delivery: ["Delivery review", "Revisão da entrega"],
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
  ["Assets and publishing", "Acervo e publicação", ["delivery", "assets", "thumbnails"]],
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

function notify(message) {
  ui("notices").replaceChildren(node("p", {}, message));
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => ui("notices").replaceChildren(), 6500);
}

async function execute(action, control) {
  if (control) control.disabled = true;
  try {
    await action();
  } catch (error) {
    notify(error.message || tr("Action failed", "A ação falhou"));
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
  ui("dialog-content").replaceChildren(stack(node("h2", {id: "dialog-title"}, heading), content, row(...controls,
    button(tr("Close", "Fechar"), () => dialog.close()))));
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
  const comment = area("", {placeholder: tr("Optional review comment", "Comentário de revisão opcional")});
  modal(tr(...states[status]), stack(node("p", {}, tr(
    "Record this decision for " + items.length + " version(s).",
    "Registrar esta decisão para " + items.length + " versão(ões).")), comment), [
    button(tr("Confirm decision", "Confirmar decisão"), async () => {
      await mutate(path("/reviews"), "POST", {versions: items.map(item => item.id), status, comment: comment.value});
      selected.clear();
      ui("dialog").close();
      render();
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
  if (["image", "reference", "thumbnail"].includes(item.kind)) return node("img", {
    src: item.url, alt: item.data.title || item.data.headline || tr("Image preview", "Prévia da imagem"), loading: "lazy"});
  const tag = ["voice", "audio"].includes(item.kind) ? "audio" : "video";
  return node(tag, {src: item.url, controls: true, preload, playsinline: true});
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
      item.url && node("a", {href: item.url + "?attachment=true"}, tr("Download version", "Baixar versão"))))));
  }
  details.addEventListener("toggle", reveal);
  return details;
}

function importAsset(kind, scene, language) {
  const types = {image: "image/png,image/jpeg,image/webp", video: "video/mp4,video/quicktime", voice: "audio/wav,audio/mpeg,audio/flac"};
  const file = input("", {type: "file", accept: types[kind]});
  const name = input();
  modal(tr("Import prepared media", "Importar mídia preparada"), stack(label(tr("Media title", "Título da mídia"), name),
    label(tr("Media file", "Arquivo de mídia"), file)), [button(tr("Upload for review", "Enviar para revisão"), async () => {
    if (!file.files[0] || !name.value.trim()) throw new Error(tr("Choose a file and enter its title", "Selecione o arquivo e informe o título"));
    const params = new URLSearchParams({kind, scene, title: name.value});
    if (language) params.set("language", language);
    await mutate(path("/uploads?" + params.toString()), "POST", file.files[0]);
    ui("dialog").close();
  }, true)]);
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

function projectsView() {
  const name = input("", {required: true});
  const topic = area("", {required: true});
  const audience = input("", {required: true});
  const tone = input(tr("Documentary", "Documentário"));
  const duration = input("45", {type: "number", min: 2, max: 7200, required: true});
  const structure = area(tr("Opening hook\nScenario setup\nInitial consequences\nEscalation\nUnexpected developments\nLong-term consequences\nConclusion",
    "Abertura\nContexto\nPrimeiras consequências\nEscalada\nReviravoltas\nConsequências de longo prazo\nConclusão"));
  const langs = select([["en-US,pt-BR", "English → Português"], ["pt-BR,en-US", "Português → English"], ["en-US", "English"], ["pt-BR", "Português"]]);
  const currency = select([["USD", "USD"], ["BRL", "BRL"]], locale === "pt" ? "BRL" : "USD");
  return stack(title(tr("Projects", "Projetos")),
    grid(card(node("h2", {}, tr("New project", "Novo projeto")), node("form", {
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
    node("button", {class: "primary", type: "submit"}, tr("Create project", "Criar projeto")))),
    stack(...projects.map(item => card(row(node("h2", {}, item.data.title), badge(item.stage)),
      node("p", {}, item.data.topic), small(item.data.languages.join(" · ")),
      button(tr("Open project", "Abrir projeto"), () => changeProject(item.id)))))));
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

function researchView() {
  const item = current("research");
  const sources = area(json(item?.data.sources || []));
  const claims = area(json(item?.data.claims || []));
  const provider = select([["local", tr("Local worksheet", "Ficha local")], codexOption(), apiOption], project.data.defaults?.research || "local");
  const name = input();
  const url = input("", {type: "url"});
  const excerpt = area();
  return stack(title(tr("Research", "Pesquisa"), assetBadge(item)), grid(
    card(node("h2", {}, tr("Source material", "Material de referência")),
      label(tr("Source title", "Título da fonte"), name), label(tr("Reference URL", "URL da fonte"), url),
      label(tr("Verified excerpt or notes", "Trecho verificado ou anotações"), excerpt),
      button(tr("Add source", "Adicionar fonte"), () => {
        if (!name.value || !url.value || !url.checkValidity() || !excerpt.value) {
          throw new Error(tr("Enter a title, URL and excerpt", "Informe título, URL e trecho"));
        }
        sources.value = json([...parse(sources), {title: name.value, url: url.value, excerpt: excerpt.value}]);
        name.value = url.value = excerpt.value = "";
      }), label(tr("Sources", "Fontes"), sources), label(tr("Research provider", "Provedor de pesquisa"), provider),
      button(tr("Estimate research", "Estimar pesquisa"), () => quote([{operation: "research", provider: provider.value, sources: parse(sources)}]))),
    card(node("h2", {}, tr("Research claims", "Afirmações da pesquisa")),
      small(tr("Each claim has text, category and source indices. Verify the source supports the claim before approval.",
        "Cada afirmação tem texto, categoria e índices de fontes. Confirme que a fonte sustenta a afirmação antes de aprovar.")),
      label(tr("Claims", "Afirmações"), claims), button(tr("Save research revision", "Salvar revisão da pesquisa"),
        () => mutate(path("/research"), "POST", {sources: parse(sources), claims: parse(claims)}), true),
      reviewControls(item), revisions("research"))));
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
  const item = current("script");
  const fallback = [{key: "hook", title: tr("Opening hook", "Abertura"), seconds: 5,
    narration: project.data.topic, category: "uncertain", sources: []}];
  const sections = (item?.data.sections || fallback).map(s => ({...s}));
  const provider = select([["local", tr("Local outline", "Estrutura local")], codexOption(), apiOption], project.data.defaults?.script || "local");
  const instructions = area("", {placeholder: tr("Variation or revision instructions", "Instruções para variação ou revisão")});
  const form = stack();
  function fields() {
    form.replaceChildren(...sections.map((section, i) => {
      const name = input(section.title, {oninput: e => section.title = e.target.value});
      const words = area(section.narration, {oninput: e => section.narration = e.target.value});
      const seconds = input(section.seconds, {type: "number", min: ".1", max: 120, step: ".1",
        oninput: e => section.seconds = Number(e.target.value)});
      const category = select([["verified", tr("Verified", "Verificado")], ["assumption", tr("Assumption", "Premissa")],
        ["speculation", tr("Speculation", "Especulação")], ["uncertain", tr("Uncertain", "Incerto")]], section.category);
      category.addEventListener("change", () => section.category = category.value);
      const refs = input(section.sources.join(", "), {oninput: e => section.sources = e.target.value.split(",").filter(v => v.trim()).map(Number)});
      const visual = input(section.visual_beat || "", {maxlength: 240, oninput: e => {
        if (e.target.value.trim()) section.visual_beat = e.target.value;
        else delete section.visual_beat;
      }});
      const motion = motionCueEditor(section.motion_cues, cues => { section.motion_cues = cues; });
      return card(label(tr("Section title", "Título da seção"), name), label(tr("Narration", "Narração"), words),
        row(label(tr("Seconds", "Segundos"), seconds), label(tr("Claim category", "Categoria"), category),
          label(tr("Source numbers", "Números das fontes"), refs)),
        label(tr("Visual beat", "Mudança visual"), visual),
        evidenceGraphicEditor(section),
        node("details", {open: Boolean(section.motion_cues?.length)},
          node("summary", {}, tr("Editorial motion", "Intervenções de movimento")), motion.element),
        button(tr("Remove section", "Remover seção"), () => { sections.splice(i, 1); fields(); }, false, sections.length === 1));
    }));
  }
  fields();
  return stack(title(tr("Scripts", "Roteiros"), assetBadge(item)),
    roughCutAction(),
    card(small(tr("Pay off the opening question. Give each still a continuous shot and use motion to reveal a useful detail.",
      "Responda à pergunta da abertura. Dê a cada imagem um plano contínuo e use movimento para revelar um detalhe relevante.")),
      row(label(tr("Provider", "Provedor"), provider), label(tr("Instructions", "Instruções"), instructions)),
      row(button(tr("Estimate new variation", "Estimar nova versão"), () =>
        quote([{operation: "script", provider: provider.value, instructions: instructions.value}])),
      item && button(tr("Estimate AI revision", "Estimar revisão por IA"), () =>
        quote([{operation: "script", provider: provider.value, instructions: instructions.value, version: item.id}])))),
    form, row(button(tr("Add section", "Adicionar seção"), () => {
      sections.push({...fallback[0], key: crypto.randomUUID()});
      fields();
    }), button(tr("Save script revision", "Salvar revisão do roteiro"), () => mutate(path("/script"), "POST", {sections}), true),
    item && button(tr("Open storyboard", "Abrir storyboard"), async () => {
      await mutate(path("/storyboard"), "POST");
      page = "storyboard";
      render();
    }, false, !ready(item))), reviewControls(item), revisions("script"),
    item && node("details", {}, node("summary", {}, tr("Restore previous wording", "Restaurar texto anterior")),
      row(...project.versions.filter(v => v.kind === "script" && !v.current).map(v =>
        button("v" + v.seq, () => mutate(path("/script"), "POST", v.data))))));
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
  const provider = select([["local", tr("Local motion", "Movimento local")], ["runway", "Runway · Gen-4 Turbo"]], data.provider);
  const overrides = area(json(data.style_overrides));
  const references = project.versions.filter(v => v.kind === "reference" && ready(v)).map(v => {
    const choice = input("", {type: "checkbox", checked: data.references.includes(v.id)});
    return [v.id, choice, label(v.data.title, choice)];
  });
  modal(tr("Edit scene", "Editar cena"), stack(...fields, label(tr("Video provider and model", "Provedor e modelo de vídeo"), provider),
    label(tr("Style overrides", "Ajustes de estilo"), overrides), small(tr("Approved references", "Referências aprovadas")),
    ...references.map(([, , field]) => field)), [button(tr("Save scene revision", "Salvar revisão da cena"), async () => {
      await mutate(path("/scenes/" + sid), "PUT", {...data, provider: provider.value,
        model: provider.value === "runway" ? "gen4_turbo" : "motion", style_overrides: parse(overrides),
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
  const eligible = sid => ready(sceneData(sid)) && ready(current("image/" + sid)) && !ready(current("video/" + sid));
  return stack(title(tr("Scene generation", "Geração de cenas")),
    row(button(tr("Estimate all eligible scenes", "Estimar todas as cenas elegíveis"),
      () => quote(project.scenes.filter(eligible).map(scene => ({operation: "video", scene}))), true,
      !project.scenes.some(eligible))),
    grid(...project.scenes.map(sid => {
      const scene = sceneData(sid), image = current("image/" + sid), clip = current("video/" + sid);
      const jobs = project.jobs.filter(job => job.slot === "video/" + sid);
      const generating = jobs.some(job => ["DRAFT", "GENERATING"].includes(job.status));
      return card(row(node("h2", {}, scene.data.title), assetBadge(clip)), mediaPreview(clip),
        node("p", {}, scene.data.narration), small([scene.data.provider, scene.data.model, scene.data.seconds + " s",
          clip ? clip.data.resolution + "p" : "", jobs.reduce((sum, job) => sum + job.attempts, 0) + " " + tr("attempts", "tentativas")]
          .filter(Boolean).join(" · ")),
        node("p", {}, scene.data.video_prompt), image && node("details", {}, node("summary", {}, tr("Reference image", "Imagem de referência")), mediaPreview(image)),
        jobs[0] && small(tr("Last estimate ", "Última estimativa ") + format(jobs[0].quoted) + " · " +
          tr("Actual ", "Efetivo ") + format(jobs[0].actual) + " · " + tr("Uncertain ", "Incerto ") + format(jobs[0].uncertain)),
        row(button(clip ? tr("Estimate replacement", "Estimar substituição") : tr("Estimate generation", "Estimar geração"),
          () => quote([{operation: "video", scene: sid}]), true, generating || !eligible(sid)),
        scene.data.provider === "local" && button(tr("Local animation", "Animação local"),
          () => chooseAnimation(sid, scene, clip), false, generating || !ready(scene)),
        button(tr("Edit prompt or model", "Editar prompt ou modelo"), () => editScene(sid, scene)),
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

function timelineView() {
  let saved = current("timeline");
  if (!timelineDraft || (!timelineDraft.clips.length && project.scenes.length)) {
    timelineBase = saved?.id || null;
    const defaultClips = project.scenes.map(scene => ({scene, trim: 0, narration_offset: 0, transition: "cut"}));
    const existing = saved?.data;
    timelineDraft = existing && defaultClips.length === existing.clips.length &&
      existing.clips.every(clip => project.scenes.includes(clip.scene)) ? structuredClone(existing) : {
      clips: defaultClips, languages: [...project.data.languages], aspect: project.data.style.aspect,
      resolution: 1080, subtitles: true, watermark: false, music: null, music_volume: .15, metadata: {},
    };
  }
  const draft = timelineDraft;
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
  const rendered = mediaPreview(assembly);
  const versions = new Map(project.versions.map(v => [v.id, v]));
  const renderedRevision = versions.get(assembly?.data.timeline_revision);
  const chapters = (assembly?.data.retention || []).flatMap(entry => {
    const slot = versions.get(entry.video)?.slot;
    return slot?.startsWith("video/") ? [{...entry, sceneId: slot.slice(6)}] : [];
  });
  const mode = select([
    ...(rendered ? [["assembly", tr("Latest assembly", "Última montagem")]] : []),
    ["clip", tr("Selected take", "Tomada selecionada")],
  ], rendered ? "assembly" : "clip");
  mode.setAttribute("aria-label", tr("Preview source", "Fonte da prévia"));
  let previewMedia, clipPreview, clipPreviewId;

  function footage(clip) {
    return clip?.video ? versions.get(clip.video) : clip && current("video/" + clip.scene);
  }

  function referenceImage(video) {
    return video?.dependencies.map(dep => versions.get(dep.id)).find(asset => asset?.kind === "image");
  }

  function refreshDraftInfo() {
    draftInfo.textContent = (saved?.id || null) !== timelineBase ?
      tr("The saved timeline changed in another session.", "A linha do tempo salva mudou em outra sessão.") :
      saved && json(saved.data) === json(draft) ?
      tr("Timeline saved · v", "Linha do tempo salva · v") + saved.seq :
      tr("Timeline draft · save to resume later", "Rascunho da linha do tempo · salve para retomar depois");
  }

  function seek(player, time) {
    if (!player || !Number.isFinite(time)) return;
    if (player.readyState > 0) player.currentTime = Math.min(time, player.duration);
    else player.addEventListener("loadedmetadata", () => {
      player.currentTime = Math.min(time, player.duration);
    }, {once: true});
  }

  function showPreview(seekToScene = false) {
    const clip = draft.clips[selectedClip];
    let media = rendered;
    if (mode.value === "clip") {
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
      previewInfo.textContent = (assembly.data.rough_cut ?
        tr("Local draft · v", "Rascunho local · v") : tr("Rendered assembly · v", "Montagem renderizada · v")) + assembly.seq +
        (renderedRevision ? tr(" · timeline v", " · linha do tempo v") + renderedRevision.seq : "") +
        (assembly.fresh ? "" : tr(" · inputs have changed", " · os insumos mudaram"));
      const chapter = chapters.find(item => item.sceneId === clip?.scene);
      if (seekToScene && chapter) seek(media, chapter.start);
    }
    if (media === previewMedia && panel.hasChildNodes()) return;
    previewMedia?.pause();
    panel.replaceChildren(media || node("p", {class: "muted"},
      tr("Your preview will appear here.", "Sua prévia vai aparecer aqui.")));
    previewMedia = media;
  }

  function captionEditor(clip) {
    const languages = project.data.languages;
    const interfaceLanguage = locale === "pt" ? "pt-BR" : "en-US";
    const language = select(languages.map(lang => [lang, lang]), languages.includes(interfaceLanguage) ?
      interfaceLanguage : languages[0]);
    const text = area("", {rows: 4, maxlength: 8000});
    const narration = () => current("voice/" + clip.scene + "/" + language.value)?.data.narration || "";
    const showText = () => { text.value = clip.captions?.[language.value] ?? narration(); };
    language.addEventListener("change", showText);
    text.addEventListener("input", () => {
      const value = text.value.trim();
      if (value && value !== narration()) {
        clip.captions ??= {};
        clip.captions[language.value] = value;
      } else if (clip.captions) {
        delete clip.captions[language.value];
        if (!Object.keys(clip.captions).length) delete clip.captions;
      }
    });
    showText();
    return node("details", {}, node("summary", {}, tr("Scene captions", "Legenda da cena")),
      stack(label(tr("Caption language", "Idioma da legenda"), language),
        label(tr("Caption text", "Texto da legenda"), text),
        small(tr("This text appears on the next render. The existing narration is reused.",
          "O texto entra na próxima renderização. A narração existente será reutilizada."))));
  }

  function selectClip(index, seekToScene) {
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

  mode.addEventListener("change", () => showPreview(true));
  if (rendered) {
    const poster = referenceImage(versions.get(chapters[0]?.video));
    if (poster?.url) rendered.poster = poster.url;
    for (const [language, id] of Object.entries(assembly.data.captions || {})) {
      rendered.append(node("track", {kind: "subtitles", srclang: language, label: language,
        src: path("/media/" + id + "?webvtt=true")}));
    }
    rendered.setAttribute("aria-label", tr("Rendered assembly", "Montagem renderizada"));
    rendered.addEventListener("timeupdate", () => {
      if (mode.value !== "assembly" || inspector.contains(document.activeElement)) return;
      const chapter = chapters.find(item => item.start <= rendered.currentTime && rendered.currentTime < item.end);
      const index = draft.clips.findIndex(clip => clip.scene === chapter?.sceneId);
      if (index >= 0 && index !== selectedClip) selectClip(index, false);
    });
  }

  function clipEditor(clip) {
    const options = project.versions.filter(item => item.slot === "video/" + clip.scene && usable(item)).map(item => [item.id, "v" + item.seq]);
    const video = select([["", tr("Current take", "Tomada atual")], ...options], clip.video || "");
    if (clip.video && !options.some(([id]) => id === clip.video)) {
      video.append(node("option", {value: clip.video}, tr("Previous take · needs review", "Tomada anterior · requer revisão")));
      video.value = clip.video;
    }
    video.addEventListener("change", () => {
      clip.video = video.value || null;
      mode.value = "clip";
      showPreview(true);
    });
    const numeric = (key, en, pt, optional = false) => label(tr(en, pt), input(clip[key] ?? "", {
      type: "number", min: 0, step: ".05", oninput: e => { clip[key] = optional && !e.target.value ? null : Number(e.target.value); },
      onchange: () => { if (key === "trim" && mode.value === "clip") seek(clipPreview, clip.trim); },
    }));
    const transition = select([["cut", tr("Cut", "Corte")], ["fade", tr("Fade to black", "Fade para preto")]], clip.transition);
    transition.addEventListener("change", () => { clip.transition = transition.value; });
    const effect = select([["", tr("None", "Nenhum")], ...audioAssets], clip.effect || "");
    effect.addEventListener("change", () => { clip.effect = effect.value || null; });
    const swap = offset => {
      const a = selectedClip, b = a + offset;
      [draft.clips[a], draft.clips[b]] = [draft.clips[b], draft.clips[a]];
      selectedClip = b;
      refreshDraftInfo();
      fields(true);
    };
    return stack(node("h2", {}, sceneData(clip.scene)?.data.title || tr("Scene", "Cena")),
      label(tr("Take", "Tomada"), video),
      node("div", {class: "clip-timing"},
        numeric("trim", "Trim start (s)", "Corte inicial (s)"),
        numeric("seconds", "Clip length (s)", "Duração do clipe (s)", true)),
      row(button(tr("Move earlier", "Mover antes"), () => swap(-1), false, selectedClip === 0),
        button(tr("Move later", "Mover depois"), () => swap(1), false, selectedClip >= draft.clips.length - 1)),
      captionEditor(clip),
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
        node("p", {}, tr("Someone saved another revision. Load the latest timeline to continue, or close to keep reviewing your edits.",
          "Outra revisão foi salva. Carregue a linha do tempo mais recente para continuar ou feche para conferir seus ajustes.")), [
          button(tr("Load saved timeline", "Carregar versão salva"), async () => {
            timelineDraft = null;
            await refresh();
            ui("dialog").close();
          }),
        ]);
      return null;
    }
    if (projectId !== target || timelineDraft !== draft) return null;
    saved = result;
    timelineBase = result.id;
    const unchanged = json(draft) === draftData;
    if (unchanged) timelineDraft = structuredClone(result.data);
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
  const content = node("section", {class: "editor-surface card stack"}, node("div", {class: "editor-layout"},
    node("div", {class: "stack preview-stack"}, panel, row(mode, assetBadge(assembly)), previewInfo), sequence, inspector));
  const result = stack(title(project.data.title), rough || content, !rough && row(draftInfo), !rough && controls,
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
    const next = missingInput();
    missingButton.textContent = next ? tr(...previewActions[next]) : "";
    missingButton.hidden = !next;
    renderButton.hidden = Boolean(next);
    renderButton.disabled = Boolean(activeRender);
  };
  refreshTimelineState();
  result.addEventListener("input", refreshTimelineState);
  result.addEventListener("change", refreshTimelineState);
  return result;
}

async function deliveryView() {
  const target = projectId;
  const endpoint = path("/delivery");
  const plan = await api(endpoint);
  if (target !== projectId) return null;
  const assembly = current("assembly"), exported = current("export");
  const matching = plan.revision && assembly?.fresh && assembly.data.timeline_revision === plan.revision;
  const final = matching && !assembly.data.watermark;
  const delivered = final && exported?.fresh && exported.data.timeline_revision === plan.revision;
  const player = mediaPreview(assembly);
  player?.setAttribute("aria-label", tr("Delivery assembly", "Montagem da entrega"));
  const preview = player && card(node("div", {class: "preview-stage"}, player), row(assetBadge(assembly),
    small(tr("Assembly v", "Montagem v") + assembly.seq),
    matching && small(tr("Saved cut v", "Corte salvo v") + plan.seq),
    !matching && small(tr("Earlier assembly", "Montagem anterior"))));
  for (const [language, id] of Object.entries(assembly?.data.captions || {})) {
    player?.append(node("track", {kind: "subtitles", srclang: language, label: language, src: path("/media/" + id + "?webvtt=true")}));
  }
  const files = (item, currentDelivery) => item && row(
    node("a", {href: item.url + "?attachment=true"}, currentDelivery ?
      tr("Download final MP4", "Baixar MP4 final") : tr("Download existing MP4", "Baixar MP4 existente")),
    ...Object.entries(item.data.captions || {}).map(([language, id]) => node("a",
      {href: path("/media/" + id + "?attachment=true")}, language + " · SRT")));
  const editor = button(tr("Open editor", "Abrir editor"), () => navigate("timeline"));
  const heading = title(tr("Delivery review", "Revisão da entrega"), editor);
  if (plan.blocked) return stack(heading, card(node("p", {role: "status"}, plan.blocked),
    files(exported, false)), preview);

  const materialNames = {scene: ["Storyboard", "Storyboard"], image: ["Reference frame", "Quadro de referência"],
    video: ["Take", "Tomada"], voice: ["Narration", "Narração"], translation: ["Translation", "Tradução"],
    reference: ["Reference", "Referência"], audio: ["Audio", "Áudio"]};
  const pending = scene => scene.materials.some(item => item.status === "AWAITING_REVIEW");
  const incomplete = plan.scenes.filter(pending);
  const materialIds = materials => [...new Set(materials.map(item => item.id))];
  const active = project.jobs.find(job => ["assembly", "export"].includes(job.operation) &&
    ["DRAFT", "GENERATING"].includes(job.status));
  const chapter = scene => button(tr("Go to scene", "Ir ao trecho"), () => {
    const seek = () => { player.currentTime = Math.min(scene.start, player.duration); };
    if (player.readyState) seek();
    else player.addEventListener("loadedmetadata", seek, {once: true});
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
    action, !delivered && files(exported, false));
  return stack(heading, preview, delivery, checklist, assembly && revisions("assembly"), jobHistory());
}

function jobHistory() {
  const active = project.jobs.filter(job => ["DRAFT", "GENERATING"].includes(job.status));
  return node("details", {open: Boolean(active.length)}, node("summary", {},
    tr("Job history", "Histórico de trabalhos") + " (" + project.jobs.length + ")"), jobsTable());
}

function jobsTable() {
  return card(row(node("h2", {}, tr("Jobs", "Trabalhos")), button(tr("Refresh", "Atualizar"), refresh)),
    ...project.jobs.slice(0, 40).map(job => {
      const cancel = ["DRAFT", "GENERATING"].includes(job.status) && !job.cancel;
      const confirmSettlement = () => {
        const amount = input("", {type: "number", min: 0, step: ".01"});
        const receipt = input("", {placeholder: tr("Provider invoice or receipt reference", "Referência da fatura ou recibo")});
        modal(tr("Record verified charge", "Registrar cobrança verificada"), stack(label(tr("Actual charge", "Cobrança efetiva"), amount),
          label(tr("Receipt reference", "Referência do recibo"), receipt)), [
          button(tr("Confirm charge", "Confirmar cobrança"), async () => {
            if (!amount.value || !amount.checkValidity() || !receipt.value.trim()) {
              throw new Error(tr("Enter a verified amount and receipt", "Informe um valor verificado e um recibo"));
            }
            await mutate(path("/jobs/" + job.id + "/settle"), "POST",
              {actual: Math.round(Number(amount.value) * 100), receipt: receipt.value, confirmed: true});
            ui("dialog").close();
          }, true),
        ]);
      };
      const operation = job.operation === "rough_cut" ? ["First draft", "Primeiro rascunho"] :
        navLabels[job.operation] || [job.operation, job.operation];
      return card(row(small(tr(...operation)), badge(job.status),
        small(job.provider + " · " + job.model + " · " + job.progress + "%")),
      job.last_error && node("p", {}, job.last_error),
      row(cancel && button(tr("Cancel", "Cancelar"), () => mutate(path("/jobs/" + job.id + "/cancel"), "POST")),
        button(tr("View authorization", "Ver autorização"), async () => {
          const authorization = await api(path("/quotes/" + job.quote));
          modal(tr("Recorded authorization", "Autorização registrada"), stack(node("p", {},
            authorization.actor, " · ", format(authorization.maximum), " · ",
            new Date(authorization.authorized_at * 1000).toLocaleString()), node("pre", {}, json(authorization.requests))));
        }),
        job.uncertain > 0 && button(tr("Record verified charge", "Registrar cobrança verificada"), confirmSettlement),
        job.remote && job.status === "FAILED" && button(tr("Check provider status", "Consultar estado no provedor"),
          () => mutate(path("/jobs/" + job.id + "/reconcile"), "POST"))));
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
  const videoTitle = current("timeline")?.data.metadata?.[language]?.title || project.data.title;
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
    })), jobsTable());
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
        small(tr("Research, scripts and translations use your signed-in Codex account.", "Pesquisa, roteiros e traduções usam a sua conta conectada no Codex.")),
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
    node("p", {}, tr("Give an agent access to this project's media and timeline. Edit proposals await your decision.",
      "Dê acesso às mídias e à linha do tempo deste projeto. As propostas de edição aguardam sua decisão.")),
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
    const open = () => {
      timelineDraft = null;
      navigate("timeline");
    };
    const editBase = project.versions.find(version => version.id === timelineBase);
    if (timelineDraft && json(timelineDraft) !== json(editBase?.data)) {
      modal(tr("Open the applied cut?", "Abrir o corte aplicado?"),
        node("p", {}, tr("There are unsaved changes in this tab. Opening the applied cut discards those local edits.",
          "Há alterações não salvas nesta aba. Abrir o corte aplicado descarta esses ajustes locais.")),
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
      small(tr("Submitted", "Enviada em") + " " + new Date(proposal.created * 1000).toLocaleString()),
      proposal.plan.diff.map(row => stack(node("h3", {}, row.title),
        Object.entries(row.changes).map(([field, change]) => stack(node("strong", {}, tr(...fields[field])),
          node("div", {class: "proposal-diff"}, ["before", "after"].map(side =>
            node("div", {class: "proposal-value"}, small(side === "after" ? tr("Proposed", "Proposto") : tr("Current", "Atual")),
              node("p", {}, describe(field, change, side))))))))),
      stale && node("p", {role: "status"}, tr("The saved cut changed. Request a proposal based on the latest revision.",
        "O corte salvo mudou. Peça uma proposta baseada na revisão atual.")),
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
  editing = false;
  document.documentElement.lang = locale === "pt" ? "pt-BR" : "en";
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
    ui("workspace").replaceChildren(loginView());
    return;
  }
  const globalPages = ["projects", "catalog", "providers"];
  const pages = project ? Object.keys(navLabels) : globalPages;
  if (!pages.includes(page)) page = "projects";
  const url = new URL(location.href);
  if (project) url.searchParams.set("project", project.id);
  else url.searchParams.delete("project");
  url.searchParams.set("view", page);
  if (page !== "proposals") url.searchParams.delete("proposal");
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
    ...globalPages.slice(0, 2).map(name => nav(name)),
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
  const asyncViews = {providers: providersView, catalog: catalogView, proposals: proposalsView, delivery: deliveryView};
  if (page in asyncViews) {
    const loadingPage = page;
    const loadingProject = projectId;
    ui("workspace").replaceChildren(node("p", {}, tr("Loading…", "Carregando…")));
    execute(async () => {
      const view = await asyncViews[loadingPage]();
      if (view && page === loadingPage && projectId === loadingProject && loggedIn) ui("workspace").replaceChildren(view);
    });
    return;
  }
  const views = {dashboard, projects: projectsView, research: researchView, scripts: scriptsView, storyboard: storyboardView,
    scenes: scenesView, narration: narrationView, timeline: timelineView, thumbnails: thumbnailsView, assets: assetView, costs: costsView, settings: settingsView};
  ui("workspace").replaceChildren(views[page]());
}

function navigate(name) {
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
  const player = ui("workspace").querySelector(".preview-stage video");
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
