"""`{{...}}` references and branch conditions, parsed by hand.

**THERE IS NO `eval` HERE AND THERE MUST NOT BE ONE.** A workflow is authored by
an admin, but the VALUES that flow through it are tool output, and a tool result
is third-party text from a server somebody registered. `eval` over a string that
a remote server had any hand in is arbitrary code execution with extra steps.

**A REFERENCE IS A PATH EVALUATION, NOT A STRING SUBSTITUTION.** That distinction
is the whole security argument of this module, and it is enforced by one rule:

    a `{{...}}` reference must be the ENTIRE argument value.

`"{{검색.top.title}}"` is a reference. `"제목: {{검색.top.title}}"` is a template,
and a template is refused - at SAVE, not at run. Under substitution the next
tool's argument would be a string a third-party server wrote most of, and the
argument schema could no longer say what it is. Under path evaluation the value
is whatever the path pointed at, it must resolve to a single scalar, and a path
that lands on a dict or a list is a run-time failure rather than a `str()` of
somebody else's JSON.

Two further bounds on a resolved value, both here rather than at the call site so
that nothing has to remember them:

- it must be a scalar (`str`, `int`, `float`, `bool`) or `None`. A structure is
  refused - see above.
- a string is capped at `MAX_ARGUMENT_CHARS`. Without this, a tool returning two
  megabytes puts two megabytes into the NEXT tool's arguments, which is a bill
  and a denial of service against whoever is on the other end.

The condition language is JSON, not a string grammar, and that is deliberate
laziness: the only thing that genuinely needs parsing is the reference, so that
is the only parser written. `{"kind": "compare", "left": "{{a.count}}", "op":
">", "right": 0}` is what the canvas renders as `{{a.count}} > 0`.
"""

import re
from dataclasses import dataclass

# One reference, whole. The inner group is deliberately greedy-free and refuses
# a nested brace, so `{{a.{{b}}}}` is a malformed path rather than a clever one.
REFERENCE_RE = re.compile(r"^\{\{\s*([^{}]+?)\s*\}\}$")
# Anything that even LOOKS like it wants to be a reference. A value that trips
# this but not REFERENCE_RE is a template, and templates are refused.
SUSPECT_RE = re.compile(r"\{\{|\}\}")
# Hangul, latin, digits, underscore, hyphen. A segment is a node id or a field
# name; neither has any business carrying a dot, a brace or whitespace.
SEGMENT_RE = re.compile(r"^[\w가-힣-]+$", re.UNICODE)

# ponytail: a flat constant, not a Setting. It is a safety floor rather than a
# tuning knob - no deployment wants it larger - and making it configurable would
# invite an operator to raise it. Promote it to Settings if a real corpus ever
# needs a longer single argument.
MAX_ARGUMENT_CHARS = 2000

COMPARATORS = ("==", "!=", ">", ">=", "<", "<=")
# The structural set, and the LLM placeholder. `llm` is in the schema and is
# refused at save: a branch that costs a model call per question should be
# switched on by an owner who can see the price, not arrive as a side effect of
# somebody drawing a box. See the spec, section 2.
CONDITION_KINDS = ("compare", "exists", "empty", "and", "or", "not", "llm")

MIXED_REFERENCE_MESSAGE = "참조는 값 전체여야 합니다. 문자열 안에 섞어 쓸 수 없습니다: {name}"
BAD_PATH_MESSAGE = "참조 경로를 이해하지 못했습니다: {name}"
UNKNOWN_REFERENCE_MESSAGE = "아직 실행되지 않은 노드를 참조합니다: {name}"
NOT_A_SCALAR_MESSAGE = "참조가 값 하나로 풀리지 않았습니다: {name}"
TOO_LONG_MESSAGE = "참조한 값이 너무 깁니다(최대 {limit}자): {name}"
UNKNOWN_CONDITION_MESSAGE = "알 수 없는 분기 조건입니다: {name}"
LLM_CONDITION_MESSAGE = "모델 판단 분기(kind: llm)는 아직 켜져 있지 않습니다."
BAD_COMPARATOR_MESSAGE = "알 수 없는 비교 연산자입니다: {name}"
BAD_CONDITION_SHAPE_MESSAGE = "분기 조건의 모양이 올바르지 않습니다."


