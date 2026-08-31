"""The tokenizer the sparse arm uses at BOTH ingest and query time.

That agreement is the entire contract of a lexical index: `content_tsv` holds
whatever tokenizer wrote it, and a query built by a different one asks for
lexemes no row ever stored - a silent false negative with a healthy-looking
Bitmap Index Scan in the plan. So both sides call this module, and changing
`settings.sparse_tokenizer` requires re-running scripts/backfill_tsv.py.

WHY BIGRAM IS THE DEFAULT (spec section S3, measured on the live 2578-chunk
manual with the 52-question fixture, ts_rank scorer, top_n=14):

                          sparse-only   fused   tokenizer-group   ms/q   new dep
    simple (whitespace)         0.673   0.846             0.750     24         -
    rule-based josa split       0.808   0.904             0.750     24      none
    bigram                      0.904   0.962             1.000     71      none
    morphemes (kiwipiepy)       0.923   0.942             1.000     31     30 MB

The tokenizer mattered far more than the scorer: fixing only the scorer bought
+0.058, fixing only the tokenizer bought +0.231. bigram wins because Korean josa
are glued to the noun - '상표등록출원이나' and '상표등록출원' are two unrelated
tokens to a whitespace tokenizer, and share seven bigrams. It beats morphemes by
one question out of 52 (not significant); what actually decides it is that it
needs no new dependency and is six lines.

ponytail: bigram tsvectors run ~380 lexemes per chunk, so the sparse arm costs
71 ms at 2578 chunks against 24 ms for 'simple'. That is noise beside the answer
completion today and a problem at 10x the corpus. The upgrade path is morpheme
analysis - already measured at 0.942 fused, 31 ms, 3.0 MB with kiwipiepy - and
it needs a third entry here plus a backfill, nothing else.
"""

import re
from collections.abc import Callable

# The reference implementation is the token stream `to_tsvector('simple', ...)`
# produces, which is what this column held before 0012. Hangul is spelled out
# rather than leaning on \w: \w under re.UNICODE also admits every other script's
# letters and marks, which would quietly change what a "word" is.
_WORD = re.compile(r"[0-9a-zA-Z가-힣]+")


def simple(text: str) -> list[str]:
    """Whitespace/word tokens, lowercased - the pre-0012 behaviour."""
    return _WORD.findall(text.lower())


def bigram(text: str) -> list[str]:
    """Character bigrams per word token; a token of length <= 2 is emitted whole.

    Semantics copied exactly from `ngrams(text, 2)` in scripts/eval_retrieval.py,
    because every number in the docstring above came out of that function and a
    subtly different one invalidates them.
    """
    out: list[str] = []
    for token in _WORD.findall(text.lower()):
        if len(token) <= 2:
            out.append(token)
        else:
            out.extend(token[i : i + 2] for i in range(len(token) - 1))
    return out


TOKENIZERS: dict[str, Callable[[str], list[str]]] = {"simple": simple, "bigram": bigram}


def tokenize(text: str, name: str) -> list[str]:
    """Dispatch by name. KeyError on an unknown name is deliberate: silently
    falling back to 'simple' would build an index nothing queries correctly."""
    return TOKENIZERS[name](text)
