FROM python:3.12-slim AS base

# Security: non-root user
RUN groupadd -r trader && useradd -r -g trader -d /app -s /sbin/nologin trader

WORKDIR /app

# Install uv for fast dependency resolution
# Pin uv version — do not use :latest to prevent supply chain attacks
COPY --from=ghcr.io/astral-sh/uv:0.6.12 /uv /usr/local/bin/uv

# Copy dependency files first (cache layer)
COPY pyproject.toml uv.lock ./

# Install dependencies in isolated venv
RUN uv sync --frozen --no-dev

# Copy application code (harness + hardened runner + validator)
COPY prepare.py backtest.py backtest_sandboxed.py validate_data.py ./
COPY run_benchmarks.py generate_charts.py export_equity.py export_milestones.py ./
COPY benchmarks/ benchmarks/

# Strategy gets mounted at runtime (mutable)
# Data cache gets mounted read-only at runtime

# Drop to non-root
USER trader

# Health check — can we import the harness?
HEALTHCHECK --interval=30s --timeout=5s \
    CMD python -c "from prepare import load_data, compute_score; print('ok')" || exit 1

ENTRYPOINT ["uv", "run", "python"]
CMD ["backtest_sandboxed.py"]
