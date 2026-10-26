import pytest

torch = pytest.importorskip("torch")
from elyntro_model import ByteTokenizer, ElyntroLanguageModel, ModelConfig


def tiny_model():
    return ElyntroLanguageModel(ModelConfig(block_size=16, n_layer=2, n_head=2, n_embd=16, dropout=0.0))


def test_byte_tokenizer_round_trips_spanish_utf8():
    tokenizer = ByteTokenizer()
    text = "Elyntro: acción, niño, ¿qué tal?"
    assert tokenizer.decode(tokenizer.encode(text)) == text
    assert tokenizer.encode("A", add_bos=True, add_eos=True) == [257, 65, 258]


def test_model_forward_has_logits_and_finite_loss():
    model = tiny_model()
    x, y = torch.randint(0, 256, (2, 12)), torch.randint(0, 256, (2, 12))
    logits, loss = model(x, y)
    assert logits.shape == (2, 12, 259)
    assert loss is not None and torch.isfinite(loss)


def test_model_rejects_context_longer_than_block_size():
    with pytest.raises(ValueError, match="exceeds block_size"):
        tiny_model()(torch.zeros((1, 17), dtype=torch.long))


def test_generation_extends_prompt_and_restores_training_state():
    model = tiny_model()
    model.train()
    output = model.generate(torch.tensor([[257, 72, 105]]), max_new_tokens=3, temperature=1.0, top_k=5, eos_token_id=None)
    assert output.shape == (1, 6)
    assert model.training is True


def test_config_rejects_non_divisible_heads():
    with pytest.raises(ValueError, match="divisible"):
        ModelConfig(n_embd=10, n_head=3)
