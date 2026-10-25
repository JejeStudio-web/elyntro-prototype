"""Small demo of scoped contextual memory."""
from pathlib import Path
from tempfile import TemporaryDirectory

from elyntro import MemoryStore

with TemporaryDirectory() as directory:
    memory = MemoryStore(Path(directory) / "elyntro.db")
    memory.add(
        namespace="demo-user",
        content="Prefers concise technical explanations with concrete examples",
        memory_type="explicit",
        tags=["communication", "style"],
    )
    print(memory.retrieve("demo-user", "How should I explain a technical concept?"))
