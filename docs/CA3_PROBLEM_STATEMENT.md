# CA-3 Problem Statement and Domain Choice

## Problem

Software teams need quick feedback when a Python change introduces a defect. Manual testing takes time, while writing useful edge-case tests requires a developer to understand both the code and the intended behavior. A test generator can add breadth, but generated tests must distinguish explicit expectations from heuristics and must not report an unexecuted case as passing.

## Chosen domain: software quality assurance

TestPilot AI is an agentic testing and bug-detection platform for Python modules. The domain fits an agent-based system because a useful run naturally separates into planning, static analysis, test design, execution, evidence review, optional repair, and reporting. Each stage has a clear input/output boundary and can record its evidence. The domain also makes safety visible: submitted code must run in an isolation mode, and results need to say whether execution really occurred.

## Existing challenge and proposed system

The repository began as a Streamlit application with a multi-agent orchestration core. Its challenge is to find functional bugs and edge-case failures while keeping the user's original source untouched. This CA-3 version exposes the existing workflow through a React client and FastAPI service, with SQLite persistence and a bounded agent orchestrator. It adds a contract-driven API, browser routes for the application pages, endpoint and browser verification, and documentation suitable for a live demonstration.

The deterministic test engine uses requirements and doctests as confirmed expected-value oracles. Other generated cases provide heuristic checks such as no-crash, return type, determinism, and invalid-input behavior. A deterministic patch is kept only when it parses and the same executed tests demonstrate improvement without breaking a previously passing test. AI repair is optional and was disabled in the measured sample evaluation.

## Intended benefit and limits

The intended benefit is a repeatable path from Python source to inspectable test evidence, candidate fix, and exportable report. The three bundled samples below demonstrate how measured behavior can improve on known planted defects. They do not establish general bug-detection accuracy: the set is small, inputs are sample-specific, several generated failures are heuristic, and execution used local trusted mode. Passing tests do not prove code is bug-free.

## Evaluation evidence

See [CA-3 validation results](CA3_VALIDATION_RESULTS.md) and the machine-readable [results JSON](ca3_validation_results.json). The evaluation invokes the real `Orchestrator.start` path with the bundled sample requirements, deterministic provider, and local trusted execution in an isolated temporary database. Counts are taken from those runs; no expected outcome was inferred from the README.
