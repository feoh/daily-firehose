const articleCards = () =>
	Array.from(document.querySelectorAll("[data-article-card]"));
const feedItems = () =>
	Array.from(document.querySelectorAll("[data-feed-list-item]"));
let selectedArticleIndex = -1;
let selectedFeedIndex = -1;

const selectedArticle = () => {
	const cards = articleCards();
	if (cards.length === 0) {
		return null;
	}
	if (selectedArticleIndex < 0 || selectedArticleIndex >= cards.length) {
		selectedArticleIndex = 0;
	}
	return cards[selectedArticleIndex];
};

const selectArticle = (index, { focus = true } = {}) => {
	const cards = articleCards();
	if (cards.length === 0) {
		selectedArticleIndex = -1;
		return null;
	}

	selectedArticleIndex = Math.max(0, Math.min(index, cards.length - 1));
	cards.forEach((card, cardIndex) => {
		card.classList.toggle("is-selected", cardIndex === selectedArticleIndex);
		card.setAttribute(
			"aria-current",
			cardIndex === selectedArticleIndex ? "true" : "false",
		);
	});

	const card = cards[selectedArticleIndex];
	if (focus) {
		card.focus({ preventScroll: true });
		card.scrollIntoView({ block: "nearest" });
	}
	return card;
};

const moveSelection = (offset) => {
	const cards = articleCards();
	if (cards.length === 0) {
		return;
	}
	const currentIndex = selectedArticleIndex < 0 ? 0 : selectedArticleIndex;
	selectArticle(currentIndex + offset);
};

const selectedFeed = () => {
	const items = feedItems();
	if (items.length === 0) {
		return null;
	}
	if (selectedFeedIndex < 0 || selectedFeedIndex >= items.length) {
		selectedFeedIndex = 0;
	}
	return items[selectedFeedIndex];
};

const selectFeed = (index, { focus = true } = {}) => {
	const items = feedItems();
	if (items.length === 0) {
		selectedFeedIndex = -1;
		return null;
	}

	selectedFeedIndex = Math.max(0, Math.min(index, items.length - 1));
	items.forEach((item, itemIndex) => {
		item.classList.toggle("is-selected", itemIndex === selectedFeedIndex);
		item.setAttribute(
			"aria-current",
			itemIndex === selectedFeedIndex ? "true" : "false",
		);
	});

	const item = items[selectedFeedIndex];
	if (focus) {
		const link = item.querySelector("[data-open-feed]");
		(link || item).focus({ preventScroll: true });
		item.scrollIntoView({ block: "nearest" });
	}
	return item;
};

const moveFeedSelection = (offset) => {
	const items = feedItems();
	if (items.length === 0) {
		return;
	}
	const currentIndex = selectedFeedIndex < 0 ? 0 : selectedFeedIndex;
	selectFeed(currentIndex + offset);
};

const openKeyboardHelp = () => {
	const help = document.querySelector("#keyboard-help");
	if (!help) {
		return;
	}
	help.hidden = false;
	help.querySelector("button")?.focus();
};

const focusShortcutTarget = () => {
	const article = selectedArticle();
	if (article) {
		article.focus({ preventScroll: true });
		return;
	}

	const feed = selectedFeed();
	if (feed) {
		feed.focus({ preventScroll: true });
		return;
	}

	document.querySelector("#main-content")?.focus({ preventScroll: true });
};

const closeKeyboardHelp = () => {
	const help = document.querySelector("#keyboard-help");
	if (!help || help.hidden) {
		return;
	}
	help.hidden = true;
	focusShortcutTarget();
};

const isTypingTarget = (target) => {
	if (!(target instanceof HTMLElement)) {
		return false;
	}
	return Boolean(
		target.closest("input, textarea, select, button, [contenteditable='true']"),
	);
};

const navigateToUrl = (href, { allowExternal = false } = {}) => {
	let url;
	try {
		url = new URL(href, window.location.href);
	} catch {
		return false;
	}

	if (!["http:", "https:"].includes(url.protocol)) {
		return false;
	}

	if (!allowExternal && url.origin !== window.location.origin) {
		return false;
	}

	window.location.assign(url.href);
	return true;
};

document.addEventListener("DOMContentLoaded", () => {
	if (articleCards().length > 0) {
		selectArticle(0, { focus: false });
	}
	if (feedItems().length > 0) {
		selectFeed(0, { focus: false });
	}
});