class ExpressionError(ValueError):
    """A reference or a condition that will not be evaluated.

    Raised at SAVE by `validate_graph` - where it becomes a Korean 400 - and at
    RUN by the executor, where it becomes a failed node rather than a dead run.
    """


@dataclass(frozen=True)
class Reference:
    """A parsed `{{a.b.c}}`. `raw` is kept for the message a failure prints."""

    raw: str
    segments: tuple[str, ...]


def parse_reference(value: object) -> Reference | None:
    """A reference, or None if this value does not contain one.

    Raises rather than returning None for a value that contains `{{` and is not
    exactly one reference: that is the template case, and letting it through as
    a literal would silently ship the substitution this module exists to refuse.
    """
    if not isinstance(value, str):
        return None
    match = REFERENCE_RE.match(value.strip())
    if match is None:
        if SUSPECT_RE.search(value):
            raise ExpressionError(MIXED_REFERENCE_MESSAGE.format(name=value[:100]))
        return None
    path = match.group(1)
    segments = tuple(part.strip() for part in path.split("."))
    if not segments or not all(SEGMENT_RE.match(part) for part in segments):
        raise ExpressionError(BAD_PATH_MESSAGE.format(name=path[:100]))
    return Reference(raw=value.strip(), segments=segments)


def references_in(value: object) -> list[Reference]:
    """Every reference inside an arguments object, one level of nesting deep.

    Used at save time to check that a node only names nodes that can precede it.
    A dict or list argument is walked; anything else is a literal.
    """
    found: list[Reference] = []
    if isinstance(value, dict):
        for item in value.values():
            found.extend(references_in(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(references_in(item))
    else:
        reference = parse_reference(value)
        if reference is not None:
            found.append(reference)
    return found


def _walk(scope: dict, reference: Reference) -> object:
    current: object = scope
    for segment in reference.segments:
        if isinstance(current, dict):
            if segment not in current:
                raise ExpressionError(UNKNOWN_REFERENCE_MESSAGE.format(name=reference.raw[:100]))
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit():
            index = int(segment)
            if index >= len(current):
                raise ExpressionError(UNKNOWN_REFERENCE_MESSAGE.format(name=reference.raw[:100]))
            current = current[index]
        else:
            raise ExpressionError(UNKNOWN_REFERENCE_MESSAGE.format(name=reference.raw[:100]))
    return current


def resolve(value: object, scope: dict) -> object:
    """One argument value with its references replaced by what they point AT.

    A scalar or None comes back. A dict or a list is walked and rebuilt, so a
    nested argument object works - but each individual LEAF is still a whole
    reference or a literal, never a template.
    """
    if isinstance(value, dict):
        return {key: resolve(item, scope) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, scope) for item in value]
    reference = parse_reference(value)
    if reference is None:
        return value
    resolved = _walk(scope, reference)
    # The two bounds from the module docstring. A dict or list here is exactly
    # the case that separates path evaluation from `str()`-ing somebody else's
    # JSON into an argument.
    if resolved is not None and not isinstance(resolved, str | int | float | bool):
        raise ExpressionError(NOT_A_SCALAR_MESSAGE.format(name=reference.raw[:100]))
    if isinstance(resolved, str) and len(resolved) > MAX_ARGUMENT_CHARS:
        raise ExpressionError(
            TOO_LONG_MESSAGE.format(limit=MAX_ARGUMENT_CHARS, name=reference.raw[:100])
        )
    return resolved


def _compare(left: object, op: str, right: object) -> bool:
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    # An ordering comparison between a string and a number raises TypeError in
    # Python 3, and the value on the left came out of a tool. Refuse it as a
    # condition failure rather than letting it escape as a TypeError.
    if not isinstance(left, int | float) or not isinstance(right, int | float):
        if not (isinstance(left, str) and isinstance(right, str)):
            raise ExpressionError(BAD_CONDITION_SHAPE_MESSAGE)
    if op == ">":
        return left > right  # type: ignore[operator]
    if op == ">=":
        return left >= right  # type: ignore[operator]
    if op == "<":
        return left < right  # type: ignore[operator]
    return left <= right  # type: ignore[operator]


def check_condition(condition: object) -> None:
    """Static check, at save time. Raises for a shape the evaluator would not
    understand, and for `kind: "llm"`, which is in the schema and not switched on."""
    if not isinstance(condition, dict):
        raise ExpressionError(BAD_CONDITION_SHAPE_MESSAGE)
    kind = condition.get("kind")
    if kind not in CONDITION_KINDS:
        raise ExpressionError(UNKNOWN_CONDITION_MESSAGE.format(name=str(kind)[:50]))
    if kind == "llm":
        raise ExpressionError(LLM_CONDITION_MESSAGE)
    if kind == "compare":
        op = condition.get("op")
        if op not in COMPARATORS:
            raise ExpressionError(BAD_COMPARATOR_MESSAGE.format(name=str(op)[:20]))
        parse_reference(condition.get("left"))
        parse_reference(condition.get("right"))
    elif kind in ("exists", "empty"):
        parse_reference(condition.get("of"))
    elif kind == "not":
        check_condition(condition.get("of"))
    else:  # and / or
        parts = condition.get("of")
        if not isinstance(parts, list) or not parts:
            raise ExpressionError(BAD_CONDITION_SHAPE_MESSAGE)
        for part in parts:
            check_condition(part)


def _resolve_operand(value: object, scope: dict) -> object:
    """Like `resolve`, but tolerant of a structure: `exists`/`empty` are the two
    operators whose whole job is to ask about one."""
    reference = parse_reference(value)
    if reference is None:
        return value
    return _walk(scope, reference)


def evaluate(condition: object, scope: dict) -> bool:
    """Which way a branch goes. `check_condition` has already run at save time,
    but this re-checks the shape rather than trusting it: a graph row can be
    edited in the database, and a stored graph outlives the code that saved it."""
    if not isinstance(condition, dict):
        raise ExpressionError(BAD_CONDITION_SHAPE_MESSAGE)
    kind = condition.get("kind")
    if kind == "compare":
        op = condition.get("op")
        if op not in COMPARATORS:
            raise ExpressionError(BAD_COMPARATOR_MESSAGE.format(name=str(op)[:20]))
        return _compare(resolve(condition.get("left"), scope), op, resolve(condition.get("right"), scope))
    if kind == "exists":
        try:
            value = _resolve_operand(condition.get("of"), scope)
        except ExpressionError:
            # 존재함 on a path that does not exist is False, not an error. That
            # is the entire question it was asked.
            return False
        return value is not None
    if kind == "empty":
        try:
            value = _resolve_operand(condition.get("of"), scope)
        except ExpressionError:
            return True
        if value is None:
            return True
        if isinstance(value, str | list | dict):
            return len(value) == 0
        return False
    if kind == "not":
        return not evaluate(condition.get("of"), scope)
    if kind in ("and", "or"):
        parts = condition.get("of")
        if not isinstance(parts, list) or not parts:
            raise ExpressionError(BAD_CONDITION_SHAPE_MESSAGE)
        results = [evaluate(part, scope) for part in parts]
        return all(results) if kind == "and" else any(results)
    if kind == "llm":
        raise ExpressionError(LLM_CONDITION_MESSAGE)
    raise ExpressionError(UNKNOWN_CONDITION_MESSAGE.format(name=str(kind)[:50]))
