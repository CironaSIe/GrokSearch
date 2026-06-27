from pydantic import BaseModel, Field
from typing import Optional, Literal
import uuid


class IntentOutput(BaseModel):
    core_question: str = Field(description="Distilled core question in one sentence")
    query_type: Literal["factual", "comparative", "exploratory", "analytical"] = Field(
        description="factual=single answer, comparative=A vs B, exploratory=broad understanding, analytical=deep reasoning"
    )
    time_sensitivity: Literal["realtime", "recent", "historical", "irrelevant"] = Field(
        description="realtime=today, recent=days/weeks, historical=months+, irrelevant=timeless"
    )
    domain: Optional[str] = Field(default=None, description="Specific domain if identifiable")
    premise_valid: Optional[bool] = Field(default=None, description="False if the question contains a flawed assumption")
    ambiguities: Optional[list[str]] = Field(default=None, description="Unresolved ambiguities that may affect search direction")
    unverified_terms: Optional[list[str]] = Field(
        default=None,
        description="External classifications, rankings, or taxonomies that may be incomplete or outdated "
        "in training data (e.g., 'CCF-A', 'Fortune 500', 'OWASP Top 10'). "
        "Each should become a prerequisite sub-query in Phase 3."
    )


class ComplexityOutput(BaseModel):
    level: Literal[1, 2, 3] = Field(
        description="1=simple (1-2 searches), 2=moderate (3-5 searches), 3=complex (6+ searches)"
    )
    estimated_sub_queries: int = Field(ge=1, le=20)
    estimated_tool_calls: int = Field(ge=1, le=50)
    justification: str


class SubQuery(BaseModel):
    id: str = Field(description="Unique identifier (e.g., 'sq1')")
    goal: str
    expected_output: str = Field(description="What a successful result looks like")
    tool_hint: Optional[str] = Field(default=None, description="Suggested tool: web_search | web_fetch | web_map")
    boundary: str = Field(description="What this sub-query explicitly excludes — MUST state mutual exclusion with sibling sub-queries, not just the broader domain")
    depends_on: Optional[list[str]] = Field(default=None, description="IDs of prerequisite sub-queries")


class SearchTerm(BaseModel):
    term: str = Field(description="Search query string. MUST be ≤8 words. Drop redundant synonyms (e.g., use 'RAG' not 'RAG retrieval augmented generation').")
    purpose: str = Field(description="Single sub-query ID this term serves (e.g., 'sq2'). ONE term per sub-query — do NOT combine like 'sq1+sq2'.")
    round: int = Field(ge=1, description="Execution round: 1=broad discovery, 2+=targeted follow-up refined by round 1 findings")


class StrategyOutput(BaseModel):
    approach: Literal["broad_first", "narrow_first", "targeted"] = Field(
        description="broad_first=wide then narrow, narrow_first=precise then expand, targeted=known-item"
    )
    search_terms: list[SearchTerm]
    fallback_plan: Optional[str] = Field(default=None, description="Fallback if primary searches fail")


class ToolPlanItem(BaseModel):
    sub_query_id: str
    tool: Literal["web_search", "web_fetch", "web_map"]
    reason: str
    params: Optional[dict] = Field(default=None, description="Tool-specific parameters")


class ExecutionOrderOutput(BaseModel):
    parallel: list[list[str]] = Field(description="Groups of sub-query IDs runnable in parallel")
    sequential: list[str] = Field(description="Sub-query IDs that must run in order")
    estimated_rounds: int = Field(ge=1)


PHASE_NAMES = [
    "intent_analysis",
    "complexity_assessment",
    "query_decomposition",
    "search_strategy",
    "tool_selection",
    "execution_order",
]

REQUIRED_PHASES: dict[int, set[str]] = {
    1: {"intent_analysis", "complexity_assessment", "query_decomposition"},
    2: {"intent_analysis", "complexity_assessment", "query_decomposition", "search_strategy"},
    3: {"intent_analysis", "complexity_assessment", "query_decomposition", "search_strategy", "execution_order"},
}

_ACCUMULATIVE_LIST_PHASES = {"query_decomposition", "tool_selection"}
_MERGE_STRATEGY_PHASE = "search_strategy"


def _split_csv(value: str) -> list[str]:
    return [s.strip() for s in value.split(",") if s.strip()] if value else []


