"use strict";

const ui = id => document.getElementById(id);
let locale = localStorage.getItem("mpt-production-language") || "en";
let projectId = sessionStorage.getItem("mpt-production-project");
let projects = [], project = null, loggedIn = false, page = "dashboard";
let loginMethod = "token";
let selected = new Set(), noticeTimer, selectedClip = 0, timelineDraft = null;
let editing = false, polling = false;

const tr = (en, pt) => locale === "pt" ? pt : en;
const navLabels = {
  dashboard: ["Dashboard", "Painel"], projects: ["Projects", "Projetos"], research: ["Research", "Pesquisa"],
  scripts: ["Scripts", "Roteiros"], storyboard: ["Storyboard", "Storyboard"], scenes: ["Scene generation", "Geração de cenas"],
  narration: ["Narration", "Narração"], timeline: ["Timeline", "Linha do tempo"], assets: ["Asset library", "Biblioteca"],
  costs: ["Cost analytics", "Custos"], providers: ["Providers", "Provedores"], settings: ["Settings", "Configurações"],
};
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

async function api(url, method = "GET", data) {
  const raw = data instanceof File;
  const options = {method, credentials: "same-origin"};
  if (data !== undefined) {
    options.headers = {"Content-Type": raw ? "application/octet-stream" : "application/json"};
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
    throw new Error(detail || tr("Request failed", "A solicitação falhou"));
  }
  return response.json();
}

function modal(heading, content, controls = []) {
  const dialog = ui("dialog");
  ui("dialog-content").replaceChildren(stack(node("h2", {}, heading), content, row(...controls,
    button(tr("Close", "Fechar"), () => dialog.close()))));
  if (!dialog.open) dialog.showModal();
}

async function refresh(redraw = true) {
  projects = await api("/api/projects");
  project = projects.some(item => item.id === projectId) ? await api(path()) : null;
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
  const result = await api(path("/quotes"), "POST", {jobs});
  const maximum = input((result.total / 100).toFixed(2), {type: "number", min: 0, step: ".01"});
  const consent = input("", {type: "checkbox"});
  const usesCodex = result.items.some(item => item.billing === "subscription");
  modal(tr("Authorize generation", "Autorizar geração"), stack(
    node("p", {}, (usesCodex ? tr("Estimated direct API spend ", "Gasto direto estimado com API ") :
      tr("Estimated cost ", "Custo estimado ")) + format(result.total, result.currency) + " · " +
      tr("Remaining budget ", "Orçamento restante ") + format(result.budget.remaining, result.currency)),
    node("p", {}, `${result.scene_count} ${tr("scenes", "cenas")} · ${result.attempts} ${tr("authorized attempts", "tentativas autorizadas")}`),
    node("ul", {}, result.items.map(item => node("li", {}, `${item.slot.split("/")[0]} · ${item.provider} · ${item.model} · ${item.billing === "subscription" ? tr("ChatGPT plan usage", "Uso da assinatura ChatGPT") : format(item.estimate)}`))),
    usesCodex && node("p", {}, tr(
      "Codex uses your plan allowance or existing ChatGPT credits. Check that automatic reload is off in ChatGPT Usage to avoid buying more.",
      "O Codex usa a franquia ou o saldo de créditos do ChatGPT. Confira se a recarga automática está desativada em Uso no ChatGPT para evitar novas compras.")),
    label(tr("Maximum authorized spend", "Gasto máximo autorizado"), maximum),
    label(tr("I authorize these jobs at this maximum", "Autorizo estes trabalhos até esse limite"), consent)), [
    button(tr("Authorize and queue", "Autorizar e enfileirar"), async () => {
      if (!consent.checked || !maximum.value || !maximum.checkValidity()) {
        throw new Error(tr("Confirm the spending maximum", "Confirme o limite de gasto"));
      }
      await mutate(path("/quotes/" + result.id + "/authorize"), "POST",
        {maximum: Math.round(Number(maximum.value) * 100), confirmed: true});
      ui("dialog").close();
      notify(tr("Jobs queued", "Trabalhos enfileirados"));
    }, true),
  ]);
}

