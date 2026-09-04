"""The reference-dependent document: cut on its own numbering, carrying the
clause that governs it, and delivering what it cites.

The Korean fixture below is 상표심사기준 p.89 verbatim, because that page is the
measured failure the feature exists for - the chunk that answers "상표등록출원서에
등록대상은 뭘로 기재해?" is "3. 상표 / 4. 지정상품 ...", it contains neither
상표등록출원서 nor 제36조 nor a verb, and the governing clause is three chunks away
where neither the dense arm nor +/-1 neighbour expansion can reach it.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.chunk import EMBEDDING_DIM, Chunk
from app.models.chunk_edge import ChunkEdge
from app.models.collection import Collection
from app.models.document import Document
from app.models.user import User
from app.rag.blocks import Block
from app.rag.chunking.base import ChunkCandidate, ChunkingStrategy
from app.rag.chunking.fixed import FixedChunking
from app.rag.chunking.hierarchy import (
    HierarchicalChunking,
    detect,
    find_citations,
    resolve_citation,
    resolve_scheme,
)
from app.rag.pipeline import process_document
from app.retrieval.evidence import RetrievedChunk
from app.retrieval.references import attach
from app.retrieval.vector_store import PgVectorStore

KOREAN_LEGAL = {"strategy": "hierarchical", "preset": "korean_legal"}
SCHEME = resolve_scheme(KOREAN_LEGAL)

# Big enough that nothing here is ever cut for budget unless a test says so.
WIDE_BUDGET = 100_000


async def fake_embed_fn(texts: list[str]) -> list[list[float]]:
    return [[1.0, 0.0, 0.0] for _ in texts]


# 상표심사기준 p.89, as the module docstring of app/rag/chunking/hierarchy.py
# quotes it: one governing clause and six enumeration items that say nothing on
# their own.
P89 = [
    Block(text="제3장 상표등록출원서류", block_type="heading", page=89),
    Block(
        text=(
            "제36조(상표등록출원) ① 상표등록을 받으려는 자는 다음 각 호의 사항을 적은 "
            "상표등록출원서를 지식재산처장에게 제출하여야 한다."
        ),
        block_type="paragraph",
        page=89,
    ),
    Block(
        text="1. 출원인의 성명 및 주소(법인인 경우에는 그 명칭 및 영업소의 소재지)",
        block_type="list_item",
        page=89,
    ),
    Block(
        text="2. 출원인의 대리인이 있는 경우에는 그 대리인의 성명 및 주소나 영업소의 소재지",
        block_type="list_item",
        page=89,
    ),
    Block(text="3. 상표", block_type="list_item", page=89),
    Block(
        text='4. 지정상품 및 산업통상자원부령으로 정하는 상품류(이하 "상품류"라 한다)',
        block_type="list_item",
        page=89,
    ),
    Block(
        text="5. 제46조제3항에 따른 사항(우선권을 주장하는 경우만 해당한다)", block_type="list_item", page=89
    ),
    Block(text="6. 그 밖에 산업통상자원부령으로 정하는 사항", block_type="list_item", page=89),
]

# The next 항 of the same 조. Written long enough to be body rather than a title,
# which is what makes its label in `section` read "②" and not the whole sentence.
HANG_2 = Block(
    text=(
        "② 제1항에 따른 상표등록출원서에는 산업통상자원부령으로 정하는 상표견본을 첨부하여야 "
        "한다. 다만, 제1항의 경우는 예외로 한다."
    ),
    block_type="paragraph",
    page=90,
)


def _hierarchical(**kwargs) -> HierarchicalChunking:
    return HierarchicalChunking(SCHEME, **kwargs)


# --- the ancestor line --------------------------------------------------------


async def test_an_enumeration_item_carries_the_clause_that_governs_it():
    """THE measured failure. "3. 상표 / 4. 지정상품 및 ... 상품류" is the answer to a
    question about 상표등록출원서 and holds none of those words; the governing
    sentence is three chunks away, so neither the dense arm nor +/-1 neighbour
    expansion can reach it. Without the ancestor line inside the chunk there is
    nothing here for a sentence-shaped question to be near."""
    candidates = await _hierarchical(target_chars=240).chunk(P89, fake_embed_fn)

    item = next(c for c in candidates if "3. 상표" in c.content)
    assert "4. 지정상품" in item.content
    assert "상표등록출원서" in item.content
    assert "제36조" in item.content
    assert "다음 각 호의 사항" in item.content
    # Every piece of the run, not only the first: the tail piece ("6. 그 밖에 ...")
    # is exactly the one that was unreachable.
    pieces = [c for c in candidates if c.metadata.get("path") == "조36"]
    assert len(pieces) > 1, [c.content for c in pieces]
    assert all("상표등록출원서를 지식재산처장에게" in c.content for c in pieces)


async def test_a_sibling_paragraph_opens_its_own_chunk_under_the_same_article():
    """② is a provision in its own right, so it must not be glued to ①'s six
    items - and it must still say which 조 it belongs to, or it is the same
    orphan the feature exists to fix."""
    candidates = await _hierarchical(target_chars=240).chunk([*P89, HANG_2], fake_embed_fn)

    second = next(c for c in candidates if c.metadata.get("path") == "조36/항2")
    assert "제36조(상표등록출원)" in second.content
    # The ancestor is carried as a LABEL, so ①'s body does not ride along.
    assert "다음 각 호의 사항" not in second.content
    assert "1. 출원인의 성명" not in second.content
    assert second.page == 90


async def test_the_section_of_a_chunk_is_its_position_not_the_last_numbered_item():
    """`section` is what a citation shows the user. For this page it used to read
    "6. 그 밖에 산업통상자원부령으로 정하는 사항" - a numbered item the parser's
    heading heuristic mistook for the title of the NEXT chunk, and which names
    nothing."""
    candidates = await _hierarchical(target_chars=240).chunk([*P89, HANG_2], fake_embed_fn)

    clause = next(c for c in candidates if c.metadata.get("path") == "조36")
    assert clause.section == "제3장 상표등록출원서류 > 제36조(상표등록출원)"
    second = next(c for c in candidates if c.metadata.get("path") == "조36/항2")
    assert second.section == "제3장 상표등록출원서류 > 제36조(상표등록출원) > ②"
    assert all(c.section != "6. 그 밖에 산업통상자원부령으로 정하는 사항" for c in candidates)


# --- the heading that is only a heading ----------------------------------------


async def test_a_heading_its_own_children_repeat_is_not_a_chunk():
    """"제3장 상표등록출원서류" opens a level and has no body of its own, and every
    chunk under it already begins with that exact line. MEASURED: 742 such chunks
    in this corpus, 196 of 특허법's 1,019, and five of them held one of the eight
    delivered slots across the fixture - two of them the same line twice."""
    candidates = await _hierarchical(target_chars=240).chunk([*P89, HANG_2], fake_embed_fn)

    assert not any(c.content == "제3장 상표등록출원서류" for c in candidates)
    # And it is not LOST: it is the head of every chunk that sits under it.
    assert all(c.content.startswith("제3장 상표등록출원서류") for c in candidates)


async def test_a_heading_that_names_a_section_nothing_opens_under_is_kept():
    """The other half of the rule, and the reason it is not "drop every opener
    with no body": 특허·실용신안 심사기준's contents page is 574 chapter titles with
    nothing beneath them, and each is the only thing naming its chapter."""
    blocks = [
        Block(text="제9장 보칙", block_type="heading", page=1),
        Block(text="제10장 벌칙", block_type="heading", page=1),
        Block(text="제200조(벌칙) 이를 위반한 자는 처벌한다.", block_type="paragraph", page=1),
    ]

    candidates = await _hierarchical().chunk(blocks, fake_embed_fn)

    assert any(c.content == "제9장 보칙" for c in candidates)
    assert not any(c.content == "제10장 벌칙" for c in candidates)


async def test_an_opener_carrying_its_own_clause_is_kept_even_with_children():
    """상표심사기준 sets 제6조 and its ① on ONE line and the ② on the next. The
    child's ancestor line trims a line that long back to the marker and the
    title, so ①'s sentence exists nowhere else - dropping this run would delete
    it from the corpus. The test is not "has no body", it is "is repeated below"."""
    blocks = [
        Block(
            text=(
                "제6조(재외자의 상표관리인) ① 국내에 주소나 영업소가 없는 자는 그 재외자의 "
                "상표관리인에 의해서만 상표에 관한 절차를 밟을 수 있다."
            ),
            block_type="paragraph",
            page=1,
        ),
        Block(
            text="② 상표관리인은 위임된 권한의 범위에서 본인을 대리한다.",
            block_type="paragraph",
            page=1,
        ),
    ]

    candidates = await _hierarchical().chunk(blocks, fake_embed_fn)

    assert any("상표관리인에 의해서만" in c.content for c in candidates)


# --- citations ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "path", "law", "relative"),
    [
        ("제12조", (("조", "12"),), "", False),
        ("제1조제1항", (("조", "1"), ("항", "1")), "", False),
        ("제46조제3항", (("조", "46"), ("항", "3")), "", False),
        # The sub-article number is written AFTER the marker, so it cannot be one
        # contiguous capture - and dropping it would make 제5조의2 resolve to 제5조,
        # a different article.
        ("제5조의2제2항", (("조", "5의2"), ("항", "2")), "", False),
        ("[특법54(3)]", (("조", "54"), ("항", "3")), "특법", False),
        ("[민법950]", (("조", "950"),), "민법", False),
        # No 조 of its own: this one is completed by the chunk that wrote it.
        ("제1항", (("항", "1"),), "", True),
    ],
)
def test_a_citation_states_its_own_depth(written, path, law, relative):
    """There is no "reference depth" setting because the string already answered
    the question. A depth read from configuration would be wrong for every
    citation of a different shape in the same document."""
    citation = find_citations(f"자세한 내용은 {written}에 따른다.", SCHEME)[0]

    assert citation.label == written
    assert citation.path == path
    # A `law` group is what marks a citation as pointing OUT of this document.
    assert citation.law == law
    assert bool(citation.law) is (law != "")
    assert citation.relative is relative


def test_an_article_citation_is_not_read_a_second_time_as_a_bare_paragraph():
    """The 조-citation pattern and the relative-reference pattern overlap: without
    the span guard, "제36조제1항" is read once as itself and again as a bare
    "제1항", which then resolves against the CITING chunk's 조 - a confident edge
    to the wrong article."""
    assert [c.label for c in find_citations("제36조제1항에 따른 출원서", SCHEME)] == ["제36조제1항"]


def test_a_relative_reference_resolves_against_the_citing_chunks_own_article():
    """The owner's own case: "[1조1항] ... 제1항의 경우는 예외로 한다". Written inside
    제36조제2항, "제1항" means 제36조제1항 and nothing else.

    It is completed by LEVEL, not by list length. A length-based slice takes the
    citing path's first len-1 components, which is right here by luck and wrong
    the moment the citing chunk is itself only 조-deep."""
    citation = find_citations("다만, 제1항의 경우는 예외로 한다.", SCHEME)[0]

    # The document opens a chunk for 제36조 and one for its ②, but none for ① - the
    # index has no "조36/항1" to land on, so the longest prefix that exists wins.
    index = {"조36": 3, "조36/항2": 9, "조46": 12}
    assert resolve_citation(citation, (("조", "36"), ("항", "2")), index, SCHEME) == 3
    # And a chunk with no 조 of its own has nothing to complete it WITH, so the
    # citation is unresolvable rather than absolute: a bare 항1 lookup lands on
    # whichever ① some other chapter happens to open with.
    assert resolve_citation(citation, (), index, SCHEME) is None


# --- detection ----------------------------------------------------------------


def _statute(articles: int = 12) -> list[Block]:
    """A hierarchy that cross-references itself: what the two 심사기준 look like."""
    blocks: list[Block] = []
    for i in range(articles):
        blocks.append(
            Block(
                text=f"제{36 + i}조(제목{i}) ① 제46조제3항에 따른 사항은 다음 각 호와 같다.",
                block_type="paragraph",
            )
        )
        blocks.append(Block(text=f"1. 항목 {i}", block_type="list_item"))
    return blocks


def _prose(count: int = 40) -> list[Block]:
    return [
        Block(text=f"토마토 역병은 감염된 토양과 튀는 물을 통해 퍼진다. 사례 {i}.", block_type="paragraph")
        for i in range(count)
    ]


def test_a_document_that_cross_references_its_own_provisions_is_reference_dependent():
    """Both ratios clear the bar: it has a hierarchy AND it points at it."""
    detection = detect(_statute(), SCHEME)

    assert detection.confidence == "high"
    assert detection.character == "reference_dependent"
    # The counts are returned with the verdict because they are stored on the
    # document row and RENDERED. This project has already shipped one automatic
    # decision nobody could see.
    assert detection.levels == {"조": 12, "호": 12}
    assert detection.blocks == 24
    assert detection.spine_ratio == 0.5
    assert detection.as_json()["detected"] == "reference_dependent"


def test_plain_prose_is_self_contained_and_says_so_with_a_count_of_nothing():
    """A research report with no numbering has nothing to hang an ancestor line
    on. "none" is the true answer here, not a miss."""
    detection = detect(_prose(), SCHEME)

    assert detection.confidence == "none"
    assert detection.character == "self_contained"
    assert detection.levels == {}
    assert detection.spine_ratio == 0.0
    assert detection.citation_ratio == 0.0


def test_a_handful_of_quoted_provisions_in_a_manual_lands_in_the_ambiguous_band():
    """기술분야별 심사실무가이드, measured: ten 조 openers in 8,576 blocks and a
    citation rate under the bar. The band falls to self_contained on purpose - a
    document wrongly left alone retrieves exactly as it does today, while one
    wrongly promoted gets a WRONG governing clause stamped on every chunk."""
    blocks = [
        *[Block(text=f"제{n}조(인용된 조문 {n}) 인용한다.", block_type="paragraph") for n in range(1, 11)],
        *[Block(text="출원인은 제36조에 따른 출원서를 제출한다.", block_type="paragraph") for _ in range(20)],
        *_prose(970),
    ]

    detection = detect(blocks, SCHEME)

    assert detection.confidence == "ambiguous"
    assert detection.character == "self_contained"
    assert detection.blocks == 1000
    assert detection.levels["조"] == 10
    assert detection.spine_ratio == 0.01
    assert detection.citation_ratio == 0.02


def test_an_article_heading_is_not_counted_as_a_citation_of_itself():
    """ "제36조(상표등록출원)" matches the citation pattern perfectly. Counting it
    would make every hierarchical document look maximally cross-referenced
    whether it cites anything or not - the ratio would measure the level patterns
    rather than the document."""
    blocks = [
        Block(text=f"제{n}조(제목 {n}) 이 조의 내용은 별표와 같다.", block_type="paragraph")
        for n in range(1, 21)
    ]

    detection = detect(blocks, SCHEME)

    assert detection.levels["조"] == 20
    assert detection.citation_ratio == 0.0
    # A numbered handbook that points at nothing: hierarchy alone is not enough.
    assert detection.confidence == "ambiguous"
    assert detection.character == "self_contained"


# --- the prose between the provisions -----------------------------------------


class RecordingStrategy(ChunkingStrategy):
    """Records the blocks it was handed and emits one candidate per block, so a
    test can tell which cutter saw what."""

    def __init__(self):
        self.seen: list[list[str]] = []

    async def chunk(self, blocks, embed_fn):
        self.seen.append([block.text for block in blocks])
        return [
            ChunkCandidate(content=b.text, token_count=1, char_count=len(b.text), page=b.page) for b in blocks
        ]


async def test_the_prose_between_the_provisions_keeps_the_configured_strategy():
    """99 조 openers in 5,878 blocks of 특허·실용신안 심사기준: the overwhelming
    majority of a reference-dependent document is ordinary examination prose, and
    re-cutting it with a size pass instead of the deployment's own strategy would
    move every boundary the 52-question fixture was measured on - a retrieval
    regression dressed up as a structure feature."""
    prose = RecordingStrategy()
    preamble = [
        Block(text="이 심사기준은 상표법의 해석과 적용에 관한 지침이다.", block_type="paragraph"),
        Block(text="개정 연혁은 부록에 정리되어 있다.", block_type="paragraph"),
    ]

    candidates = await _hierarchical(prose=prose, target_chars=240).chunk([*preamble, *P89], fake_embed_fn)

    assert prose.seen == [[block.text for block in preamble]]
    # And what the prose strategy produced is untouched - no ancestor prefix, no
    # hierarchy metadata.
    assert candidates[0].content == preamble[0].text
    assert candidates[0].metadata == {}
    assert any(c.metadata.get("strategy") == "hierarchical" for c in candidates)


async def test_a_document_with_no_numbering_at_all_is_left_entirely_to_prose():
    """Selecting the hierarchy for a collection is the user's business. Silently
    re-cutting a document that turns out to have no hierarchy would be ours."""
    prose = RecordingStrategy()

    candidates = await _hierarchical(prose=prose).chunk(_prose(5), fake_embed_fn)

    assert prose.seen == [[b.text for b in _prose(5)]]
    assert all(c.metadata == {} for c in candidates)


# --- the same machinery, no Korean in it --------------------------------------

CONTRACT = {
    "strategy": "hierarchical",
    "levels": [
        ["article", r"^Article\s+(?P<n>\d+)\."],
        ["clause", r"^\((?P<n>[a-z])\)(?=\s)"],
        ["item", r"^\((?P<n>\d+)\)(?=\s)"],
    ],
    "addressable": ["article", "clause", "item"],
    "break_level": "clause",
    # The capture group names ARE the contract: a group named for an addressable
    # level contributes that component of the target path.
    "citations": [r"Article\s+(?P<article>\d+)(?:\s*\((?P<clause>[a-z])\))?"],
}

CONTRACT_BLOCKS = [
    Block(text="Article 5. Termination", block_type="heading", page=3),
    Block(
        text="(a) Either party may terminate this agreement on thirty days written notice.",
        block_type="paragraph",
        page=3,
    ),
    Block(text="(1) Notice must be delivered to the address in Schedule 1.", block_type="list_item", page=3),
    Block(
        text="(b) Termination under Article 5(a) does not affect accrued rights.",
        block_type="paragraph",
        page=3,
    ),
]


async def test_the_levels_and_the_citations_are_configuration_not_korean():
    """An English contract, supplied as a `chunking` dict with no preset. The
    preset is cultural knowledge and it is DATA; if a document numbered "Article 5
    / (a) / (1)" needs a code change, the generalisation did not happen."""
    scheme = resolve_scheme(CONTRACT)

    candidates = await HierarchicalChunking(scheme, target_chars=400).chunk(CONTRACT_BLOCKS, fake_embed_fn)

    clause = next(c for c in candidates if c.metadata.get("path") == "article5/clausea")
    assert clause.content.startswith("Article 5. Termination > (a) Either party may terminate")
    assert clause.section == "Article 5. Termination > (a)"
    # (1) is deeper than the break level, so it extends the path rather than
    # opening a chunk of ten characters under a longer prefix.
    assert "(1) Notice must be delivered" in clause.content

    citation = find_citations(CONTRACT_BLOCKS[3].text, scheme)[0]
    assert citation.label == "Article 5(a)"
    assert citation.path == (("article", "5"), ("clause", "a"))
    index = {c.metadata["path"]: i for i, c in enumerate(candidates) if c.metadata.get("path")}
    assert (
        resolve_citation(citation, (("article", "5"), ("clause", "b")), index, scheme)
        == index["article5/clausea"]
    )


