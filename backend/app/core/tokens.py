import tiktoken

_ENCODING = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(encode_tokens(text))


def encode_tokens(text: str) -> list[int]:
    # disallowed_special=(): tiktoken defaults to disallowed_special="all", which
    # raises ValueError on any text spelling out a special token - "<|endoftext|>"
    # is ordinary prose in technical writing about LLMs, so this is at least as
    # likely to be an accident as an attack. The default made the failure sticky:
    # it breaks chunking at ingest, and once such a chunk is indexed every request
    # that retrieves it 500s the same way. These functions measure length and slice
    # text; they never build a model's input array, so there is nothing here for a
    # special token to mean.
    return _ENCODING.encode(text, disallowed_special=())


def decode_tokens(token_ids: list[int]) -> str:
    return _ENCODING.decode(token_ids)