class PhaseRecord(BaseModel):
    phase: str
    thought: str
    data: dict | list | None = None
    confidence: float = 1.0


class PlanningSession:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.phases: dict[str, PhaseRecord] = {}
        self.complexity_level: int | None = None

    @property
    def completed_phases(self) -> list[str]:
        return [p for p in PHASE_NAMES if p in self.phases]

    def required_phases(self) -> set[str]:
        return REQUIRED_PHASES.get(self.complexity_level or 3, REQUIRED_PHASES[3])

    def is_complete(self) -> bool:
        if self.complexity_level is None:
            return False
        return self.required_phases().issubset(self.phases.keys())

    def build_executable_plan(self) -> dict:
        plan = {name: record.data for name, record in self.phases.items()}
        # Auto-fill tool_selection: default unmapped sub-queries to web_search
        if "tool_selection" not in plan and "query_decomposition" in plan:
            sq_data = plan["query_decomposition"]
            if isinstance(sq_data, list):
                plan["tool_selection"] = [
                    {"sub_query_id": item["id"], "tool": "web_search", "reason": "default"}
                    for item in sq_data
                ]
        return plan


class PlanningEngine:
    def __init__(self):
        self._sessions: dict[str, PlanningSession] = {}

    def get_session(self, session_id: str) -> PlanningSession | None:
        return self._sessions.get(session_id)

    def process_phase(
        self,
        phase: str,
        thought: str,
        session_id: str = "",
        is_revision: bool = False,
        revises_phase: str = "",
        confidence: float = 1.0,
        phase_data: dict | list | None = None,
    ) -> dict:
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
        else:
            sid = session_id if session_id else uuid.uuid4().hex[:12]
            session = PlanningSession(sid)
            self._sessions[sid] = session

        target = revises_phase if is_revision and revises_phase else phase
        if target not in PHASE_NAMES:
            return {"error": f"Unknown phase: {target}. Valid: {', '.join(PHASE_NAMES)}"}

        if target in _ACCUMULATIVE_LIST_PHASES:
            if is_revision:
                session.phases[target] = PhaseRecord(
                    phase=target, thought=thought,
                    data=[phase_data] if not isinstance(phase_data, list) else phase_data,
                    confidence=confidence,
                )
            elif target in session.phases and isinstance(session.phases[target].data, list):
                session.phases[target].data.append(phase_data)
                session.phases[target].thought = thought
                session.phases[target].confidence = confidence
            else:
                session.phases[target] = PhaseRecord(
                    phase=target, thought=thought, data=[phase_data], confidence=confidence,
                )
        elif target == _MERGE_STRATEGY_PHASE:
            existing = session.phases.get(target)
            if is_revision:
                session.phases[target] = PhaseRecord(
                    phase=target, thought=thought, data=phase_data, confidence=confidence,
                )
            elif existing and isinstance(existing.data, dict) and isinstance(phase_data, dict):
                existing.data.setdefault("search_terms", []).extend(phase_data.get("search_terms", []))
                if phase_data.get("approach"):
                    existing.data["approach"] = phase_data["approach"]
                if phase_data.get("fallback_plan"):
                    existing.data["fallback_plan"] = phase_data["fallback_plan"]
                existing.thought = thought
                existing.confidence = confidence
            else:
                session.phases[target] = PhaseRecord(
                    phase=target, thought=thought, data=phase_data, confidence=confidence,
                )
        else:
            session.phases[target] = PhaseRecord(
                phase=target, thought=thought, data=phase_data, confidence=confidence,
            )

        if target == "complexity_assessment" and isinstance(phase_data, dict):
            level = phase_data.get("level")
            if level in (1, 2, 3):
                session.complexity_level = level

        complete = session.is_complete()
        result: dict = {
            "session_id": session.session_id,
            "completed_phases": session.completed_phases,
            "complexity_level": session.complexity_level,
            "plan_complete": complete,
        }

        remaining = [p for p in PHASE_NAMES if p in session.required_phases() and p not in session.phases]
        if remaining:
            result["phases_remaining"] = remaining

        if complete:
            result["executable_plan"] = session.build_executable_plan()

        return result


engine = PlanningEngine()


# ── Decontamination Pipeline ────────────────────────────────────────────

