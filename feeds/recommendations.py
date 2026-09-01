"""Deterministic, per-user article recommendations.

The ranker treats local ``SavedArticle`` rows as positive feedback and every
other article as implicit negative feedback. Publication and fetch dates are
intentionally absent from both the candidate filter and feature set: an old
article competes on exactly the same terms as a new one.
"""

from __future__ import annotations

import hashlib
import html
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

import numpy as np
from numpy.typing import NDArray
from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, Max
from django.db.models.functions import Substr
from django.utils.html import strip_tags
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression

from .models import Article, Category, Feed, SavedArticle
from .queries import read_article_ids

ALGORITHM_VERSION = "tfidf-logistic-mmr-v1"
MAX_SUMMARY_CHARS = 6_000
MAX_NOTES_CHARS = 2_000
MAX_FEATURES = 40_000
RERANK_POOL_SIZE = 500
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class ArticleDocument:
    article_id: int
    feed_id: int
    category_id: int | None
    url: str
    title: str
    author: str = ""
    summary: str = ""
    feed_title: str = ""
    category_title: str = ""


@dataclass(frozen=True)
class SavedSignal:
    article_id: int
    title: str
    notes: str = ""
    feed_title: str = ""
    category_title: str = ""
    interest_score: float | None = None


@dataclass(frozen=True)
class RankedRecommendation:
    article_id: int
    score: float
    reason: str
    matched_saved_article_id: int | None


@dataclass(frozen=True)
class RecommendationPage:
    cards: list[dict[str, Any]]
    profile_size: int


def recommendation_limit() -> int:
    return int(settings.RECOMMENDATION_ARTICLE_LIMIT)


def _clean_text(value: str, *, limit: int) -> str:
    return _SPACE.sub(" ", html.unescape(strip_tags(value or ""))).strip()[:limit]


def _document_text(document: ArticleDocument, signal: SavedSignal | None = None) -> str:
    title = _clean_text(document.title, limit=1_000)
    feed = _clean_text(document.feed_title, limit=500)
    category = _clean_text(document.category_title, limit=300)
    author = _clean_text(document.author, limit=300)
    summary = _clean_text(document.summary, limit=MAX_SUMMARY_CHARS)
    profile_title = ""
    profile_notes = ""
    profile_feed = ""
    profile_category = ""
    if signal is not None:
        profile_title = _clean_text(signal.title, limit=1_000)
        profile_notes = _clean_text(signal.notes, limit=MAX_NOTES_CHARS)
        profile_feed = _clean_text(signal.feed_title, limit=500)
        profile_category = _clean_text(signal.category_title, limit=300)

    # Exact metadata tokens let the model learn source affinity without making
    # source identity the whole recommendation. Repeating titles and notes gives
    # the reader's explicit signals more weight than long feed summaries.
    return " ".join(
        (
            title,
            title,
            title,
            profile_title,
            profile_title,
            profile_notes,
            profile_notes,
            feed,
            feed,
            profile_feed,
            category,
            category,
            profile_category,
            f"df_feed_{document.feed_id}",
            f"df_feed_{document.feed_id}",
            f"df_category_{document.category_id}",
            author,
            summary,
        )
    )


def _signal_weight(signal: SavedSignal) -> float:
    score = signal.interest_score
    if score is None or not math.isfinite(score):
        return 1.0
    score = min(5.0, max(0.0, score))
    return 0.25 + 1.25 * (score / 5.0)


def _canonical_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").casefold()
        port = parsed.port
    except ValueError:
        return value.casefold()
    if port is not None:
        hostname = f"{hostname}:{port}"
    return urlunsplit(
        (parsed.scheme.casefold(), hostname, parsed.path, parsed.query, "")
    )


def _canonical_title(value: str) -> str:
    return _clean_text(value, limit=1_000).casefold()


def _stable_key(document: ArticleDocument) -> tuple[str, str, int]:
    return (
        _canonical_title(document.title),
        _canonical_url(document.url),
        document.article_id,
    )


