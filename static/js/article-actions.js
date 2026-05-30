document.addEventListener("submit", async (event) => {
  const form = event.target.closest("form[data-article-action]");
  if (!form) {
    return;
  }

  event.preventDefault();

  const button = form.querySelector("button[type='submit']");
  if (button) {
    button.disabled = true;
  }

  try {
    const response = await fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    });

    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }

    const result = await response.json();
    const card = form.closest(".article-card");
    const message = document.createElement("p");
    message.className = `message inline-message ${result.level || "success"}`;
    message.setAttribute("role", "status");
    message.textContent = result.message || "Done.";

    if (card && result.remove) {
      card.replaceWith(message);
    } else if (card) {
      card.prepend(message);
    }
  } catch (error) {
    const card = form.closest(".article-card");
    const message = document.createElement("p");
    message.className = "message inline-message error";
    message.setAttribute("role", "alert");
    message.textContent = "Sorry, that action failed. Please try again.";
    if (card) {
      card.prepend(message);
    }
    if (button) {
      button.disabled = false;
    }
  }
});
