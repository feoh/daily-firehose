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
		target.closest("input, textarea, select, button") || target.isContentEditable,
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
	const navFold = document.querySelector(".nav-fold");
	const mobileNavigation = window.matchMedia("(max-width: 42rem)");
	const syncNavigation = () => {
		if (!navFold) {
			return;
		}
		if (mobileNavigation.matches) {
			navFold.removeAttribute("open");
		} else {
			navFold.setAttribute("open", "");
		}
	};
	syncNavigation();
	mobileNavigation.addEventListener("change", syncNavigation);

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

	if (isTypingTarget(event.target)) {
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

	// Held action keys emit repeats; after a card is removed, a repeat would act
	// on the next article even though the user only intended one action.
	if (event.repeat && ["s", "m"].includes(event.key)) {
		event.preventDefault();
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

document.addEventListener("submit", (event) => {
	const form = event.target.closest("form[data-feed-discovery-form]");
	if (!form) {
		return;
	}
	if (form.dataset.discoveryPending === "true") {
		event.preventDefault();
		return;
	}
	form.dataset.discoveryPending = "true";
	form.setAttribute("aria-busy", "true");
	const button = form.querySelector("[data-feed-discovery-submit]");
	const status = form.querySelector("[data-feed-discovery-status]");
	if (button) {
		button.disabled = true;
		button.textContent = "Finding feed…";
	}
	if (status) {
		status.textContent = "Checking this URL for a valid feed.";
	}
});

const ACTION_STATUS_DURATION_MS = 8000;
let actionStatusTimer = null;

const actionStatusRegion = () => {
	let region = document.querySelector("[data-action-status]");
	if (!region) {
		region = document.createElement("div");
		region.className = "action-status";
		region.dataset.actionStatus = "";
		region.setAttribute("role", "status");
		region.setAttribute("aria-live", "polite");
		region.setAttribute("aria-atomic", "true");
		document.body.append(region);
	}
	return region;
};

const clearActionStatus = () => {
	window.clearTimeout(actionStatusTimer);
	actionStatusTimer = null;
	const region = document.querySelector("[data-action-status]");
	if (!region) {
		return;
	}
	const hadFocus = region.contains(document.activeElement);
	region.replaceChildren();
	if (hadFocus) {
		focusShortcutTarget();
	}
};

const scheduleActionStatusClear = () => {
	window.clearTimeout(actionStatusTimer);
	actionStatusTimer = window.setTimeout(() => {
		// Never yank the Undo button away from someone who has focused it.
		const region = document.querySelector("[data-action-status]");
		if (region?.matches(":focus-within")) {
			scheduleActionStatusClear();
			return;
		}
		clearActionStatus();
	}, ACTION_STATUS_DURATION_MS);
};

// One shared, self-clearing status replaces per-card confirmations so removed
// cards leave no residue and the next article moves into the tapped position.
const showActionStatus = (text, { level = "success", undo = null } = {}) => {
	const region = actionStatusRegion();
	const message = document.createElement("p");
	message.className = `message action-status-message ${level}`;
	const label = document.createElement("span");
	label.className = "action-status-text";
	label.textContent = text;
	message.append(label);
	if (undo) {
		const undoButton = document.createElement("button");
		undoButton.type = "button";
		undoButton.className = "action-status-undo";
		undoButton.textContent = "Undo";
		undoButton.addEventListener("click", () => undo(undoButton));
		message.append(undoButton);
	}
	region.replaceChildren(message);
	scheduleActionStatusClear();
};

const showCardMessage = (card, text, { level = "success", role = "status" }) => {
	card.querySelector(":scope > .inline-message")?.remove();
	const message = document.createElement("p");
	message.className = `message inline-message ${level}`;
	message.setAttribute("role", role);
	message.textContent = text;
	card.prepend(message);
};

const undoMarkAction = (form, card, placeholder) => async function undo(
	undoButton,
) {
	undoButton.disabled = true;
	const body = new FormData(form);
	const state = body.get("state");
	body.set("state", state === "read" ? "unread" : "read");
	body.delete("remove_on_success");
	try {
		const response = await fetch(form.action, {
			method: "POST",
			body,
			headers: { "X-Requested-With": "XMLHttpRequest" },
			credentials: "same-origin",
		});
		if (!response.ok) {
			throw new Error(`Request failed with status ${response.status}`);
		}
		await response.json();
	} catch (error) {
		showActionStatus("Sorry, undo failed. Please try again.", {
			level: "error",
			undo,
		});
		return;
	}

	card.classList.remove("is-folding");
	form.dataset.actionPending = "false";
	const button = form.querySelector("button[type='submit']");
	if (button) {
		button.disabled = false;
	}
	if (placeholder.isConnected) {
		placeholder.replaceWith(card);
		selectArticle(articleCards().indexOf(card));
	}
	showActionStatus("Restored the article.");
};

document.addEventListener("submit", async (event) => {
	const form = event.target.closest("form[data-article-action]");
	if (!form) {
		return;
	}

	event.preventDefault();
	if (form.dataset.actionPending === "true") {
		return;
	}
	form.dataset.actionPending = "true";

	const button = form.querySelector("button[type='submit']");
	if (button) {
		button.disabled = true;
	}

	try {
		const card = form.closest(".article-card");
		const cardIndex = card ? articleCards().indexOf(card) : -1;
		if (
			(form.dataset.articleId &&
				card?.dataset.articleId &&
				form.dataset.articleId !== card.dataset.articleId) ||
			(form.dataset.articleUrl &&
				card?.dataset.articleUrl &&
				form.dataset.articleUrl !== card.dataset.articleUrl)
		) {
			throw new Error("Article action verification failed");
		}
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
		if (
			result.article &&
			(String(result.article.id) !== card?.dataset.articleId ||
				result.article.url !== card?.dataset.articleUrl)
		) {
			throw new Error("Article response verification failed");
		}
		const text = result.message || "Done.";
		const level = result.level || "success";

		if (card && result.remove) {
			const hadFocus = card.contains(document.activeElement);
			if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
				card.classList.add("is-folding");
				await new Promise((resolve) => window.setTimeout(resolve, 180));
			}
			card.querySelector(":scope > .inline-message")?.remove();
			const placeholder = document.createComment("removed article");
			card.replaceWith(placeholder);
			const next = selectArticle(cardIndex, { focus: false });
			if (hadFocus) {
				(next || document.querySelector("#main-content"))?.focus({
					preventScroll: true,
				});
			}
			const undo =
				form.dataset.actionType === "mark-read"
					? undoMarkAction(form, card, placeholder)
					: null;
			showActionStatus(text, { level, undo });
		} else if (card) {
			showCardMessage(card, text, { level });
			form.dataset.actionPending = "false";
			if (button) {
				button.disabled = false;
			}
		}
	} catch (error) {
		const card = form.closest(".article-card");
		if (card) {
			showCardMessage(card, "Sorry, that action failed. Please try again.", {
				level: "error",
				role: "alert",
			});
		}
		form.dataset.actionPending = "false";
		if (button) {
			button.disabled = false;
		}
	}
});
