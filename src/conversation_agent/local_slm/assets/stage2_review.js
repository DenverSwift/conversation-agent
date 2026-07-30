const dimensions = [
  ["naturalness", "Естественность"],
  ["relevance", "По делу"],
  ["brevity", "Краткость"],
  ["telegram_likeness", "Похоже на Telegram"],
  ["relationship_fit", "Подходит контакту"],
  ["emotional_appropriateness", "Уместный тон"],
  ["factual_discipline", "Без выдумок"],
  ["personality_fit", "Живой стиль"],
];

const extraFields = [
  ["correct_action", "Правильное действие", [
    ["yes", "Да", { tone: "good" }],
    ["no", "Нет", { tone: "bad" }],
  ]],
  ["hallucination", "Галлюцинация", [
    ["no", "Нет", { tone: "good" }],
    ["unsure", "Не уверен", { tone: "warning" }],
    ["yes", "Да", { tone: "bad" }],
  ]],
  ["bot_like", "Похож на бота", [
    ["no", "Нет", { tone: "good" }],
    ["yes", "Да", { tone: "bad" }],
  ]],
  ["needs_human_edit", "Нужна правка", [
    ["no", "Нет", { tone: "good" }],
    ["minor", "Небольшая", { tone: "warning" }],
    ["major", "Серьёзная", { tone: "bad" }],
  ]],
];

let reviewData = null;
let visibleIndices = [];
let visiblePosition = 0;
let ratings = emptyRatings();

const workspace = document.querySelector("#workspace");
const emptyState = document.querySelector("#emptyState");
const saveButton = document.querySelector("#saveButton");
const saveStatus = document.querySelector("#saveStatus");
const unreviewedOnly = document.querySelector("#unreviewedOnly");

function emptyRatings() {
  return {
    winner: null,
    candidate_A: {},
    candidate_B: {},
  };
}

function buildControls() {
  const winner = document.querySelector("#winnerControl");
  [
    ["A", "Ответ A"],
    ["B", "Ответ B"],
    ["tie_good", "Оба хорошие"],
    ["tie_bad", "Оба плохие"],
  ].forEach(([value, label]) => winner.appendChild(choiceButton("winner", value, label)));

  const grid = document.querySelector("#ratingsGrid");
  dimensions.forEach(([key, label]) => {
    grid.appendChild(ratingRow(key, label, [
      ["1", "Плохо", { tone: "bad" }],
      ["3", "Нормально", { tone: "warning" }],
      ["5", "Хорошо", { tone: "good" }],
    ]));
  });
  extraFields.forEach(([key, label, choices]) => {
    grid.appendChild(ratingRow(key, label, choices));
  });
}

function ratingRow(key, label, choices) {
  const row = document.createElement("div");
  row.className = "rating-row";
  const title = document.createElement("span");
  title.className = "rating-label";
  title.textContent = label;
  row.appendChild(title);
  ["A", "B"].forEach((candidate) => {
    const segments = document.createElement("div");
    segments.className = "segments";
    choices.forEach(([value, text, flags]) => {
      const button = choiceButton(`${candidate}:${key}`, value, text);
      Object.entries(flags).forEach(([flag, enabled]) => {
        if (enabled) button.dataset[flag] = "true";
      });
      segments.appendChild(button);
    });
    row.appendChild(segments);
  });
  return row;
}

function choiceButton(group, value, label) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.group = group;
  button.dataset.value = value;
  button.textContent = label;
  return button;
}

function currentItem() {
  if (!visibleIndices.length) return null;
  return reviewData.items[visibleIndices[visiblePosition]];
}

function rebuildVisible(preferredPairId = null) {
  visibleIndices = reviewData.items
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => !unreviewedOnly.checked || !item.reviewed)
    .map(({ index }) => index);
  if (preferredPairId) {
    const next = visibleIndices.findIndex(
      (index) => reviewData.items[index].pair_id === preferredPairId,
    );
    visiblePosition = next >= 0 ? next : Math.min(visiblePosition, visibleIndices.length - 1);
  } else {
    visiblePosition = Math.min(visiblePosition, Math.max(visibleIndices.length - 1, 0));
  }
  render();
}

function render() {
  const item = currentItem();
  updateProgress();
  if (!item) {
    workspace.hidden = true;
    emptyState.hidden = false;
    saveButton.disabled = true;
    return;
  }
  workspace.hidden = false;
  emptyState.hidden = true;
  document.querySelector("#scenarioPosition").textContent =
    `${visiblePosition + 1} из ${visibleIndices.length}`;
  document.querySelector("#scenarioId").textContent = item.scenario_id;
  document.querySelector("#category").textContent = item.category;
  renderConversation(item.conversation);
  document.querySelector("#relationship").textContent = relationshipText(item.relationship);
  document.querySelector("#knownFacts").textContent = listText(item.known_facts);
  document.querySelector("#restrictions").textContent = listText(item.restrictions);
  renderCandidate("A", item.candidate_A);
  renderCandidate("B", item.candidate_B);
  ratings = item.ratings ? structuredClone(item.ratings) : emptyRatings();
  syncControls();
  saveStatus.textContent = item.reviewed ? "Оценка сохранена" : "";
}

function renderConversation(turns) {
  const root = document.querySelector("#conversation");
  root.replaceChildren();
  turns.forEach((turn) => {
    (turn.messages || []).forEach((message) => {
      const node = document.createElement("p");
      node.className = "incoming";
      node.textContent = message;
      root.appendChild(node);
    });
  });
}

