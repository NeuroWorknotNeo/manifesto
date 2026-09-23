// Поиск по списку подписей и кнопка «Скопировать ссылку».
// Страница полностью работает и без этого скрипта.
(() => {
  const norm = (s) => s.toLowerCase().replaceAll("ё", "е").trim();

  const search = document.querySelector(".search");
  if (search) {
    const items = [...document.querySelectorAll(".sig-list li")];
    const nothing = document.querySelector(".nothing");
    search.addEventListener("input", () => {
      const query = norm(search.value);
      let shown = 0;
      for (const item of items) {
        const hit = !query || item.dataset.search.includes(query);
        item.hidden = !hit;
        if (hit) shown += 1;
      }
      if (nothing) nothing.hidden = shown > 0;
    });
  }

  for (const button of document.querySelectorAll("[data-copy]")) {
    button.addEventListener("click", async () => {
      const url = button.dataset.copy;
      try {
        await navigator.clipboard.writeText(url);
        button.textContent = "Ссылка скопирована";
      } catch {
        window.prompt("Скопируйте ссылку:", url);
      }
    });
  }
})();
