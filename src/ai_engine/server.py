from __future__ import annotations

from fastapi import FastAPI

from src.ai_engine.api_models import Step3AnalysisRequest, Step3AnalysisResponse
from src.ai_engine.config import AgentConfigError
from src.ai_engine.errors import MultiAgentError
from src.ai_engine.pipeline import MultiAgentAnalyst, build_default_pipeline


def create_app(pipeline: MultiAgentAnalyst | None = None) -> FastAPI:
    app = FastAPI(
        title="Chess Multi-Agent Analyst",
        version="0.1.0",
        description="Step-3 AI analyst server for chess game explanations.",
    )
    app.state.pipeline = pipeline

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/multi-agent-analysis", response_model=Step3AnalysisResponse)
    def analyze_game(request: Step3AnalysisRequest) -> Step3AnalysisResponse:
        try:
            analyst = app.state.pipeline or build_default_pipeline()
            report = analyst.run(request.to_context())
            return Step3AnalysisResponse(
                success=True,
                data=report.to_api_data(include_debug=request.include_debug),
            )
        except (AgentConfigError, MultiAgentError, ValueError) as exc:
            return Step3AnalysisResponse(success=False, error=str(exc))

    return app


app = create_app()
