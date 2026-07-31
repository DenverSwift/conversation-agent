"use strict";

const ISSUE_TAGS = [
  "assistant-like",
  "semantically wrong",
  "invented information",
  "repeats incoming",
  "too long",
  "too short",
  "too many bubbles",
  "unnecessary question",
  "inappropriate profanity",
  "unnatural politeness",
  "wrong casing",
  "style mismatch",
  "privacy concern",
];

const state = {
  data: null,
  track: "all",
  index: 0,
  tags: new Set(),
  saving: false,
};

const byId = (id) => document.getElementById(id);

function visibleItems() {
  if (!state.data) return [];
  return state.data.items.filter(
    (item) => state.track === "all" || item.track === state.track,
  );
}

function renderBubble(container, text, extraClass = "") {
  const bubble = document.createElement("div");
  bubble.className = `bubble ${extraClass}`.trim();
  bubble.textContent = text;
  container.appendChild(bubble);
}

function renderContext(turns) {
  const container = byId("context");
  container.replaceChildren();
  turns.forEach((turn) => {
    const row = document.createElement("div");
    const role = turn.role === "contact" || turn.role === "user" ? "contact" : "agent";
    row.className = `turn ${role}`;
    const label = document.createElement("div");
    label.className = "turn-role";
    label.textContent = role === "contact" ? "Контакт" : "Вы";
    const content = document.createElement("div");
    renderBubble(content, String(turn.content || ""));
    row.append(label, content);
    container.appendChild(row);
  });
  if (!turns.length) renderBubble(container, "Контекст отсутствует");
}

function renderCandidate(id, candidate) {
  const container = byId(id);
  container.replaceChildren();
  const messages = candidate.messages || [];
  if (messages.length) {
    messages.forEach((message) => renderBubble(container, message));
    return;
  }
  const empty = document.createElement("div");
  empty.className = "empty-action";
  empty.textContent = candidate.action === "reaction"
    ? `Реакция: ${candidate.reaction || "без реакции"}`
    : "Без ответа";
  container.appendChild(empty);
}

function renderTags(savedTags = []) {
  const container = byId("issue-tags");
  container.replaceChildren();
  state.tags = new Set(savedTags);
  ISSUE_TAGS.forEach((tag) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = tag;
    button.classList.toggle("active", state.tags.has(tag));
    button.addEventListener("click", () => {
      if (state.tags.has(tag)) state.tags.delete(tag);
      else state.tags.add(tag);
      button.classList.toggle("active", state.tags.has(tag));
    });
    container.appendChild(button);
  });
}

function render() {
  if (!state.data) return;
  const items = visibleItems();
  if (state.index >= items.length) state.index = Math.max(0, items.length - 1);
  const item = items[state.index];
  byId("reviewer").textContent = state.data.reviewer;
  byId("seed").textContent = state.data.seed;
  byId("reviewed").textContent = state.data.reviewed;
  byId("remaining").textContent = state.data.remaining;
  byId("total").textContent = state.data.total;
  byId("all-count").textContent = state.data.total;
  byId("controlled-count").textContent = state.data.tracks.controlled;
  byId("private-count").textContent = state.data.tracks["private-shadow"];
  const percent = state.data.total
    ? Math.round((state.data.reviewed / state.data.total) * 100)
    : 0;
  byId("percent").textContent = `${percent}%`;
  byId("progress-bar").style.width = `${percent}%`;

  if (!item) {
    byId("pair-position").textContent = "0 / 0";
    byId("context").textContent = "Нет пар в этом фильтре";
    byId("candidate-a-output").replaceChildren();
    byId("candidate-b-output").replaceChildren();
    return;
  }
  byId("track-badge").textContent = item.track.replace("-", " ").toUpperCase();
  byId("category").textContent = item.category;
  byId("pair-position").textContent = `${state.index + 1} / ${items.length}`;
  byId("previous").disabled = state.index === 0;
  byId("next").disabled = state.index >= items.length - 1;
  renderContext(item.context || []);
  renderCandidate("candidate-a-output", item.candidate_A);
  renderCandidate("candidate-b-output", item.candidate_B);
  renderTags(item.saved_issue_tags || []);
  document.querySelectorAll("#choices button").forEach((button) => {
    button.classList.toggle("active", button.dataset.choice === item.saved_choice);
  });
  byId("candidate-a").classList.toggle(
    "selected",
    Boolean(item.saved_choice && item.saved_choice.startsWith("a_")),
  );
  byId("candidate-b").classList.toggle(
    "selected",
    Boolean(item.saved_choice && item.saved_choice.startsWith("b_")),
  );
  const status = byId("save-status");
  status.textContent = item.reviewed ? "Сохранено" : "Не оценено";
  status.classList.toggle("saved", item.reviewed);
  const privateRated = item.track === "private-shadow" && item.reviewed;
  byId("target-panel").classList.toggle("hidden", !privateRated);
  byId("reveal-target").classList.remove("hidden");
  byId("target-output").classList.add("hidden");
  byId("target-output").replaceChildren();
}

function toast(message, error = false) {
  const element = byId("toast");
  element.textContent = message;
  element.classList.toggle("error", error);
  element.classList.add("visible");
  window.setTimeout(() => element.classList.remove("visible"), 1800);
}

async function load() {
  const response = await fetch("/api/review", { cache: "no-store" });
  if (!response.ok) throw new Error("Не удалось загрузить review");
  state.data = await response.json();
  const firstUnreviewed = visibleItems().findIndex((item) => !item.reviewed);
  state.index = firstUnreviewed >= 0 ? firstUnreviewed : 0;
  render();
}

async function save(choice) {
  if (state.saving) return;
  const item = visibleItems()[state.index];
  if (!item) return;
  state.saving = true;
  try {
    const response = await fetch("/api/reviews", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pair_id: item.pair_id,
        choice,
        issue_tags: [...state.tags],
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Ошибка сохранения");
    item.reviewed = true;
    item.saved_choice = choice;
    item.saved_issue_tags = [...state.tags];
    state.data.reviewed = payload.reviewed;
    state.data.remaining = payload.total - payload.reviewed;
    render();
    toast("Оценка сохранена");
  } catch (error) {
    toast(error.message, true);
  } finally {
    state.saving = false;
  }
}

async function reveal() {
  const item = visibleItems()[state.index];
  if (!item) return;
  try {
    const response = await fetch(
      `/api/target?pair_id=${encodeURIComponent(item.pair_id)}`,
      { cache: "no-store" },
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Target недоступен");
    const container = byId("target-output");
    container.replaceChildren();
    payload.messages.forEach((message) => renderBubble(container, message));
    container.classList.remove("hidden");
    byId("reveal-target").classList.add("hidden");
  } catch (error) {
    toast(error.message, true);
  }
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    state.track = button.dataset.track;
    state.index = 0;
    const firstUnreviewed = visibleItems().findIndex((item) => !item.reviewed);
    if (firstUnreviewed >= 0) state.index = firstUnreviewed;
    render();
  });
});

document.querySelectorAll("#choices button").forEach((button) => {
  button.addEventListener("click", () => save(button.dataset.choice));
});
byId("previous").addEventListener("click", () => {
  state.index = Math.max(0, state.index - 1);
  render();
});
byId("next").addEventListener("click", () => {
  state.index = Math.min(visibleItems().length - 1, state.index + 1);
  render();
});
byId("reveal-target").addEventListener("click", reveal);

load().catch((error) => toast(error.message, true));