# --- the pipeline -------------------------------------------------------------

# The same p.89 page as a FILE, plus enough neighbouring provisions for the
# detector to have something to count. 제40조 and 제41조 cite each other, which is
# the citation CYCLE the delivery-time walk has to terminate on.
STATUTE = "\n".join(
    [
        *[block.text for block in P89],
        HANG_2.text,
        "제37조(절차의 보정) 지식재산처장은 제36조제1항에 따른 출원서가 방식에 맞지 아니한 "
        "경우에는 기간을 정하여 보정을 명하여야 한다.",
        "제38조(대리권의 증명) 대리인의 대리권은 서면으로 증명하여야 하며, [민법950]에 따른 제한을 받는다.",
        "제39조(준용) 제38조의 경우에는 [특법54(3)]을 준용한다.",
        "제40조(상호 참조) 이 조에 따른 처리절차는 제41조에 따른다.",
        "제41조(역참조) 이 조에 따른 처리절차는 제40조에 따른다.",
        "제42조(수수료) 출원인은 산업통상자원부령으로 정하는 수수료를 내야 한다.",
        "제43조(출원공고) 지식재산처장은 출원공고를 하여야 한다.",
        "제44조(열람) 누구든지 출원서류의 열람을 청구할 수 있다.",
        "제45조(비밀유지) 심사관은 직무상 알게 된 비밀을 누설하여서는 아니 된다.",
        "제46조(조약에 따른 우선권 주장) ① 조약당사국에 출원한 자는 우선권을 주장할 수 있다.",
        "③ 제1항에 따라 우선권을 주장한 자는 최초의 출원서 등본을 제출하여야 한다.",
        # 제48조's own line is a BARE HEADING, so it is no longer a chunk of its
        # own and "제48조" is a path nothing opens. 제49조 cites it anyway.
        "제48조(수수료의 반환)",
        "① 잘못 낸 수수료는 청구에 따라 반환하여야 한다.",
        "제49조(반환절차) 수수료의 반환은 제48조에 따른다.",
    ]
)

