"""Model providers: demo embeddings/chat plus the OpenAI-compatible code paths."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from app import providers
from app.config import settings


# ------------------------------------------------------------------- tokens

def test_tokens_drop_stopwords_and_single_letters():
    assert providers._tokens("The a an of and to") == []
    assert providers._tokens("Refund policy") == ["refund", "policy"]


def test_tokens_lowercase_and_keep_numbers():
    assert providers._tokens("HTTP 404 Error") == ["http", "404", "error"]


def test_tokens_split_chinese_into_unigrams():
    tokens = providers._tokens("退款政策")
    assert tokens == ["退", "款", "政", "策"]


def test_tokens_mix_latin_and_chinese():
    assert set(providers._tokens("退款 policy")) == {"退", "款", "policy"}


def test_tokens_ignore_punctuation_only_input():
    assert providers._tokens("!!! ??? ...") == []


# ------------------------------------------------------- demo embeddings

def test_demo_embedding_shape_and_normalisation():
    vectors = providers.DemoEmbedding().embed(["alpha beta", "gamma delta", ""])
    assert vectors.shape == (3, 512)
    assert vectors.dtype == np.float32

    norms = np.linalg.norm(vectors, axis=1)
    assert norms[0] == pytest.approx(1.0, abs=1e-5)
    assert norms[1] == pytest.approx(1.0, abs=1e-5)
    assert norms[2] == 0.0  # no tokens => zero vector


def test_demo_embedding_is_deterministic():
    engine = providers.DemoEmbedding()
    first = engine.embed(["refund policy within 30 days"])
    second = providers.DemoEmbedding().embed(["refund policy within 30 days"])
    np.testing.assert_array_equal(first, second)


def test_demo_embedding_ranks_lexical_overlap_higher():
    engine = providers.DemoEmbedding()
    query, related, unrelated = engine.embed(
        ["refund policy", "our refund policy allows returns", "shipping takes five days"]
    )
    assert float(query @ related) > float(query @ unrelated)


def test_demo_embedding_supports_custom_dimension():
    assert providers.DemoEmbedding(dim=64).embed(["anything"]).shape == (1, 64)


def test_embed_texts_uses_local_vectors_in_demo_mode():
    vectors = providers.embed_texts(["one two", "three four"])
    assert vectors.shape == (2, 512)
    assert np.linalg.norm(vectors, axis=1) == pytest.approx([1.0, 1.0], abs=1e-5)


def test_embed_texts_handles_empty_input():
    assert providers.embed_texts([]).shape[0] == 0


# ------------------------------------------------------------- demo chat

CONTEXTS = [
    {
        "content": (
            "Our refund policy allows returns within 30 days of purchase. "
            "Refunds are processed in 5 business days."
        )
    }
]


def test_demo_chat_answers_with_matching_sentences():
    answer = providers.DemoChat().answer("How long is the refund policy?", CONTEXTS)
    assert answer.startswith("Based on your knowledge base:")
    assert "30 days" in answer
    assert answer.count("•") >= 1


def test_demo_chat_caps_the_number_of_bullets():
    context = [{"content": " ".join(f"Refund policy rule {i} applies here." for i in range(8))}]
    answer = providers.DemoChat().answer("refund policy rule", context)
    assert answer.count("•") <= 3


def test_demo_chat_ignores_short_sentences():
    answer = providers.DemoChat().answer("refund?", [{"content": "Refund ok."}])
    assert "could not find anything relevant" in answer


def test_demo_chat_reports_when_nothing_matches():
    answer = providers.DemoChat().answer("quantum chromodynamics", CONTEXTS)
    assert "could not find anything relevant" in answer


def test_demo_chat_handles_empty_context():
    assert "could not find anything relevant" in providers.DemoChat().answer("refund", [])


def test_demo_chat_prompts_for_a_question_when_input_is_empty():
    answer = providers.DemoChat().answer("the and of", CONTEXTS)
    assert answer == "Please ask a question about the documents in your knowledge base."


def test_demo_chat_deduplicates_repeated_sentences():
    context = [{"content": "Refund policy is 30 days. Refund policy is 30 days."}]
    answer = providers.DemoChat().answer("refund policy", context)
    assert answer.count("Refund policy is 30 days") == 1


def test_demo_chat_stream_yields_word_by_word():
    text = "two words"
    assert "".join(providers.DemoChat().stream(text)) == "two words "


def test_demo_chat_stemming_matches_plurals():
    assert providers.DemoChat._stemmed("orders") == {"order"}
    # too short to be stripped, and stopwords never reach the stemmer
    assert providers.DemoChat._stemmed("gas") == {"gas"}
    assert providers.DemoChat._stemmed("is") == set()


# ------------------------------------------------------- provider dispatch

def test_demo_chat_skips_sentences_made_only_of_stopwords():
    context = [{"content": "It is not that they were here, and it is what it was then."}]
    assert "could not find anything relevant" in providers.DemoChat().answer("same one", context)


def test_chat_once_uses_the_demo_engine_without_a_key():
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "refund policy?"}]
    assert providers.chat_once(messages, CONTEXTS).startswith("Based on your knowledge base:")


def test_chat_stream_uses_the_demo_engine_without_a_key():
    messages = [{"role": "user", "content": "refund policy?"}]
    text = "".join(providers.chat_stream(messages, CONTEXTS))
    assert "30 days" in text


def test_chat_once_without_contexts_still_answers():
    messages = [{"role": "user", "content": "refund policy?"}]
    assert "could not find anything relevant" in providers.chat_once(messages)


# ------------------------------------------------- OpenAI-compatible client

class _FakeChatClient:
    """Minimal stand-in for `openai.OpenAI` used by the non-demo code paths."""

    def __init__(self, reply: str = "model answer", chunks=("model ", "answer")):
        self.calls: list[dict] = []
        self._reply = reply
        self._chunks = chunks
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                if kwargs.get("stream"):
                    return iter(
                        [
                            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=c))])
                            for c in outer._chunks
                        ]
                        + [
                            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))]),
                            SimpleNamespace(choices=[]),
                        ]
                    )
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=outer._reply))]
                )

        self.chat = SimpleNamespace(completions=_Completions())


@pytest.fixture()
def live_mode(monkeypatch, patch_settings):
    """Pretend a chat model is configured and wire in a fake client."""
    fake = _FakeChatClient()

    with patch_settings(llm_api_key="sk-test", llm_model="test-model"):
        monkeypatch.setattr(providers, "_get_chat_client", lambda: fake)
        yield fake


def test_chat_once_hits_the_configured_model(live_mode):
    messages = [{"role": "user", "content": "hi"}]
    assert providers.chat_once(messages, CONTEXTS) == "model answer"

    call = live_mode.calls[0]
    assert call["model"] == "test-model"
    assert call["messages"] is messages
    assert call["temperature"] == pytest.approx(0.2)
    assert "stream" not in call


def test_chat_stream_concatenates_deltas_and_skips_empty_ones(live_mode):
    messages = [{"role": "user", "content": "hi"}]
    assert "".join(providers.chat_stream(messages)) == "model answer"
    assert live_mode.calls[0]["stream"] is True


def test_chat_once_propagates_provider_errors(live_mode, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(live_mode.chat.completions, "create", boom)
    with pytest.raises(RuntimeError):
        providers.chat_once([{"role": "user", "content": "hi"}], CONTEXTS)


def test_embed_texts_uses_the_api_when_configured(monkeypatch, patch_settings):
    calls = []

    class _Embeddings:
        def create(self, model, input):
            calls.append((model, input))
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[3.0, 4.0]) for _ in input]
            )

    fake = SimpleNamespace(embeddings=_Embeddings())
    monkeypatch.setattr(providers, "_get_embed_client", lambda: fake)

    with patch_settings(embedding_api_key="sk-embed", embedding_model="text-embedding-3-small"):
        vectors = providers.embed_texts(["a", "b"])

    assert calls == [("text-embedding-3-small", ["a", "b"])]
    assert vectors.shape == (2, 2)
    np.testing.assert_allclose(vectors, np.array([[0.6, 0.8], [0.6, 0.8]], dtype=np.float32))


def test_embedding_dimension_setting_is_independent_of_the_demo_engine():
    # demo vectors are 512-d while the configured dimension defaults to 1536;
    # rag.retrieve pads both sides, so the mismatch must stay tolerated.
    assert providers.embed_texts(["x"]).shape[1] == 512
    assert settings.embedding_dim >= 512


# ------------------------------------------------------- client construction

def test_chat_client_is_built_from_the_llm_settings(monkeypatch, patch_settings):
    monkeypatch.setattr(providers, "_chat_client", None)

    with patch_settings(llm_api_key="sk-chat", llm_base_url="https://llm.example.test/v1"):
        client = providers._get_chat_client()
        again = providers._get_chat_client()

    assert client is again  # cached in the module global
    assert str(client.base_url).startswith("https://llm.example.test/v1")
    assert client.api_key == "sk-chat"
    providers._chat_client = None


def test_embed_client_is_built_from_the_embedding_settings(monkeypatch, patch_settings):
    monkeypatch.setattr(providers, "_openai_client", None)

    with patch_settings(embedding_api_key="sk-embed", embedding_base_url="https://embed.example.test/v1"):
        client = providers._get_embed_client()

    assert str(client.base_url).startswith("https://embed.example.test/v1")
    assert client.api_key == "sk-embed"
    providers._openai_client = None
