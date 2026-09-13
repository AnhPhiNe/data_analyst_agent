# Tabular Analytics Agent

This context defines the shared product language for an AI-assisted workspace that turns user-supplied tabular data into traceable analysis and interactive analytical outputs.

## Language

**Analysis Session**:
A bounded interaction in which a user supplies one logical dataset, states analytical goals, and collaborates with the agent on results.
_Avoid_: Chat, conversation, job

**Orchestrator Agent**:
The single decision-making participant that proposes Analysis Plans, selects permitted Tool Actions, evaluates returned results, and communicates with the user.
_Avoid_: Agent swarm, autonomous script

**Supported Dataset**:
A rectangular, header-based table containing numeric, categorical, boolean, short-text, or date/time fields and supplied as CSV or XLSX within the accepted size limit.
_Avoid_: Any data, arbitrary file

**Source Dataset**:
The immutable, byte-for-byte copy of a Supported Dataset exactly as supplied by the user.
_Avoid_: Working file, cleaned data

**Model Gateway**:
The provider-neutral boundary that requests validated structured model output and records model
usage without exposing provider-specific objects to orchestration.
_Avoid_: Gemini client, LLM singleton

**Agent Run**:
A checkpointed execution of an Analytical Goal within an Analysis Session; it may pause for user
confirmation and resume from its last safe state.
_Avoid_: Hidden chain of thought, background script

**Working Dataset**:
A session-scoped analytical view derived from the Source Dataset through recorded transformations.
_Avoid_: Source file, modified original

**Data Profile**:
A factual summary of a Supported Dataset's structure, field types, completeness, uniqueness, distributions, and quality risks.
_Avoid_: Insight, report

**Analytical Goal**:
The business or exploratory question that an Analysis Session is intended to answer.
_Avoid_: Prompt, command

**Driver Exploration**:
An Analytical Goal that examines which observed fields have meaningful statistical associations with an outcome without asserting causation.
_Avoid_: Root-cause analysis, causal analysis, feature importance

**Semantic Annotation**:
A user-confirmed meaning, role, unit, or interpretation attached to a dataset field for the duration of an Analysis Session.
_Avoid_: Inferred fact, column description guess

**Analysis Plan**:
A user-visible sequence of analytical steps proposed by the agent to address an Analytical Goal.
_Avoid_: Chain of thought, hidden reasoning

**Tool Action**:
A recorded, reproducible computation performed against session data to produce evidence for an analytical result.
_Avoid_: Reasoning step, guess

**Verification Gate**:
A deterministic check that analytical evidence must pass before its conclusion can be presented as a Verified Insight.
_Avoid_: Agent confidence, self-review

**Verified Insight**:
A natural-language analytical conclusion backed by computed evidence, provenance, relevant caveats, and a reproducible Tool Action.
_Avoid_: Observation, AI opinion, generated insight

**Statistical Association**:
A measured relationship between observed variables that does not, by itself, establish that one variable causes another.
_Avoid_: Cause, causal driver

**Unsupported Claim**:
A proposed conclusion whose required data, semantics, statistical support, or successful verification is absent.
_Avoid_: Verified Insight, weak insight

**Analytical Artifact**:
A reusable output of an Analysis Session, such as a result table, chart, dashboard, or exported report.
_Avoid_: Response, message

**Candidate Artifact**:
An Analytical Artifact proposed by the agent but not yet selected for inclusion in a Dashboard.
_Avoid_: Final chart, dashboard item

**Pinned Artifact**:
A Candidate Artifact that the user has selected for inclusion in a Dashboard.
_Avoid_: Suggested chart, automatic chart

**Dashboard**:
An interactive collection of selected Analytical Artifacts organized around one or more related Analytical Goals.
_Avoid_: Chart collection, auto-generated report

**Bounded Autonomy**:
The operating rule under which the agent may independently perform reversible, session-scoped analysis while requiring approval for destructive or externally consequential actions.
_Avoid_: Full autonomy, unrestricted agent

**Evidence Trail**:
The ordered record connecting a Verified Insight to its source fields, filters, computations, results, and caveats.
_Avoid_: Chain of thought, explanation only