PROSE = "토마토 역병은 감염된 토양과 튀는 물을 통해 퍼진다.\n작물을 돌려짓고 잔재물을 제거해야 한다.\n" * 20


class FakeLLMProvider:
    """Full-width deterministic vectors. Nothing in this file reaches the
    network."""

    async def embed(self, texts):
        return [[0.1, 0.2, 0.3] + [0.0] * (EMBEDDING_DIM - 3) for _ in texts]

    async def chat(self, messages, **kwargs):
        raise NotImplementedError


class RecordingFixed(FixedChunking):
    """The deployment's own prose strategy, instrumented so a test can prove the
    document was cut by IT and not by the hierarchy."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls = 0

    async def chunk(self, blocks, embed_fn):
        self.calls += 1
        return await super().chunk(blocks, embed_fn)


@pytest_asyncio.fixture
async def collection(db):
    user = User(email="hierarchy@example.com", password_hash="x", role="admin")
    db.add(user)
    await db.flush()
    collection = Collection(name="심사기준", created_by=user.id)
    db.add(collection)
    await db.flush()
    await db.commit()
    return collection


async def _upload(db, collection, tmp_path, text: str, name: str, structure: dict | None = None) -> Document:
    source = tmp_path / name
    source.write_text(text, encoding="utf-8")
    document = Document(
        collection_id=collection.id,
        filename=name,
        file_type="txt",
        size_bytes=source.stat().st_size,
        storage_path=str(source),
        status="uploaded",
        structure=structure or {},
        uploaded_by=collection.created_by,
    )
    db.add(document)
    await db.commit()
    return document


async def _process(db, document, fallback) -> None:
    await process_document(
        db,
        PgVectorStore(db),
        FakeLLMProvider(),
        HierarchicalChunking(SCHEME, max_chunk_tokens=1300, target_chars=1000),
        str(document.id),
        scheme=SCHEME,
        fallback=fallback,
    )


async def _chunks(db, document) -> list[Chunk]:
    return list(
        (
            await db.execute(
                select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.chunk_index)
            )
        ).scalars()
    )


async def test_a_self_contained_document_is_cut_by_the_fallback_not_by_the_hierarchy(
    db, collection, tmp_path
):
    """The collection supplies the VOCABULARY; whether this document uses it is
    answered per document. The `일반` collection of this deployment already holds
    특허·실용신안 심사기준 beside 연구보고서 A, so a collection-wide verdict is
    guaranteed wrong."""
    document = await _upload(db, collection, tmp_path, PROSE, "연구보고서.txt")
    fallback = RecordingFixed(chunk_size=200, overlap=20)

    await _process(db, document, fallback)

    await db.refresh(document)
    assert document.status == "indexed"
    assert document.structure["character"] == "self_contained"
    assert document.structure["confidence"] == "none"
    assert fallback.calls == 1
    chunks = await _chunks(db, document)
    assert chunks
    assert all(c.chunk_metadata.get("strategy") != "hierarchical" for c in chunks)
    # No paths to resolve against, so recording every citation as unresolved
    # would put a misleading "0 of N resolved" on the document screen.
    assert "citations" not in document.structure
    assert await db.scalar(select(ChunkEdge.id).where(ChunkEdge.document_id == document.id)) is None


async def test_an_override_beats_detection_and_survives_the_run(db, collection, tmp_path):
    """Somebody who corrected a verdict must not have to correct it again after
    every re-ingest, and the screen has to be able to show both numbers at once -
    which is why `detected` is stored separately from `character`."""
    document = await _upload(
        db, collection, tmp_path, STATUTE, "상표심사기준.txt", structure={"override": "self_contained"}
    )
    fallback = RecordingFixed(chunk_size=200, overlap=20)

    await _process(db, document, fallback)

    await db.refresh(document)
    assert document.structure["override"] == "self_contained"
    assert document.structure["character"] == "self_contained"
    # What the CONTENT said is still recorded, unchanged by the correction.
    assert document.structure["detected"] == "reference_dependent"
    assert document.structure["confidence"] == "high"
    # And the override decided the cut, not just the label.
    assert fallback.calls == 1
    chunks = await _chunks(db, document)
    assert all(c.chunk_metadata.get("strategy") != "hierarchical" for c in chunks)


# --- edges, and delivering what a chunk cites ---------------------------------


@pytest_asyncio.fixture
async def statute(db, collection, tmp_path):
    document = await _upload(db, collection, tmp_path, STATUTE, "상표심사기준.txt")
    await _process(db, document, RecordingFixed(chunk_size=200, overlap=20))
    await db.refresh(document)
    return {"document": document, "chunks": await _chunks(db, document)}


def _find(statute, needle: str) -> Chunk:
    return next(c for c in statute["chunks"] if needle in c.content)


def _retrieved(chunk: Chunk) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=str(chunk.id),
        document_id=str(chunk.document_id),
        filename="상표심사기준.txt",
        content=chunk.content,
        page=chunk.page,
        section=chunk.section,
        chunk_index=chunk.chunk_index,
    )


async def test_a_citation_naming_another_law_is_recorded_and_not_resolved(db, statute):
    """[민법950] names a statute this corpus does not hold. Dropping the row would
    leave the document screen unable to say "N found, M resolved", and that
    sentence is the whole reason a user can trust what was inferred - so the edge
    exists with no destination, and the label survives exactly as written."""
    source = _find(statute, "제38조(대리권의 증명)")

    edge = (
        await db.execute(
            select(ChunkEdge).where(ChunkEdge.src_chunk_id == source.id, ChunkEdge.label == "[민법950]")
        )
    ).scalar_one()

    assert edge.kind == "ref"
    assert edge.dst_chunk_id is None
    assert edge.target_path == "조950"
    structure = statute["document"].structure
    assert structure["citations"]["unresolved"] >= 1
    assert "[민법950]" in structure["unresolved_examples"]


async def test_a_retrieved_chunk_arrives_with_the_text_of_what_it_cites(db, statute):
    """Baking the target into the chunk would copy 제46조제3항 into every chunk that
    mentions it and let the corpus rank on the same sentences dozens of times. A
    reference is a graph, and a graph is traversed - at delivery."""
    citing = _find(statute, "제37조(절차의 보정)")
    cited = _find(statute, "상표등록출원서를 지식재산처장에게")
    item = _retrieved(citing)

    await attach(db, [item], token_budget=WIDE_BUDGET, query="출원서 보정")

    assert "다음 각 호의 사항을 적은 상표등록출원서" in item.content
    assert item.content.startswith(citing.content)
    assert "[제36조제1항]" in item.content
    # IDENTITY IS UNCHANGED, exactly as neighbour expansion has it: an enlarged
    # item is still one citation at one position.
    assert item.chunk_id == str(citing.id)
    assert item.page == citing.page
    assert item.section == citing.section
    assert [n["chunk_id"] for n in item.neighbors] == [str(cited.id)]
    assert item.neighbors[0]["reason"] == "ref:제36조제1항"


async def test_a_citation_to_an_article_lands_on_the_clause_that_opens_it(db, statute):
    """제48조 writes nothing on its own line, so there is no chunk at 조48 to point
    at any more - and "제48조" still has to resolve, or half the corpus's citations
    go dark. MEASURED before the prefix index existed: 3,026 of 5,923 `ref` edges
    pointed at a heading-only chunk, which delivered an article TITLE to the model
    and spent one of the two reference slots doing it."""
    citing = _find(statute, "제49조(반환절차)")
    item = _retrieved(citing)

    await attach(db, [item], token_budget=WIDE_BUDGET, query="수수료 반환")

    assert "잘못 낸 수수료는 청구에 따라 반환하여야 한다" in item.content
    assert item.neighbors[0]["reason"] == "ref:제48조"


async def test_a_budget_too_small_attaches_nothing(db, statute):
    """The budget is a ceiling on the WHOLE evidence set, the same one neighbour
    expansion honours: an item that would have reached the model without this
    still reaches it, and this can only lose its own additions."""
    citing = _find(statute, "제37조(절차의 보정)")
    item = _retrieved(citing)

    await attach(db, [item], token_budget=5, query="출원서 보정")

    assert item.content == citing.content
    assert item.neighbors == []


async def test_a_citation_cycle_terminates(db, statute):
    """제40조 cites 제41조 which cites 제40조 back, which real statutes do. The
    recursive CTE carries a `visited` array for exactly this; without it the walk
    does not return."""
    citing = _find(statute, "제40조(상호 참조)")
    cited = _find(statute, "제41조(역참조)")
    item = _retrieved(citing)

    await attach(db, [item], token_budget=WIDE_BUDGET, query="처리절차", depth=2)

    assert [n["chunk_id"] for n in item.neighbors] == [str(cited.id)]
    assert item.content.count("제41조(역참조)") == 1


async def test_a_document_with_no_edges_is_left_alone(db, collection, tmp_path):
    """attach runs on every search, including over collections that were never
    chunked on a hierarchy."""
    document = await _upload(db, collection, tmp_path, PROSE, "연구보고서.txt")
    await _process(db, document, RecordingFixed(chunk_size=200, overlap=20))
    chunks = await _chunks(db, document)
    items = [_retrieved(chunk) for chunk in chunks]
    before = [item.content for item in items]

    await attach(db, items, token_budget=WIDE_BUDGET, query="토마토 역병")

    assert [item.content for item in items] == before
    assert all(item.neighbors == [] for item in items)


# --- 법명 스코프와 문서 간 해소 ----------------------------------------------


def test_citations_after_a_bracketed_law_name_belong_to_that_law():
    """"「특허법」 제3조부터 제7조까지, 제28조의 규정을 준용한다" - 낫표 뒤의 조문
    인용은 전부 그 법의 것이다. 실용신안법 제3조의 실제 문형이고, 이 귀속이
    없으면 준용 선언은 영원히 자기 문서 안에서만 해소를 시도한다."""
    text = "실용신안에 관하여는 「특허법」 제3조부터 제7조까지, 제28조의 규정을 준용한다."

    cites = {c.label: c for c in find_citations(text, SCHEME)}

    assert cites["「특허법」 제3조"].law == "특허법"
    assert cites["「특허법」 제7조"].law == "특허법"
    assert cites["「특허법」 제28조"].law == "특허법"
    assert all(not c.relative for c in cites.values())


def test_the_law_scope_ends_at_이_법():
    """"…「특허법」 제64조에 따라 출원공개되거나 이 법 제21조제3항에 따라…" -
    실측 문장. "이 법"이 스코프를 되돌리지 않으면 제21조가 특허법 것으로 붙고,
    잘못된 조문은 없는 조문보다 나쁘다."""
    text = "「특허법」 제64조에 따라 출원공개되거나 이 법 제21조제3항에 따라 등록공고된 것"

    cites = {tuple(c.path): c for c in find_citations(text, SCHEME)}

    assert cites[(("조", "64"),)].law == "특허법"
    assert cites[(("조", "21"), ("항", "3"))].law == ""


def test_a_relative_reference_inside_a_law_scope_stays_relative():
    """조 없는 제N항은 낯선 법에 결정론적으로 붙을 자리가 없다 - 스코프가
    열려 있어도 상대참조는 자기 문서의 조에 대고 해소된다."""
    text = "「특허법」 제64조를 준용한다. 제1항의 요건을 갖추어야 한다."

    relative = next(c for c in find_citations(text, SCHEME) if c.relative)

    assert relative.law == ""


PATENT_ACT = "\n".join(
    [
        "제1장 총칙",
        "제64조(출원공개) ① 지식재산처장은 출원일부터 1년 6개월이 지나면 출원공개를 하여야 한다.",
        "제65조(보상금청구권) ① 출원인은 경고 후 보상금을 청구할 수 있다.",
    ]
)

UM_ACT = "\n".join(
    [
        "제1장 총칙",
        "제20조(「특허법」의 준용) 실용신안의 출원공개에 관하여는 「특허법」 제64조를 준용한다.",
        "제21조(등록공고) 지식재산처장은 등록공고를 하여야 한다.",
    ]
)


@pytest_asyncio.fixture
async def two_statutes(db, collection, tmp_path):
    # 픽스처가 판정 최소 블록 수보다 작으므로 성격을 명시한다 - 검사 대상은
    # 판정이 아니라 문서 간 해소다.
    override = {"override": "reference_dependent"}
    patent = await _upload(db, collection, tmp_path, PATENT_ACT, "특허법.txt", structure=override)
    await _process(db, patent, RecordingFixed(chunk_size=200, overlap=20))
    um = await _upload(db, collection, tmp_path, UM_ACT, "실용신안법.txt", structure=override)
    await _process(db, um, RecordingFixed(chunk_size=200, overlap=20))
    return {
        "patent": {"document": patent, "chunks": await _chunks(db, patent)},
        "um": {"document": um, "chunks": await _chunks(db, um)},
    }


async def test_a_citation_naming_a_law_in_the_corpus_crosses_documents(db, two_statutes):
    """문서↔법령 동일성은 추측이 아니라 파일명이다: "특허법.txt"가 코퍼스에
    있으므로 「특허법」 제64조는 그 문서의 조64를 여는 청크에 잇는다. 이 간선이
    생기기 전에는 코퍼스의 문서 간 해소 간선이 정확히 0개였다."""
    declaring = next(c for c in two_statutes["um"]["chunks"] if "준용한다" in c.content)
    target = next(c for c in two_statutes["patent"]["chunks"] if "출원공개를 하여야" in c.content)

    edge = (
        await db.execute(
            select(ChunkEdge).where(
                ChunkEdge.src_chunk_id == declaring.id,
                ChunkEdge.label == "「특허법」 제64조",
            )
        )
    ).scalar_one()

    assert edge.kind == "ref"
    assert edge.dst_chunk_id == target.id


async def test_a_provision_arrives_with_the_declaration_that_준용s_it(db, two_statutes):
    """역방향. 준용 선언("「특허법」 제64조를 준용한다")은 조문 번호 나열이라
    질문과 겹치는 어휘가 없어 검색으로 도달하지 못한다 - 특허법 조문이 선택되면
    그것을 인용하는 다른 법의 선언이 함께 도착해야 실용신안 질문에 그 조문을
    쓸 수 있다는 사실이 모델에게 전해진다."""
    target = next(c for c in two_statutes["patent"]["chunks"] if "출원공개를 하여야" in c.content)
    declaring = next(c for c in two_statutes["um"]["chunks"] if "준용한다" in c.content)
    item = RetrievedChunk(
        chunk_id=str(target.id),
        document_id=str(target.document_id),
        filename="특허법.txt",
        content=target.content,
        page=target.page,
        section=target.section,
        chunk_index=target.chunk_index,
    )

    await attach(db, [item], token_budget=WIDE_BUDGET, query="실용신안 출원공개")

    assert "[이 조문을 인용: 「특허법」 제64조]" in item.content
    assert "준용한다" in item.content
    reverse = [n for n in item.neighbors if str(n["reason"]).startswith("cited-by:")]
    assert [n["chunk_id"] for n in reverse] == [str(declaring.id)]
