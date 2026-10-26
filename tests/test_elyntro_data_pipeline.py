from elyntro_model.data import SOURCES, normalize_sft


def test_dataset_sources_include_provenance_and_license():
    assert "wikipedia-es" in SOURCES
    assert "spanish-sft" in SOURCES
    for source in SOURCES.values():
        assert source["repo"]
        assert source["url"].startswith("https://")
        assert source["license"]


def test_normalize_sft_messages_schema():
    result = normalize_sft({"messages": [
        {"role": "user", "content": "¿Qué es Elyntro?"},
        {"role": "assistant", "content": "Es un proyecto de IA."},
    ]})
    assert result["messages"][0]["role"] == "user"
    assert result["messages"][1]["content"] == "Es un proyecto de IA."


def test_normalize_sft_instruction_schema():
    result = normalize_sft({"instruction": "Resume", "input": "Un texto", "output": "Resumen"})
    assert result["messages"][0]["role"] == "user"
    assert "Un texto" in result["messages"][0]["content"]
    assert result["messages"][1]["content"] == "Resumen"


def test_normalize_sft_rejects_empty_rows():
    assert normalize_sft({"foo": "bar"}) is None