const fallbackCopyToClipboard = (text) => {
	const textarea = document.createElement("textarea");
	textarea.value = text;
	textarea.setAttribute("readonly", "");
	textarea.style.position = "fixed";
	textarea.style.top = "-9999px";
	document.body.append(textarea);
	textarea.select();

	try {
		return document.execCommand("copy");
	} finally {
		textarea.remove();
	}
};

const copyTextToClipboard = async (text) => {
	if (navigator.clipboard?.writeText) {
		await navigator.clipboard.writeText(text);
		return;
	}

	if (!fallbackCopyToClipboard(text)) {
		throw new Error("Copy command failed");
	}
};

document.addEventListener("click", async (event) => {
	const copyButton = event.target.closest("[data-copy-to-clipboard]");
	if (copyButton) {
		const originalText = copyButton.textContent;
		const feedback = copyButton.parentElement?.querySelector(
			"[data-copy-feedback]",
		);
		copyButton.disabled = true;

		try {
			await copyTextToClipboard(copyButton.dataset.copyToClipboard || "");
			copyButton.textContent = "Copied!";
			if (feedback) {
				feedback.textContent = "Copied email address.";
			}
		} catch (error) {
			copyButton.textContent = "Copy failed";
			if (feedback) {
				feedback.textContent =
					"Copy failed. Select and copy the email address manually.";
			}
		} finally {
			window.setTimeout(() => {
				copyButton.disabled = false;
				copyButton.textContent = originalText;
				if (feedback) {
					feedback.textContent = "";
				}
			}, 2500);
		}
		return;
	}

	if (event.target.closest("[data-close-keyboard-help]")) {
		closeKeyboardHelp();
	}

	const card = event.target.closest("[data-article-card]");
	if (card) {
		const index = articleCards().indexOf(card);
		if (index >= 0) {
			selectArticle(index, { focus: false });
		}
	}

	const feedItem = event.target.closest("[data-feed-list-item]");
	if (feedItem) {
		const index = feedItems().indexOf(feedItem);
		if (index >= 0) {
			selectFeed(index, { focus: false });
		}
	}
});

document.addEventListener("keydown", (event) => {
	if (event.key === "Escape") {
		closeKeyboardHelp();
		return;
	}

	if (event.key === "?") {
		event.preventDefault();
		openKeyboardHelp();
		return;
	}

	const help = document.querySelector("#keyboard-help");
	if (help && !help.hidden) {
		return;
	}

	if (isTypingTarget(event.target)) {
		return;
	}

	const navigationLink = document.querySelector(
		`[data-keyboard-nav='${event.key}']`,
	);
	if (navigationLink) {
		event.preventDefault();
		navigateToUrl(navigationLink.href);
		return;
	}

	const hasFeeds = feedItems().length > 0;
	const card = selectedArticle();
	const feed = selectedFeed();

	if (event.key === "j") {
		event.preventDefault();
		if (hasFeeds) {
			moveFeedSelection(1);
		} else if (card) {
			moveSelection(1);
		}
	} else if (event.key === "k") {
		event.preventDefault();
		if (hasFeeds) {
			moveFeedSelection(-1);
		} else if (card) {
			moveSelection(-1);
		}
	} else if (event.key === "s") {
		event.preventDefault();
		card?.querySelector("form[data-action-type='save']")?.requestSubmit();
	} else if (event.key === "m") {
		event.preventDefault();
		card?.querySelector("form[data-action-type='mark-read']")?.requestSubmit();
	} else if (event.key === "o") {
		event.preventDefault();
		const link =
			card?.querySelector("[data-open-article]") ||
			feed?.querySelector("[data-open-feed]");
		if (link) {
			navigateToUrl(link.href, { allowExternal: true });
		}
	}
});

document.addEventListener("submit", (event) => {
	const form = event.target.closest("form[data-refresh-form]");
	if (!form) {
		return;
	}
	const button = form.querySelector("[data-refresh-button]");
	if (button) {
		button.disabled = true;
		button.textContent = "Refreshing…";
	}
});

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
		const card = form.closest(".article-card");
		const cardIndex = card ? articleCards().indexOf(card) : -1;
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
		const message = document.createElement("p");
		message.className = `message inline-message ${result.level || "success"}`;
		message.setAttribute("role", "status");
		message.textContent = result.message || "Done.";

		if (card && result.remove) {
			card.replaceWith(message);
			selectArticle(cardIndex, { focus: false });
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
