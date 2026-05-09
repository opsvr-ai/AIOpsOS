"""Promotion epsilon rule — task 22.5.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution``, task 22.5
(Phase K — Evaluator). Covers:

* **R-3.6** — Promotion transitions (``shadow → ab``, ``ab → active``)
  require ``candidate_score >= baseline_score - ε`` (ε=0.02).

This module isolates the *rule* itself from the Promoter so that:

* :mod:`src.services.evolution.promoter` (Phase L, task 23.x) has one
  canonical predicate to consult when deciding whether a candidate is
  allowed to advance — no second copy of the threshold drifts in
  elsewhere.
* Property-based tests can exercise the rule directly with pure
  floating-point inputs, without having to stand up the full candidate
  store, DB session, or Kafka stack.
* Admin tooling and the evolution control API (task 23.4) can reuse
  the same predicate for "dry-run: would this candidate pass the
  R-3.6 check?" queries without touching state.

The rule is intentionally one line of arithmetic. The bulk of what
lives here is the documentation — the ``ε=0.02`` constant is
specification-grade, and the comparison direction (``>=``, not ``>``)
is spec-grade too: a candidate that exactly ties the baseline minus
epsilon is allowed to promote, because the epsilon *is* the tolerance
we agreed to spend.
"""

from __future__ import annotations


PROMOTION_EPSILON: float = 0.02
"""Allowed score regression budget between baseline and candidate.

Sourced verbatim from R-3.6. A candidate may promote
(``shadow → ab`` or ``ab → active``) iff its latest
``skill_evaluations.candidate_score`` is no more than this far below
the paired ``baseline_score``.

Keep this a module-level ``float`` (not a ``Decimal``) so callers can
pass in either :class:`float` or :class:`decimal.Decimal` scores and
have Python coerce transparently via ``__ge__``/``__sub__``. The
comparison is *direction*-sensitive, not precision-sensitive — two
scores differing by less than ``1e-9`` are trivially within epsilon.
"""


def can_promote(
    baseline_score: float,
    candidate_score: float,
    epsilon: float = PROMOTION_EPSILON,
) -> bool:
    """Return ``True`` iff *candidate_score* is within ``epsilon`` of
    *baseline_score* (or better).

    Encodes R-3.6 as a pure function. Given the latest
    ``skill_evaluations`` row for a candidate, a Promoter may execute
    the state-machine edges ``shadow → ab`` or ``ab → active`` only
    when this predicate returns ``True``.

    The comparison is ``candidate_score >= baseline_score - epsilon``.
    It is deliberately *greater-or-equal*: at an exact tie on the
    boundary the candidate is allowed to promote. The epsilon is the
    full tolerance we agreed to spend, not a strict "must exceed"
    margin.

    Args:
        baseline_score: The paired ``baseline_score`` from the latest
            ``skill_evaluations`` row. Typically in ``[0.0, 1.0]`` but
            the rule does not enforce that — any caller handing in a
            score outside that range has upstream problems the
            Promoter cannot correct.
        candidate_score: The paired ``candidate_score`` from the same
            row.
        epsilon: Tolerance budget. Defaults to
            :data:`PROMOTION_EPSILON` (``0.02``); override only in
            tests that want to exercise the rule at a different
            threshold.

    Returns:
        ``True`` if the candidate is allowed to promote past this
        check, ``False`` otherwise.

    Notes:
        * ``candidate_score > baseline_score`` always returns ``True``
          — a candidate that strictly beats the baseline never
          trips the no-regression guard.
        * ``baseline_score == 1.0`` and ``candidate_score == 0.0``
          returns ``False`` — the maximum possible regression.
        * ``NaN`` on either side yields ``False`` because all
          comparisons against ``NaN`` are ``False``; this is the
          desired behaviour (we refuse to promote on unknown/broken
          scores rather than silently letting them through).
    """
    return candidate_score >= baseline_score - epsilon