function mediaPreview(item) {
  if (!item?.url) return null;
  if (item.kind === "captions") return null;
  if (["image", "reference"].includes(item.kind)) return node("img", {src: item.url, alt: item.data.title || tr("Storyboard preview", "Prévia do storyboard"), loading: "lazy"});
  const tag = ["voice", "audio"].includes(item.kind) ? "audio" : "video";
  return node(tag, {src: item.url, controls: true, preload: "metadata", playsinline: true});
}

function revisions(slot) {
  const versions = project.versions.filter(item => item.slot === slot);
  return node("details", {}, node("summary", {}, tr("Versions and comparison", "Versões e comparação") + " (" + versions.length + ")"),
    grid(...versions.map(item => card(row(node("strong", {}, "v" + item.seq), assetBadge(item)),
      mediaPreview(item), node("details", {}, node("summary", {}, tr("Parameters and reviews", "Parâmetros e revisões")),
        node("pre", {}, json(item.data)), ...project.reviews.filter(r => r.version === item.id).map(r =>
          node("p", {}, badge(r.decision), " ", r.comment, " · ", r.actor, " · ", new Date(r.created * 1000).toLocaleString()))),
      item.url && node("a", {href: item.url + "?attachment=true"}, tr("Download version", "Baixar versão"))))));
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

function dashboard() {
  const d = project.data, c = project.costs;
  const values = [
    [tr("Approved scenes", "Cenas aprovadas"), project.approved], [tr("Pending scenes", "Cenas pendentes"), project.pending],
    [tr("Rejected scenes", "Cenas rejeitadas"), project.rejected], [tr("Failed jobs", "Falhas"), project.failed],
    [tr("Estimated spend", "Gasto estimado"), format(c.estimated)], [tr("Actual spend", "Gasto efetivo"), format(c.actual)],
    [tr("Remaining budget", "Orçamento restante"), format(c.remaining)], [tr("Export status", "Estado da exportação"), badge(project.export_status)],
  ];
  return stack(title(d.title, badge(project.stage)),
    node("p", {}, d.topic), small([d.audience, d.languages.join(", "), d.duration + " s"].join(" · ")),
    node("progress", {max: 100, value: project.progress, "aria-label": tr("Production progress", "Progresso da produção")}),
    node("div", {class: "grid stats"}, values.map(([key, value]) => card(small(key), node("div", {class: "metric"}, value)))),
    jobsTable());
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
      return card(label(tr("Section title", "Título da seção"), name), label(tr("Narration", "Narração"), words),
        row(label(tr("Seconds", "Segundos"), seconds), label(tr("Claim category", "Categoria"), category),
          label(tr("Source numbers", "Números das fontes"), refs)),
        button(tr("Remove section", "Remover seção"), () => { sections.splice(i, 1); fields(); }, false, sections.length === 1));
    }));
  }
  fields();
  return stack(title(tr("Scripts", "Roteiros"), assetBadge(item)),
    card(row(label(tr("Provider", "Provedor"), provider), label(tr("Instructions", "Instruções"), instructions)),
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

function narrationView() {
  return stack(title(tr("Narration", "Narração")), grid(...project.scenes.flatMap(sid => project.data.languages.map((language, i) => {
    const scene = sceneData(sid), translation = current("translation/" + sid + "/" + language);
    const voice = current("voice/" + sid + "/" + language);
    const provider = select([["local", tr("System voice", "Voz do sistema")], ["openai", "OpenAI"]], project.data.defaults?.voice || "local");
    const translationProvider = select([codexOption(), apiOption], project.data.defaults?.translation || "openai");
    const voiceName = input(voice?.data.voice || "alloy");
    return card(row(node("h2", {}, scene.data.title), small(language)), node("p", {}, i ? translation?.data.narration : scene.data.narration),
      i > 0 && row(assetBadge(translation), button(tr("Edit translation", "Editar tradução"), () => editTranslation(sid, language, translation), false, !ready(scene)),
        label(tr("Translation provider", "Provedor de tradução"), translationProvider),
        button(tr("Estimate AI translation", "Estimar tradução por IA"),
          () => quote([{operation: "translation", scene: sid, language, provider: translationProvider.value}]), false, !ready(scene))),
      reviewControls(translation), row(label(tr("Speech provider", "Provedor de voz"), provider),
        label(tr("Voice name", "Nome da voz"), voiceName)),
      button(tr("Estimate narration", "Estimar narração"),
        () => quote([{operation: "voice", scene: sid, language, voice: voiceName.value, provider: provider.value}]),
        true, !ready(scene) || (i > 0 && !ready(translation))),
      button(tr("Import narration", "Importar narração"), () => importAsset("voice", sid, language),
        false, !ready(scene) || (i > 0 && !ready(translation))),
      row(assetBadge(voice), voice && small(voice.data.voice)), mediaPreview(voice), reviewControls(voice),
      revisions("voice/" + sid + "/" + language));
  }))), jobsTable());
}

function timelineView() {
  const saved = current("timeline");
  if (!timelineDraft) {
    const defaultClips = project.scenes.map(scene => ({scene, trim: 0, narration_offset: 0, transition: "cut"}));
    const existing = saved?.data;
    timelineDraft = existing && defaultClips.length === existing.clips.length &&
      existing.clips.every(clip => project.scenes.includes(clip.scene)) ? structuredClone(existing) : {
      clips: defaultClips, languages: [...project.data.languages], aspect: project.data.style.aspect,
      resolution: 1080, subtitles: true, watermark: false, music: null, music_volume: .15, metadata: {},
    };
  }
  let draft = timelineDraft;
  const content = stack();
  const audioAssets = project.versions.filter(v => v.kind === "audio" && ready(v)).map(v => [v.id, v.data.title]);
  const assembly = current("assembly"), exported = current("export");
  function fields() {
    selectedClip = Math.min(selectedClip, Math.max(0, draft.clips.length - 1));
    const control = node("div", {class: "timeline", "aria-label": tr("Scene sequence", "Sequência de cenas")},
      ...draft.clips.map((item, i) => node("button", {type: "button", "aria-pressed": String(i === selectedClip),
        onclick: () => { selectedClip = i; fields(); }}, sceneData(item.scene)?.data.title || item.scene)));
    const clip = draft.clips[selectedClip];
    const form = clip ? clipEditor(clip) : null;
    content.replaceChildren(control, ...(form ? [form] : []));
  }
  function clipEditor(clip) {
    const options = project.versions.filter(item => item.slot === "video/" + clip.scene && ready(item)).map(item => [item.id, "v" + item.seq]);
    const video = select([["", tr("Current approved clip", "Clipe aprovado atual")], ...options], clip.video || "");
    video.addEventListener("change", () => { clip.video = video.value || null; });
    const numeric = (key, en, pt, optional = false) => label(tr(en, pt), input(clip[key] ?? "", {
      type: "number", min: 0, step: ".05", oninput: e => { clip[key] = optional && !e.target.value ? null : Number(e.target.value); },
    }));
    const transition = select([["cut", tr("Cut", "Corte")], ["fade", tr("Fade to black", "Fade para preto")]], clip.transition);
    transition.addEventListener("change", () => { clip.transition = transition.value; });
    const effect = select([["", tr("None", "Nenhum")], ...audioAssets], clip.effect || "");
    effect.addEventListener("change", () => { clip.effect = effect.value || null; });
    const swap = offset => {
      const a = selectedClip, b = a + offset;
      [draft.clips[a], draft.clips[b]] = [draft.clips[b], draft.clips[a]];
      selectedClip = b;
      fields();
    };
    return card(row(button(tr("Move left", "Mover à esquerda"), () => swap(-1), false, selectedClip === 0),
      button(tr("Move right", "Mover à direita"), () => swap(1), false, selectedClip >= draft.clips.length - 1)),
    row(label(tr("Clip version", "Versão do clipe"), video), numeric("trim", "Trim start (s)", "Corte inicial (s)"),
      numeric("seconds", "Clip length (s)", "Duração do clipe (s)", true), numeric("narration_offset", "Narration offset (s)", "Atraso da narração (s)")),
    row(label(tr("Transition", "Transição"), transition), label(tr("Sound effect", "Efeito sonoro"), effect)));
  }
  fields();
  const resolution = select([[1080, "1080p"], [1440, "1440p"], [2160, "4K"]], draft.resolution);
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
    onchange: e => { draft.watermark = e.target.checked; }}))),
  ...project.data.languages.map(language => {
    const translation = project.scenes.map(sid => current("translation/" + sid + "/" + language)).find(ready);
    const info = draft.metadata[language] || {title: translation?.data.title || project.data.title,
      description: translation?.data.description || project.data.topic};
    if (draft.languages.includes(language)) draft.metadata[language] = info;
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
      {oninput: e => { info.title = e.target.value; }})), label(tr("Export description", "Descrição de exportação"), input(info.description,
      {oninput: e => { info.description = e.target.value; }})));
  }));
  return stack(title(tr("Timeline", "Linha do tempo"), assetBadge(assembly)), content, output,
    row(button(tr("Save timeline draft", "Salvar rascunho da linha do tempo"), () => mutate(path("/timeline"), "PUT", draft)),
      button(tr("Estimate assembly", "Estimar montagem"), () => quote([{operation: "assembly", timeline: draft}]), true)),
    assembly && card(node("h2", {}, tr("Final review", "Revisão final")), mediaPreview(assembly), reviewControls(assembly),
      row(node("a", {href: assembly.url + "?attachment=true"}, tr("Download draft", "Baixar rascunho")),
        button(tr("Prepare approved export", "Preparar exportação aprovada"),
          () => quote([{operation: "export", version: assembly.id}]), true, !ready(assembly) || assembly.data.watermark)),
      exported && exported.fresh && row(node("a", {href: exported.url + "?attachment=true"}, tr("Download final MP4", "Baixar MP4 final")),
        ...Object.entries(exported.data.captions || {}).map(([lang, id]) => node("a",
          {href: path("/media/" + id + "?attachment=true")}, lang + " · SRT"))),
      revisions("assembly"), exported && revisions("export")), jobsTable());
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
      return card(row(small(tr(...(navLabels[job.operation] || [job.operation, job.operation]))), badge(job.status),
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
      scope.value === "provider" ? [["openai", "OpenAI"], ["runway", "Runway"]] : [];
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

async function providersView() {
  const providers = await api("/api/providers");
  const currency = project?.data.currency || (locale === "pt" ? "BRL" : "USD");
  return stack(title(tr("Providers", "Provedores")), node("p", {class: "muted"}, tr(
    "Set credentials in the workstation environment. Rates are operator-entered ceilings in " + currency + ".",
    "Configure as credenciais no ambiente da estação. Os valores são tetos informados pelo operador em " + currency + ".")),
  grid(...providers.map(p => {
    if (p.billing === "subscription") {
      const labels = {
        chatgpt: ["Signed in with ChatGPT", "Conectado com o ChatGPT"],
        missing: ["Codex CLI missing", "Codex CLI ausente"],
        login_required: ["ChatGPT login required", "Login com ChatGPT necessário"],
        unavailable: ["Codex status unavailable", "Status do Codex indisponível"],
        invalid_model: ["Invalid Codex model configuration", "Configuração de modelo Codex inválida"],
      };
      return card(node("h2", {}, tr("Codex · ChatGPT plan", "Codex · assinatura ChatGPT")),
        row(badge(p.credential_present ? "COMPLETED" : "DRAFT"), tr(...labels[p.status])),
        small(tr("Research, scripts and translations use your signed-in Codex account.", "Pesquisa, roteiros e traduções usam a sua conta conectada no Codex.")),
        !p.credential_present && node("p", {}, tr("Check your connection with ", "Verifique a conexão com "),
          node("code", {}, "codex login status"), tr(" or sign in with ", " ou conecte-se com "), node("code", {}, "codex login")));
    }
    const saved = p.configurations[currency];
    const model = select(p.models.map(value => [value, value]), saved?.model);
    const rate = input(saved ? (saved.ceiling / 100).toFixed(2) : "", {type: "number", min: ".01", step: ".01"});
    const interval = input(saved?.interval ?? 5, {type: "number", min: 1, max: 300});
    const timeout = input(saved?.timeout ?? 180, {type: "number", min: 15, max: 1800});
    const enabled = input("", {type: "checkbox", checked: saved?.enabled === true});
    return card(row(node("h2", {}, p.provider + " · " + p.operation), badge(p.credential_present ? "COMPLETED" : "DRAFT")),
      small(p.credential_present ? tr("Credential present", "Credencial configurada") : tr("Credential missing", "Credencial ausente")),
      small(p.provider === "runway" ? "2–10 s · 720p · " + tr("approved first frame", "primeiro quadro aprovado") : ""),
      label(tr("Default model", "Modelo padrão"), model),
      label(p.provider === "runway" ? tr("Ceiling per second", "Teto por segundo") : tr("Ceiling per request", "Teto por solicitação"), rate),
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
    const field = select(options, defaults[key] || (key === "translation" ? "openai" : "local"));
    field.addEventListener("change", () => { defaults[key] = field.value; });
    return label(tr(...names), field);
  });
  return stack(title(tr("Project settings", "Configurações do projeto")), card(node("h2", {}, tr("Visual style guide", "Guia de estilo visual")),
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
  const pages = project ? Object.keys(navLabels) : ["projects", "providers"];
  if (!pages.includes(page)) page = "projects";
  if (project) {
    const switcher = select(projects.map(p => [p.id, p.data.title]), projectId);
    switcher.setAttribute("aria-label", tr("Active project", "Projeto ativo"));
    switcher.addEventListener("change", () => execute(() => changeProject(switcher.value)));
    ui("utilities").prepend(switcher);
    ui("utilities").append(button(tr("Refresh project", "Atualizar projeto"), refresh));
  }
  ui("navigation").replaceChildren(...pages.map(name => node("button", {type: "button", "aria-current": name === page ? "page" : null,
    onclick: () => { page = name; render(); ui("workspace").focus(); }}, tr(...navLabels[name]))));
  if (page === "providers") {
    ui("workspace").replaceChildren(node("p", {}, tr("Loading providers", "Carregando provedores")));
    execute(async () => {
      const view = await providersView();
      if (page === "providers" && loggedIn) ui("workspace").replaceChildren(view);
    });
    return;
  }
  const views = {dashboard, projects: projectsView, research: researchView, scripts: scriptsView, storyboard: storyboardView,
    scenes: scenesView, narration: narrationView, timeline: timelineView, assets: assetView, costs: costsView, settings: settingsView};
  ui("workspace").replaceChildren(views[page]());
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

setInterval(async () => {
  if (polling || editing || !loggedIn || !project || ui("dialog").open || page === "providers" ||
      (ui("workspace").contains(document.activeElement) && ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName))) return;
  if (!project.jobs.some(job => ["DRAFT", "GENERATING"].includes(job.status))) return;
  polling = true;
  const activeProject = projectId;
  const activePage = page;
  try {
    const updated = await api(path());
    if (!editing && !ui("dialog").open && projectId === activeProject && page === activePage &&
        json(updated.jobs) !== json(project.jobs)) {
      project = updated;
      render();
    }
  } catch (error) { notify(error.message); }
  finally { polling = false; }
}, 2500);

start();
