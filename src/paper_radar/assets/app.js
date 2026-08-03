(() => {
  "use strict";

  const cards = Array.from(document.querySelectorAll(".paper-card"));
  const search = document.querySelector("#paper-search");
  const sort = document.querySelector("#sort-papers");
  const archive = document.querySelector("#archive-select");
  const topicButtons = Array.from(document.querySelectorAll(".filter-button"));
  const visibleCount = document.querySelector("#visible-count");
  const noResults = document.querySelector("#no-results");
  const activeTopics = new Set();
  const searchableText = new Map();
  const topicsByCard = new Map();
  let filterFrame = 0;

  function normalise(value) {
    return value.toLocaleLowerCase().normalize("NFKC").replace(/\s+/g, " ").trim();
  }

  cards.forEach((card) => {
    searchableText.set(card, normalise(card.textContent || ""));
    topicsByCard.set(card, new Set((card.dataset.topics || "").split(" ").filter(Boolean)));
  });

  function sortCards() {
    const metric = sort?.value === "video" ? "video" : "fit";
    document.querySelectorAll(".paper-grid").forEach((grid) => {
      Array.from(grid.querySelectorAll(":scope > .paper-card"))
        .sort((a, b) => Number(b.dataset[metric]) - Number(a.dataset[metric]))
        .forEach((card) => grid.appendChild(card));
    });
  }

  function applyFilters() {
    const query = normalise(search?.value || "");
    let count = 0;
    cards.forEach((card) => {
      const topics = topicsByCard.get(card) || new Set();
      const topicMatch = Array.from(activeTopics).every((topic) => topics.has(topic));
      const textMatch = !query || (searchableText.get(card) || "").includes(query);
      const visible = topicMatch && textMatch;
      card.hidden = !visible;
      if (visible) count += 1;
    });

    document.querySelectorAll("[data-section]").forEach((section) => {
      section.hidden = !section.querySelector(".paper-card:not([hidden])");
    });
    if (visibleCount) visibleCount.textContent = String(count);
    if (noResults) noResults.hidden = count !== 0 || cards.length === 0;
    sortCards();
  }

  function scheduleFilters() {
    if (filterFrame) window.cancelAnimationFrame(filterFrame);
    filterFrame = window.requestAnimationFrame(() => {
      filterFrame = 0;
      applyFilters();
    });
  }

  search?.addEventListener("input", scheduleFilters);
  sort?.addEventListener("change", sortCards);
  archive?.addEventListener("change", () => {
    if (archive.value) window.location.assign(archive.value);
  });
  topicButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const topic = button.dataset.topic;
      if (!topic) return;
      if (activeTopics.has(topic)) activeTopics.delete(topic);
      else activeTopics.add(topic);
      button.setAttribute("aria-pressed", String(activeTopics.has(topic)));
      scheduleFilters();
    });
  });

  sortCards();
})();
