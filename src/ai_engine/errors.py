class MultiAgentError(Exception):
    """Base error for the step-3 multi-agent analyst."""


class AgentInputError(MultiAgentError):
    """Raised when data from previous pipeline steps is malformed."""


class AgentOutputError(MultiAgentError):
    """Raised when an LLM agent cannot produce the expected schema."""


class LLMClientError(MultiAgentError):
    """Raised when the configured LLM endpoint fails."""
