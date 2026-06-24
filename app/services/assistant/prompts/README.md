# Assistant system prompts

Versioned system prompts used by the conversational assistant. `SystemPrompt`
loads these resources by version so prompt changes remain explicit and
testable. Do not embed credentials, runtime state, or tool results here.

When adding a version, keep the previous file for reproducibility, update the
loader's supported versions, and add coverage in
[`../tests/test_assistant_prompts.py`](../tests/test_assistant_prompts.py).