DECON_PHASE_NAMES = [
    "decon_assess",
    "decon_verify",
    "decon_provenance",
    "decon_motive",
    "decon_synthesis",
    "decon_patterns",
]

DECON_ORDER: dict[str, int] = {name: i for i, name in enumerate(DECON_PHASE_NAMES)}


class DeconSession:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.phases: dict[str, PhaseRecord] = {}
        self.contamination_level: str | None = None
        self.contamination_dimensions: list[str] = []
        self.decon_complete: bool = False

    @property
    def completed_phases(self) -> list[str]:
        return [p for p in DECON_PHASE_NAMES if p in self.phases]

    def phases_remaining(self) -> list[str]:
        done = set(self.phases.keys())
        if "decon_assess" not in done:
            return []
        last_done = max((DECON_ORDER[p] for p in done if p in DECON_ORDER), default=-1)
        return [p for p in DECON_PHASE_NAMES if DECON_ORDER[p] > last_done]

    def build_summary(self) -> dict:
        summary = {
            "decon_complete": self.decon_complete,
            "contamination_level": self.contamination_level,
            "contamination_dimensions": self.contamination_dimensions,
        }
        corrections = []
        for name in self.completed_phases:
            rec = self.phases[name]
            if isinstance(rec.data, dict):
                data = rec.data
                if "corrected_search_directions" in data and data["corrected_search_directions"]:
                    summary["corrected_search_directions"] = data["corrected_search_directions"]
                if "source_reliability" in data and data["source_reliability"]:
                    summary["source_reliability"] = data["source_reliability"]
                if "contamination_summary" in data and data["contamination_summary"]:
                    summary["contamination_summary"] = data["contamination_summary"]
        return summary


class DecontaminationEngine:
    def __init__(self):
        self._sessions: dict[str, DeconSession] = {}

    def get_session(self, session_id: str) -> DeconSession | None:
        return self._sessions.get(session_id)

    def process_phase(
        self,
        phase: str,
        thought: str,
        session_id: str,
        phase_data: dict | None = None,
        confidence: float = 1.0,
        is_revision: bool = False,
    ) -> dict:
        if phase not in DECON_PHASE_NAMES:
            return {"error": f"Unknown decon phase: {phase}. Valid: {', '.join(DECON_PHASE_NAMES)}"}

        if session_id in self._sessions:
            session = self._sessions[session_id]
        else:
            session = DeconSession(session_id)
            self._sessions[session_id] = session

        if is_revision or phase not in session.phases:
            session.phases[phase] = PhaseRecord(
                phase=phase, thought=thought, data=phase_data or {}, confidence=confidence,
            )
        else:
            existing = session.phases[phase]
            if isinstance(existing.data, dict) and isinstance(phase_data, dict):
                existing.data.update(phase_data)
            else:
                session.phases[phase] = PhaseRecord(
                    phase=phase, thought=thought, data=phase_data or {}, confidence=confidence,
                )
            existing.thought = thought
            existing.confidence = confidence

        if phase == "decon_assess" and phase_data:
            session.contamination_level = phase_data.get("contamination_level")
            session.contamination_dimensions = phase_data.get("contamination_dimensions", [])
            if session.contamination_level in (None, "low"):
                session.decon_complete = True
                # low suspicion: auto-mark remaining decon phases as skipped
                for p in DECON_PHASE_NAMES:
                    if p not in session.phases and p != "decon_assess":
                        session.phases[p] = PhaseRecord(
                            phase=p, thought="skipped: contamination level low",
                            data={"skipped": True, "reason": "low contamination suspicion"},
                        )
                session.decon_complete = True

        if phase == "decon_synthesis":
            session.decon_complete = True

        last_done = max((DECON_ORDER[p] for p in session.phases if p in DECON_ORDER), default=-1)
        result: dict = {
            "session_id": session.session_id,
            "decon_complete": session.decon_complete,
            "completed_phases": session.completed_phases,
            "contamination_level": session.contamination_level,
            "contamination_dimensions": session.contamination_dimensions,
        }

        remaining = session.phases_remaining()
        if remaining:
            result["phases_remaining"] = remaining

        if session.decon_complete:
            result["decon_summary"] = session.build_summary()

        result["can_exit"] = last_done >= 0
        return result


decon_engine = DecontaminationEngine()