def _diversified_indices(
    *,
    documents: list[ArticleDocument],
    matrix: sparse.csr_matrix,
    scores: np.ndarray[Any, Any],
    candidate_indices: list[int],
    limit: int,
) -> list[int]:
    ordered = sorted(
        candidate_indices,
        key=lambda index: (-float(scores[index]), _stable_key(documents[index])),
    )

    # Duplicate URLs can enter through different feeds. Keep the strongest copy
    # before diversity work so one article never consumes several result slots.
    pool: list[int] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for index in ordered:
        canonical_url = _canonical_url(documents[index].url)
        canonical_title = _canonical_title(documents[index].title)
        if canonical_url in seen_urls or (
            len(canonical_title) >= 20 and canonical_title in seen_titles
        ):
            continue
        seen_urls.add(canonical_url)
        seen_titles.add(canonical_title)
        pool.append(index)
        if len(pool) == RERANK_POOL_SIZE:
            break
    if not pool:
        return []

    pool_scores = np.asarray([scores[index] for index in pool], dtype=np.float64)
    score_low = float(pool_scores.min())
    score_high = float(pool_scores.max())
    relevance: NDArray[np.float64]
    if score_high == score_low:
        relevance = np.ones(len(pool), dtype=np.float64)
    else:
        relevance = (pool_scores - score_low) / (score_high - score_low)

    available = np.ones(len(pool), dtype=bool)
    max_similarity = np.zeros(len(pool), dtype=np.float64)
    feed_counts: Counter[int] = Counter()
    category_counts: Counter[int | None] = Counter()
    selected: list[int] = []
    pool_matrix = matrix[pool]

    while available.any() and len(selected) < limit:
        adjusted = relevance * 0.88 - max_similarity * 0.12
        for position, index in enumerate(pool):
            if not available[position]:
                adjusted[position] = -np.inf
                continue
            document = documents[index]
            adjusted[position] -= min(0.30, 0.06 * feed_counts[document.feed_id])
            adjusted[position] -= min(
                0.08, 0.015 * category_counts[document.category_id]
            )

        # The pool was stably sorted before NumPy sees it, so argmax gives a
        # deterministic tie break without dates entering the ranking.
        chosen_position = int(np.argmax(adjusted))
        chosen_index = pool[chosen_position]
        selected.append(chosen_index)
        available[chosen_position] = False
        chosen = documents[chosen_index]
        feed_counts[chosen.feed_id] += 1
        category_counts[chosen.category_id] += 1
        similarities = (pool_matrix @ matrix[chosen_index].T).toarray().ravel()
        max_similarity = np.maximum(max_similarity, similarities)

    return selected


def _reason_for(
    *,
    document: ArticleDocument,
    document_index: int,
    matrix: sparse.csr_matrix,
    saved_indices: list[int],
    saved_signals: dict[int, SavedSignal],
    documents_by_id: dict[int, ArticleDocument],
    feed_save_counts: Counter[int],
    category_save_counts: Counter[int | None],
) -> tuple[str, int | None]:
    similarities = (matrix[document_index] @ matrix[saved_indices].T).toarray().ravel()
    weights = np.asarray(
        [
            _signal_weight(saved_signals[documents_by_id[index].article_id])
            for index in saved_indices
        ]
    )
    weighted = similarities * weights
    nearest_position = int(np.argmax(weighted))
    nearest_index = saved_indices[nearest_position]
    nearest_id = documents_by_id[nearest_index].article_id
    nearest_signal = saved_signals[nearest_id]

    saved_title = _clean_text(nearest_signal.title, limit=120)
    if weighted[nearest_position] >= 0.08 and saved_title:
        if len(saved_title) == 120:
            saved_title = f"{saved_title.rstrip()}…"
        return f"Because you saved “{saved_title}”.", nearest_id
    if feed_save_counts[document.feed_id] >= 2:
        return f"You often save articles from {document.feed_title}.", None
    if (
        document.category_id is not None
        and category_save_counts[document.category_id] >= 2
    ):
        return f"Matches your interest in {document.category_title}.", None
    return "Similar to articles you’ve saved.", nearest_id


