"""Small deterministic tokenizer: UTF-8 bytes plus three reserved tokens.

This keeps the first training implementation dependency-free and reproducible.
It is inefficient compared with a learned subword tokenizer and is not intended
as the final tokenizer for a production-scale model.
"""


class ByteTokenizer:
    PAD = 256
    BOS = 257
    EOS = 258
    VOCAB_SIZE = 259

    def encode(self, text, add_bos=False, add_eos=False):
        if not isinstance(text, str):
            raise TypeError("text must be str")
        tokens = list(text.encode("utf-8"))
        if add_bos:
            tokens.insert(0, self.BOS)
        if add_eos:
            tokens.append(self.EOS)
        return tokens

    def decode(self, tokens, skip_special=True):
        byte_values = []
        for token in tokens:
            value = int(token)
            if 0 <= value <= 255:
                byte_values.append(value)
            elif not skip_special:
                marker = {self.PAD: "<PAD>", self.BOS: "<BOS>", self.EOS: "<EOS>"}.get(value, "<UNK>")
                byte_values.extend(marker.encode("ascii"))
        return bytes(byte_values).decode("utf-8", errors="replace")