function renderCandidate(candidate, value) {
  document.querySelector(`#action${candidate}`).textContent = [
    value.action || "unknown",
    value.handoff_required ? "handoff" : null,
    value.reaction || null,
  ].filter(Boolean).join(" · ");
  const root = document.querySelector(`#messages${candidate}`);
  root.replaceChildren();
  const messages = value.messages?.length ? value.messages : ["(без сообщения)"];
  messages.forEach((message) => {
    const node = document.createElement("p");
    node.className = "bubble";
    node.textContent = message;
    root.appendChild(node);
  });
}

function relationshipText(value) {
  if (!value || !Object.keys(value).length) return "—";
  const parts = [value.type];
  ["formality", "warmth", "directness"].forEach((key) => {
    if (value[key] !== undefined) parts.push(`${key}: ${value[key]}`);
  });
  return parts.filter(Boolean).join(" · ");
}

function listText(values) {
  return values?.length ? values.join(" · ") : "—";
}

function syncControls() {
  document.querySelectorAll("[data-group]").forEach((button) => {
    const [candidate, field] = button.dataset.group.split(":");
    const selected = candidate === "winner"
      ? ratings.winner
      : ratings[`candidate_${candidate}`]?.[field];
    button.classList.toggle("selected", String(selected) === button.dataset.value);
  });
  saveButton.disabled = !isComplete();
}

function selectValue(group, value) {
  const [candidate, field] = group.split(":");
  if (candidate === "winner") {
    ratings.winner = value;
  } else {
    const converted = dimensions.some(([key]) => key === field) ? Number(value) : value;
    ratings[`candidate_${candidate}`][field] = converted;
  }
  syncControls();
}

function applyPreset(candidate, value) {
  const target = ratings[`candidate_${candidate}`];
  dimensions.forEach(([key]) => {
    target[key] = value;
  });
  if (value === 1) {
    Object.assign(target, {
      correct_action: "no",
      hallucination: "unsure",
      bot_like: "yes",
      needs_human_edit: "major",
    });
  } else if (value === 3) {
    Object.assign(target, {
      correct_action: "yes",
      hallucination: "no",
      bot_like: "no",
      needs_human_edit: "minor",
    });
  } else {
    Object.assign(target, {
      correct_action: "yes",
      hallucination: "no",
      bot_like: "no",
      needs_human_edit: "no",
    });
  }
  syncControls();
}

function isComplete() {
  if (!ratings.winner) return false;
  return ["candidate_A", "candidate_B"].every((candidate) => {
    const values = ratings[candidate];
    return dimensions.every(([key]) => [1, 3, 5].includes(values[key]))
      && extraFields.every(([key]) => Boolean(values[key]));
  });
}

function move(offset) {
  if (!visibleIndices.length) return;
  visiblePosition = (visiblePosition + offset + visibleIndices.length) % visibleIndices.length;
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

async function saveCurrent() {
  const item = currentItem();
  if (!item || !isComplete()) return;
  saveButton.disabled = true;
  saveStatus.textContent = "Сохраняю…";
  try {
    const response = await fetch("/api/reviews", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pair_id: item.pair_id, ratings }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Не удалось сохранить");
    item.reviewed = true;
    item.ratings = structuredClone(ratings);
    saveStatus.textContent = "Сохранено";
    const currentIndex = visibleIndices[visiblePosition];
    const nextPair = reviewData.items[visibleIndices[visiblePosition + 1]]?.pair_id || null;
    reviewData.reviewed = result.reviewed;
    if (unreviewedOnly.checked) {
      rebuildVisible(nextPair);
    } else {
      visiblePosition = Math.min(visiblePosition + 1, visibleIndices.length - 1);
      render();
    }
    if (currentIndex !== undefined) reviewData.items[currentIndex].reviewed = true;
  } catch (error) {
    saveStatus.textContent = error.message;
    saveButton.disabled = false;
  }
}

function updateProgress() {
  const reviewed = reviewData?.items.filter((item) => item.reviewed).length || 0;
  const total = reviewData?.items.length || 0;
  document.querySelector("#progressText").textContent = `${reviewed} / ${total}`;
  document.querySelector("#progressBar").style.width =
    total ? `${(reviewed / total) * 100}%` : "0";
}

document.addEventListener("click", (event) => {
  const choice = event.target.closest("[data-group]");
  if (choice) selectValue(choice.dataset.group, choice.dataset.value);
  const preset = event.target.closest("[data-preset]");
  if (preset) {
    applyPreset(
      preset.closest("[data-candidate]").dataset.candidate,
      Number(preset.dataset.preset),
    );
  }
});

document.querySelector("#previousButton").addEventListener("click", () => move(-1));
document.querySelector("#skipButton").addEventListener("click", () => move(1));
document.querySelector("#saveButton").addEventListener("click", saveCurrent);
document.querySelector("#showAllButton").addEventListener("click", () => {
  unreviewedOnly.checked = false;
  rebuildVisible();
});
unreviewedOnly.addEventListener("change", () => rebuildVisible(currentItem()?.pair_id));

async function start() {
  buildControls();
  try {
    const response = await fetch("/api/review");
    reviewData = await response.json();
    if (!response.ok) throw new Error(reviewData.error || "Не удалось загрузить review");
    rebuildVisible();
  } catch (error) {
    emptyState.hidden = false;
    emptyState.querySelector("h2").textContent = error.message;
  }
}

start();