def rank_recommendations(
    documents: list[ArticleDocument],
    saved_signals: list[SavedSignal],
    *,
    limit: int,
) -> list[RankedRecommendation]:
    """Rank all unsaved documents without using publication or fetch dates."""

    if limit < 1:
        raise ValueError("limit must be at least 1")
    if not documents or not saved_signals:
        return []

    documents_by_id = {
        document.article_id: index for index, document in enumerate(documents)
    }
    signals = {
        signal.article_id: signal
        for signal in saved_signals
        if signal.article_id in documents_by_id
    }
    saved_ids = set(signals)
    saved_titles = {
        title
        for article_id, signal in signals.items()
        for title in (
            _canonical_title(signal.title),
            _canonical_title(documents[documents_by_id[article_id]].title),
        )
        if title
    }
    candidate_indices = [
        index
        for index, document in enumerate(documents)
        if document.article_id not in saved_ids
        and _canonical_title(document.title) not in saved_titles
    ]
    if not signals or not candidate_indices:
        return []

    texts = [
        _document_text(document, signals.get(document.article_id))
        for document in documents
    ]
    vectorizer = TfidfVectorizer(
        strip_accents="unicode",
        lowercase=True,
        stop_words="english",
        ngram_range=(1, 2),
        min_df=2 if len(documents) >= 20 else 1,
        max_df=0.95 if len(documents) >= 20 else 1.0,
        max_features=MAX_FEATURES,
        sublinear_tf=True,
        norm="l2",
        dtype=np.float32,
    )
    try:
        matrix = cast(sparse.csr_matrix, vectorizer.fit_transform(texts))
    except ValueError:
        # HTML-only or punctuation-only corpora can have no word vocabulary.
        return []

    labels = np.asarray(
        [1 if document.article_id in saved_ids else 0 for document in documents],
        dtype=np.int8,
    )
    sample_weights = np.ones(len(documents), dtype=np.float64)
    for article_id, signal in signals.items():
        sample_weights[documents_by_id[article_id]] = _signal_weight(signal)

    classifier = LogisticRegression(
        C=1.0,
        class_weight="balanced",
        max_iter=500,
        solver="liblinear",
        random_state=20_260_901,
    )
    classifier.fit(matrix, labels, sample_weight=sample_weights)
    scores = classifier.predict_proba(matrix)[:, 1]
    selected_indices = _diversified_indices(
        documents=documents,
        matrix=matrix,
        scores=scores,
        candidate_indices=candidate_indices,
        limit=limit,
    )

    saved_indices = [documents_by_id[article_id] for article_id in signals]
    documents_by_index = {index: document for index, document in enumerate(documents)}
    feed_save_counts = Counter(
        documents[documents_by_id[article_id]].feed_id for article_id in signals
    )
    category_save_counts = Counter(
        documents[documents_by_id[article_id]].category_id for article_id in signals
    )
    results: list[RankedRecommendation] = []
    for index in selected_indices:
        document = documents[index]
        reason, matched_id = _reason_for(
            document=document,
            document_index=index,
            matrix=matrix,
            saved_indices=saved_indices,
            saved_signals=signals,
            documents_by_id=documents_by_index,
            feed_save_counts=feed_save_counts,
            category_save_counts=category_save_counts,
        )
        results.append(
            RankedRecommendation(
                article_id=document.article_id,
                score=float(scores[index]),
                reason=reason,
                matched_saved_article_id=matched_id,
            )
        )
    return results


def _corpus_fingerprint(user: Any) -> tuple[Any, ...]:
    articles = Article.objects.aggregate(count=Count("id"), latest=Max("updated_at"))
    saves = SavedArticle.objects.filter(user=user).aggregate(
        count=Count("id"), latest=Max("updated_at")
    )
    feeds = Feed.objects.aggregate(count=Count("id"), latest=Max("updated_at"))
    categories = Category.objects.aggregate(count=Count("id"), latest=Max("created_at"))
    return (
        ALGORITHM_VERSION,
        cast(int, user.pk),
        articles["count"],
        articles["latest"],
        saves["count"],
        saves["latest"],
        feeds["count"],
        feeds["latest"],
        categories["count"],
        categories["latest"],
        recommendation_limit(),
    )


def _cache_key(user: Any) -> str:
    digest = hashlib.sha256(repr(_corpus_fingerprint(user)).encode()).hexdigest()
    return f"daily-firehose:recommendations:{cast(int, user.pk)}:{digest}"


def _load_documents() -> list[ArticleDocument]:
    rows = (
        Article.objects.annotate(
            summary_excerpt=Substr("summary", 1, MAX_SUMMARY_CHARS)
        )
        .order_by("id")
        .values(
            "id",
            "feed_id",
            "feed__category_id",
            "url",
            "title",
            "author",
            "summary_excerpt",
            "feed__title",
            "feed__category__name",
        )
    )
    return [
        ArticleDocument(
            article_id=row["id"],
            feed_id=row["feed_id"],
            category_id=row["feed__category_id"],
            url=row["url"],
            title=row["title"],
            author=row["author"],
            summary=row["summary_excerpt"] or "",
            feed_title=row["feed__title"],
            category_title=row["feed__category__name"] or "",
        )
        for row in rows
    ]


def _load_saved_signals(user: Any) -> list[SavedSignal]:
    return [
        SavedSignal(
            article_id=row["article_id"],
            title=row["title"],
            notes=row["notes"],
            feed_title=row["feed__title"] or "",
            category_title=row["category__name"] or "",
            interest_score=row["interest_score"],
        )
        for row in SavedArticle.objects.filter(user=user)
        .order_by("article_id")
        .values(
            "article_id",
            "title",
            "notes",
            "feed__title",
            "category__name",
            "interest_score",
        )
    ]


def _rank_for_user(user: Any) -> tuple[list[RankedRecommendation], int]:
    saved_signals = _load_saved_signals(user)
    return (
        rank_recommendations(
            _load_documents(), saved_signals, limit=recommendation_limit()
        ),
        len(saved_signals),
    )


def recommendation_cards(user: Any) -> RecommendationPage:
    key = _cache_key(user)
    cached = cache.get(key)
    if cached is None:
        cached = _rank_for_user(user)
        cache.set(key, cached, timeout=int(settings.RECOMMENDATION_CACHE_SECONDS))
    ranked, profile_size = cast(tuple[list[RankedRecommendation], int], cached)
    if not ranked:
        return RecommendationPage(cards=[], profile_size=profile_size)

    ranked_by_id = {result.article_id: result for result in ranked}
    article_ids = list(ranked_by_id)
    # Even if a cache backend briefly serves an old fingerprint, a newly saved
    # article must never still appear as a recommendation.
    saved_ids = set(
        SavedArticle.objects.filter(user=user, article_id__in=article_ids).values_list(
            "article_id", flat=True
        )
    )
    article_ids = [
        article_id for article_id in article_ids if article_id not in saved_ids
    ]
    articles = list(
        Article.objects.select_related(
            "feed", "feed__category", "newsletter_issue"
        ).filter(id__in=article_ids)
    )
    articles_by_id = {cast(int, article.pk): article for article in articles}
    ordered_articles = [
        articles_by_id[article_id]
        for article_id in article_ids
        if article_id in articles_by_id
    ]
    read_ids = read_article_ids(
        user,
        Article.objects.filter(
            id__in=[cast(int, article.pk) for article in ordered_articles]
        ),
    )
    return RecommendationPage(
        cards=[
            {
                "article": article,
                "is_read": cast(int, article.pk) in read_ids,
                "is_saved": False,
                "recommendation_reason": ranked_by_id[cast(int, article.pk)].reason,
            }
            for article in ordered_articles
        ],
        profile_size=profile_size,
    )
